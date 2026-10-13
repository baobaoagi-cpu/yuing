"""No live network: verify the public-only boundary and source evidence contract."""
import hashlib
import ipaddress
import socket
import unittest
from unittest.mock import patch

from deploy_pwa import public_page_reader as reader


class PublicPageReaderTest(unittest.TestCase):
    def test_private_and_non_https_urls_never_reach_network(self):
        bad = ["http://example.com", "https://localhost/private",
               "https://metadata.google.internal/x", "https://127.0.0.1/x",
               "https://user:pass@example.com/", "https://example.com:8443/",
               "https://example.com\\@localhost/", "https://example.com/#secret",
               "https://example.com/\nHost:evil.test"]
        with patch.object(reader.socket, "getaddrinfo") as dns:
            for url in bad:
                with self.subTest(url=url), self.assertRaises(reader.PageReadError):
                    reader.read_public_page(url)
            dns.assert_not_called()

    def test_mixed_private_dns_reply_blocks_before_request(self):
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443)),
                     (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        with patch.object(reader.socket, "getaddrinfo", return_value=addresses), \
             patch.object(reader, "_request") as request:
            with self.assertRaisesRegex(reader.PageReadError, "内网"):
                reader.read_public_page("https://example.com/")
            request.assert_not_called()

    def test_no_redirect_follow_and_limited_source_record(self):
        raw = b"<html><script>ignore()</script><p>Hello &amp; welcome.</p></html>"
        with patch.object(reader, "_public_addresses", return_value=[ipaddress.ip_address("93.184.215.14")]), \
             patch.object(reader, "_request", return_value=(raw, "text/html")) as request:
            result = reader.read_public_page("https://Example.COM:443/a?q=1")
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
                reader._request("example.com", "/", [ipaddress.ip_address("93.184.215.14")])
            tls.return_value.wrap_socket.assert_called_once()

        Response.status = 200
        Response.getheader = lambda self, name, default=None: {
            "Content-Type": "text/html", "Content-Length": str(reader.MAX_BYTES + 1)
        }.get(name, default)
        with patch.object(reader.ssl, "create_default_context"), \
             patch.object(reader.socket, "create_connection"), \
             patch.object(reader.http.client, "HTTPSConnection", Connection):
            with self.assertRaisesRegex(reader.PageReadError, "过大"):
                reader._request("example.com", "/", [ipaddress.ip_address("93.184.215.14")])


if __name__ == "__main__":
    unittest.main()
