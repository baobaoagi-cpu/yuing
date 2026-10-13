"""Small password gate for the Railway sensory preview.

This is an internal preview gate, not the future multi-user account system.
No user records, private memory, microphone audio or model requests are stored.
"""
from __future__ import annotations

import base64
import hmac
import io
import json
import os
import re
import zipfile
from email.parser import BytesParser
from email import policy
from xml.etree import ElementTree as ET
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
USERNAME = os.environ.get("POC_PREVIEW_USER", "")
PASSWORD = os.environ.get("POC_PREVIEW_PASSWORD", "")
BRIDGE = os.environ.get("POC_LOCAL_BRIDGE_URL", "").rstrip("/")
_bridge_url = urlparse(BRIDGE)
if BRIDGE and (_bridge_url.scheme != "http" or _bridge_url.hostname not in {"127.0.0.1", "localhost"}):
    raise RuntimeError("POC_LOCAL_BRIDGE_URL must be a loopback HTTP URL")
MAX_JSON = 16_384
MAX_AUDIO = 2_000_000
MAX_FILE = 5_000_000


def extract_document(filename, data):
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise ValueError("PDF 格式不正確")
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise ValueError("加密 PDF 尚不支援")
        if len(reader.pages) > 20:
            raise ValueError("PDF 最多 20 頁")
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
        kind = "PDF"
    elif ext in {".docx", ".pptx"}:
        kind = "Word" if ext == ".docx" else "PowerPoint"
        parts = []
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            if len(names) > 1000 or sum(x.file_size for x in archive.infolist()) > 20_000_000:
                raise ValueError("檔案內容過大")
            if ext == ".docx":
                targets = [n for n in names if re.fullmatch(r"word/(document|header\d+|footer\d+)\.xml", n)]
            else:
                targets = sorted((n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                                 key=lambda n: int(re.search(r"slide(\d+)", n).group(1)))
            for name in targets[:100]:
                raw = archive.read(name)
                if len(raw) > 2_000_000:
                    raise ValueError("單頁內容過大")
                root = ET.fromstring(raw)
                parts.append(" ".join(node.text.strip() for node in root.iter()
                                      if node.tag in {"{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t",
                                                      "{http://schemas.openxmlformats.org/drawingml/2006/main}t"}
                                      and node.text and node.text.strip()))
        text = "\n".join(parts)
    else:
        raise ValueError("僅支援 PDF、DOCX、PPTX")
    clean = re.sub(r"[ \t]+", " ", text).strip()
    if not clean:
        return {"kind": kind, "summary": "未抽出文字；掃描圖片或圖表內容尚未支援。", "characters": 0}
    preview = clean[:2500]
    if len(clean) > 2500:
        preview += "…（僅顯示前 2500 字）"
    return {"kind": kind, "summary": preview, "characters": len(clean),
            "note": "這是文字抽取預覽，尚未經模型理解或摘要。"}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def _authorized(self):
        if not USERNAME or not PASSWORD:
            self.send_error(503, "Preview credentials not configured")
            return False
        try:
            scheme, value = self.headers.get("Authorization", "").split(" ", 1)
            if scheme.lower() != "basic":
                raise ValueError
            received = base64.b64decode(value, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            received = ""
        expected = f"{USERNAME}:{PASSWORD}"
        if not hmac.compare_digest(received, expected):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Duanni internal POC"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False
        return True

    def _json(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _bridge(self, path, body=None, content_type=None):
        headers = {"Host": _bridge_url.netloc, "Origin": BRIDGE}
        if content_type:
            headers["Content-Type"] = content_type
        request = Request(BRIDGE + path, data=body, headers=headers,
                          method="POST" if body is not None else "GET")
        try:
            with urlopen(request, timeout=90) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            try:
                return exc.code, json.load(exc)
            except (ValueError, UnicodeDecodeError):
                return exc.code, {"error": "本機橋接服務失敗"}
        except (URLError, TimeoutError):
            return 503, {"error": "本機橋接服務無法連線"}

    def do_GET(self):
        if self.path == "/healthz":
            self.send_response(200 if USERNAME and PASSWORD else 503)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if not self._authorized():
            return
        if self.path == "/api/capabilities":
            ready = False
            if BRIDGE:
                status, payload = self._bridge("/api/status")
                ready = status == 200 and bool(payload.get("ready"))
            return self._json(200, {
                "text": ready, "turn_voice": ready, "live_voice": False,
                "image": False, "files": True, "sound_events": True,
                "file_mode": "PDF/DOCX/PPTX 文字抽取預覽",
                "mode": "local_bridge" if BRIDGE else "railway_preview",
                "reason": "本機 Hermes/Whisper 已連線" if ready else "雲端尚未接入對話模型；生活聲辨識在手機端執行",
            })
        return super().do_GET()

    def do_POST(self):
        if not self._authorized():
            return
        origin = self.headers.get("Origin")
        expected = f"https://{self.headers.get('Host')}" if os.environ.get("RAILWAY_ENVIRONMENT_ID") else f"http://{self.headers.get('Host')}"
        if origin and origin != expected:
            return self._json(403, {"error": "來源不符"})
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].lower()
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if self.path == "/api/analyze":
            if content_type != "multipart/form-data" or not 0 < length <= MAX_FILE + 10_000:
                return self._json(413, {"error": "請上傳 5 MB 內的 PDF、DOCX 或 PPTX"})
            try:
                raw = self.rfile.read(length)
                header = self.headers.get("Content-Type", "")
                message = BytesParser(policy=policy.default).parsebytes(
                    b"MIME-Version: 1.0\r\nContent-Type: " + header.encode("ascii") + b"\r\n\r\n" + raw)
                parts = list(message.iter_parts())
                if len(parts) != 1:
                    raise ValueError("請一次上傳一個檔案")
                part = parts[0]
                filename = part.get_filename() or ""
                data = part.get_payload(decode=True) or b""
                if not 0 < len(data) <= MAX_FILE:
                    raise ValueError("檔案大小需在 5 MB 內")
                return self._json(200, extract_document(filename, data))
            except (ValueError, zipfile.BadZipFile, ET.ParseError, ImportError) as exc:
                return self._json(400, {"error": str(exc)})
            except Exception:
                return self._json(422, {"error": "無法抽取此檔案的文字"})
        if not BRIDGE:
            return self._json(503, {"error": "此 Railway 預覽尚未接入對話服務"})
        if self.path == "/api/chat" and content_type == "application/json" and 0 < length <= MAX_JSON:
            try:
                data = json.loads(self.rfile.read(length))
                text = str(data.get("message", ""))[:4000]
            except (ValueError, TypeError):
                return self._json(400, {"error": "文字格式不正確"})
            status, result = self._bridge("/api/chat", json.dumps({"text": text, "history": []}).encode(), "application/json")
            return self._json(status, result)
        if self.path == "/api/transcribe" and content_type == "audio/wav" and 44 <= length <= MAX_AUDIO:
            status, result = self._bridge("/api/transcribe", self.rfile.read(length), "audio/wav")
            return self._json(status, result)
        return self._json(415, {"error": "此功能或檔案格式尚未啟用"})

    def do_HEAD(self):
        if not self._authorized():
            return
        return super().do_HEAD()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
