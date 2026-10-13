"""Small password gate for the Railway sensory preview.

This is an internal preview gate, not the future multi-user account system.
No user records, private memory, microphone audio or model requests are stored.
"""
from __future__ import annotations

import base64
import hmac
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
USERNAME = os.environ.get("POC_PREVIEW_USER", "")
PASSWORD = os.environ.get("POC_PREVIEW_PASSWORD", "")


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

    def do_GET(self):
        if self.path == "/healthz":
            self.send_response(200 if USERNAME and PASSWORD else 503)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if not self._authorized():
            return
        return super().do_GET()

    def do_HEAD(self):
        if not self._authorized():
            return
        return super().do_HEAD()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
