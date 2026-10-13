"""Bounded, opt-in public HTTPS page reader. Never fetches private network URLs.

Returned text is untrusted source material, not instructions for the agent.
No redirects or proxy environment variables are followed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import time
from urllib.parse import quote, urlsplit, urlunsplit

MAX_BYTES = 256 * 1024
MAX_TEXT = 12_000
TOTAL_TIMEOUT = 8
_ALLOWED_TYPES = {"text/html", "text/plain"}
_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.I)


class PageReadError(ValueError):
    pass


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "svg", "noscript", "template"}:
            self.skip += 1
        elif tag in {"p", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "svg", "noscript", "template"} and self.skip:
            self.skip -= 1
        elif tag in {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def _validated_url(url):
    if (not isinstance(url, str) or not 1 <= len(url) <= 2048 or
            any(ord(char) < 32 or ord(char) == 127 for char in url)):
        raise PageReadError("网址格式不正确")
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise PageReadError("网址格式不正确") from exc
    if (parts.scheme != "https" or not host or parts.username is not None or
            parts.password is not None or port not in (None, 443) or
            parts.fragment or "\\" in url):
        raise PageReadError("只读取公开 HTTPS 网页")
    if host.endswith("."):
        host = host[:-1]
    host = host.lower()
    labels = host.split(".")
    if (len(labels) < 2 or len(host) > 253 or
            any(not _HOST_LABEL.fullmatch(label) for label in labels) or
            labels[-1].isdigit() or host.endswith((".local", ".internal", ".localhost"))):
        raise PageReadError("网址主机名不正确")
    # HTTP request targets must be ASCII. Keep existing percent escapes and
    # URL delimiters, while encoding Chinese and other Unicode path text.
    try:
        path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
        query = quote(parts.query, safe="/?%:@!$&'()*+,;=-._~")
    except UnicodeError as exc:
        raise PageReadError("网址格式不正确") from exc
    # Omit fragment and explicit :443. Avoid smuggling a second authority.
    return host, urlunsplit(("https", host, path, query, ""))


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise PageReadError("网页读取超时")
    return remaining


def _resolve_addresses(host, deadline):
    _remaining(deadline)
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise PageReadError("网页域名暂时无法解析") from exc
    _remaining(deadline)
    if len(addresses) > 64:
        raise PageReadError("网页域名暂时无法解析")
    return [item[4][0] for item in addresses]


def _public_addresses(host, deadline):
    try:
        addresses = _resolve_addresses(host, deadline)
    except PageReadError:
        raise
    except (OSError, ValueError) as exc:
        raise PageReadError("网页域名暂时无法解析") from exc
    ips = []
    for endpoint in addresses:
        try:
            address = ipaddress.ip_address(endpoint)
        except ValueError as exc:
            raise PageReadError("网页域名暂时无法解析") from exc
        if not address.is_global:
            raise PageReadError("不读取内网地址")
        if address not in ips:
            ips.append(address)
    if not ips:
        raise PageReadError("网页域名暂时无法解析")
    # Check the *entire* DNS answer for private IPs first; then cap retries.
    return ips[:2]


def _request(host, path, addresses, deadline):
    context = ssl.create_default_context()
    last_error = None
    for address in addresses:
        connection = http.client.HTTPSConnection(host, 443, timeout=_remaining(deadline), context=context)
        raw = None
        try:
            # Connect to the DNS-validated address while preserving SNI/cert validation.
            raw = socket.create_connection((str(address), 443), timeout=_remaining(deadline))
            raw.settimeout(_remaining(deadline))
            connection.sock = context.wrap_socket(raw, server_hostname=host)
            raw = None  # TLS socket is now owned by the connection.
            connection.sock.settimeout(_remaining(deadline))
            connection.request("GET", path, headers={"Host": host,
                "User-Agent": "YulingPOC/1.0 (+public page reader)",
                "Accept": "text/html,text/plain", "Accept-Encoding": "identity"})
            connection.sock.settimeout(_remaining(deadline))
            response = connection.getresponse()
            if response.status != 200:
                raise PageReadError("网页未返回可读取的内容")
            content_type = response.getheader("Content-Type", "").split(";", 1)[0].lower().strip()
            if content_type not in _ALLOWED_TYPES:
                raise PageReadError("只读取 HTML 或纯文字网页")
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise PageReadError("网页压缩格式暂不支持")
            length = response.getheader("Content-Length")
            try:
                declared_length = int(length) if length else None
            except ValueError as exc:
                raise PageReadError("网页响应格式不正确") from exc
            if declared_length is not None and declared_length > MAX_BYTES:
                raise PageReadError("网页内容过大")
            chunks = []
            size = 0
            while size <= MAX_BYTES:
                connection.sock.settimeout(_remaining(deadline))
                # read1 does at most one socket read, so trickle responses
                # cannot repeatedly reset a full per-operation timeout.
                chunk = response.read1(min(8192, MAX_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            raw_content = b"".join(chunks)
            if len(raw_content) > MAX_BYTES:
                raise PageReadError("网页内容过大")
            return raw_content, content_type
        except PageReadError:
            raise
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            last_error = exc
        finally:
            if raw is not None:
                raw.close()
            connection.close()
    raise PageReadError("网页暂时无法连接") from last_error


def _read_public_page_impl(url):
    """Run within the short-lived reader process; never call on a server thread."""
    deadline = time.monotonic() + TOTAL_TIMEOUT
    host, canonical = _validated_url(url)
    addresses = _public_addresses(host, deadline)
    path = urlsplit(canonical).path
    if urlsplit(canonical).query:
        path += "?" + urlsplit(canonical).query
    raw, content_type = _request(host, path, addresses, deadline)
    decoded = raw.decode("utf-8", errors="replace")
    if content_type == "text/html":
        parser = _TextExtractor()
        parser.feed(decoded)
        decoded = " ".join(parser.parts)
    text = re.sub(r"[ \t]+", " ", decoded)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()[:MAX_TEXT]
    if not text:
        raise PageReadError("网页没有可读取的文字")
    return {"url": canonical, "fetched_at": datetime.now(timezone.utc).isoformat(),
            "sha256": hashlib.sha256(raw).hexdigest(), "content_type": content_type,
            "text": text, "source_kind": "public_web_untrusted"}


_CHILD_RUNNER = ("import importlib.util,json,sys\n"
                 "spec=importlib.util.spec_from_file_location('public_page_reader_child',sys.argv[1])\n"
                 "module=importlib.util.module_from_spec(spec)\n"
                 "spec.loader.exec_module(module)\n"
                 "try:\n"
                 " print(json.dumps({'result':module._read_public_page_impl(sys.stdin.read(2048))}))\n"
                 "except module.PageReadError as exc:\n"
                 " print(json.dumps({'error':str(exc)}))\n")


def read_public_page(url):
    """Explicit fetch with a killable, eight-second process boundary.

    The caller must enforce auth, quota and consent before invoking this.
    Neither a slow DNS lookup nor a trickling TLS response can outlive the
    subprocess timeout; subprocess.run kills and waits for its direct child.
    """
    _validated_url(url)
    try:
        result = subprocess.run([sys.executable, "-I", "-c", _CHILD_RUNNER,
                                 os.path.abspath(__file__)], input=url, text=True,
                                capture_output=True, timeout=TOTAL_TIMEOUT, check=False)
        if result.returncode != 0:
            raise PageReadError("网页暂时无法读取")
        record = json.loads(result.stdout)
        if not isinstance(record, dict):
            raise ValueError("invalid reader reply")
        if "error" in record:
            raise PageReadError(record["error"])
        if not isinstance(record.get("result"), dict):
            raise ValueError("invalid reader reply")
        return record["result"]
    except subprocess.TimeoutExpired as exc:
        raise PageReadError("网页读取超时") from exc
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise PageReadError("网页暂时无法读取") from exc
