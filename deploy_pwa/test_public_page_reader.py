"""No live network: verify the public-only boundary and source evidence contract."""
import hashlib
import ipaddress
import socket
import subprocess
import time
import unittest
from unittest.mock import patch

from deploy_pwa import public_page_reader as reader


class PublicPageReaderTest(unittest.TestCase):
    def test_private_and_non_https_urls_never_reach_network(self):
        bad = ["http://example.com", "https://localhost/private",
               "https://metadata.google.internal/x", "https://127.0.0.1/x",
               "https://user:pass@example.com/", "https://example.com:8443/",
               "https://example.com\\@localhost/", "https://example.com/#secret",
               "https://example.com/\nHost:evil.test", "https://example.com/\ud800"]
        with patch.object(reader.subprocess, "run") as child:
            for url in bad:
                with self.subTest(url=url), self.assertRaises(reader.PageReadError):
                    reader.read_public_page(url)
            child.assert_not_called()

    def test_mixed_private_dns_reply_blocks_before_request(self):
        addresses = ["93.184.215.14", "8.8.8.8", "127.0.0.1"]
        with patch.object(reader, "_resolve_addresses", return_value=addresses), \
             patch.object(reader, "_request") as request:
            with self.assertRaisesRegex(reader.PageReadError, "内网"):
                reader._read_public_page_impl("https://example.com/")
            request.assert_not_called()

    def test_public_dns_candidates_are_capped_after_full_validation(self):
        addresses = ["93.184.215.14", "8.8.8.8", "1.1.1.1"]
        with patch.object(reader, "_resolve_addresses", return_value=addresses):
            selected = reader._public_addresses("example.com", time.monotonic() + 5)
        self.assertEqual([str(ip) for ip in selected], addresses[:2])

    def test_unicode_path_and_query_are_percent_encoded(self):
        host, canonical = reader._validated_url("https://example.com/上海/你好?q=记忆&v=%E4%B8%AD")
        self.assertEqual(host, "example.com")
        self.assertEqual(canonical,
                         "https://example.com/%E4%B8%8A%E6%B5%B7/%E4%BD%A0%E5%A5%BD"
                         "?q=%E8%AE%B0%E5%BF%86&v=%E4%B8%AD")
        self.assertTrue(canonical.isascii())

    def test_no_redirect_follow_and_limited_source_record(self):
        raw = b"<html><script>ignore()</script><p>Hello &amp; welcome.</p></html>"
        with patch.object(reader, "_public_addresses", return_value=[ipaddress.ip_address("93.184.215.14")]), \
             patch.object(reader, "_request", return_value=(raw, "text/html")) as request:
            result = reader._read_public_page_impl("https://Example.COM:443/a?q=1")
        self.assertEqual(request.call_args.args[:2], ("example.com", "/a?q=1"))
        self.assertEqual(result["url"], "https://example.com/a?q=1")
        self.assertEqual(result["text"], "Hello & welcome.")
        self.assertEqual(result["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(result["source_kind"], "public_web_untrusted")
        self.assertTrue(result["fetched_at"].endswith("+00:00"))

    def test_response_rejects_redirect_and_oversized_body(self):
        class Response:
            status = 302
            def getheader(self, name, default=None):
                return {"Location": "http://127.0.0.1/private"}.get(name, default)
            def read(self, amount):
                raise AssertionError("redirect body must not be read")

        class Connection:
            def __init__(self, *args, **kwargs):
                self.sock = None
            def request(self, method, path, headers):
                self.headers = headers
            def getresponse(self):
                return Response()
            def close(self):
                pass

        with patch.object(reader.ssl, "create_default_context") as tls, \
             patch.object(reader.socket, "create_connection"), \
             patch.object(reader.http.client, "HTTPSConnection", Connection):
            with self.assertRaisesRegex(reader.PageReadError, "未返回"):
                reader._request("example.com", "/", [ipaddress.ip_address("93.184.215.14")], time.monotonic() + 5)
            tls.return_value.wrap_socket.assert_called_once()

        Response.status = 200
        Response.getheader = lambda self, name, default=None: {
            "Content-Type": "text/html", "Content-Length": str(reader.MAX_BYTES + 1)
        }.get(name, default)
        with patch.object(reader.ssl, "create_default_context"), \
             patch.object(reader.socket, "create_connection"), \
             patch.object(reader.http.client, "HTTPSConnection", Connection):
            with self.assertRaisesRegex(reader.PageReadError, "过大"):
                reader._request("example.com", "/", [ipaddress.ip_address("93.184.215.14")], time.monotonic() + 5)

    def test_subprocess_timeout_is_killable_and_no_result_is_returned(self):
        with patch.object(reader.subprocess, "run", side_effect=subprocess.TimeoutExpired("resolver", 0.01)) as run, \
             patch.object(reader, "_request") as request:
            with self.assertRaisesRegex(reader.PageReadError, "超时"):
                reader.read_public_page("https://example.com/")
            self.assertEqual(run.call_args.kwargs["timeout"], reader.TOTAL_TIMEOUT)
            request.assert_not_called()

    def test_public_entrypoint_uses_isolated_child_and_returns_sourced_result(self):
        payload = '{"result":{"url":"https://example.com/","text":"Hello","source_kind":"public_web_untrusted"}}'
        completed = subprocess.CompletedProcess([], 0, payload, "")
        with patch.object(reader.subprocess, "run", return_value=completed) as run:
            result = reader.read_public_page("https://example.com/")
        self.assertEqual(result["text"], "Hello")
        self.assertEqual(run.call_args.args[0][1], "-I")
        self.assertEqual(run.call_args.kwargs["input"], "https://example.com/")

    def test_child_runner_imports_module_without_network(self):
        # The child can load this file under -I; validation stops before DNS.
        result = subprocess.run([reader.sys.executable, "-I", "-c", reader._CHILD_RUNNER,
                                 reader.os.path.abspath(reader.__file__)],
                                input="http://example.com/", text=True,
                                capture_output=True, timeout=2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("error", reader.json.loads(result.stdout))

    def test_deadline_applies_across_address_fallbacks(self):
        address = ipaddress.ip_address("93.184.215.14")
        with patch.object(reader.ssl, "create_default_context"), \
             patch.object(reader.socket, "create_connection", side_effect=OSError("unreachable")) as connect, \
             patch.object(reader, "_remaining", side_effect=[0.1, 0.1, reader.PageReadError("网页读取超时")]):
            with self.assertRaisesRegex(reader.PageReadError, "超时"):
                reader._request("example.com", "/", [address, address], time.monotonic() + 5)
            connect.assert_called_once()

    def test_dns_reply_rejects_non_public_and_malformed_addresses(self):
        for addresses in (["93.184.215.14", "::1"], ["not-an-ip"]):
            with self.subTest(addresses=addresses), \
                 patch.object(reader, "_resolve_addresses", return_value=addresses), \
                 self.assertRaises(reader.PageReadError):
                reader._public_addresses("example.com", time.monotonic() + 5)


if __name__ == "__main__":
    unittest.main()
