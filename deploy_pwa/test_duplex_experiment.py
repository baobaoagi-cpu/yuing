"""The optional realtime offer relay must not expose its paid provider key."""

import os
import unittest
from unittest.mock import patch

from deploy_pwa import server


class _Answer:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self, _limit):
        return b"v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"


class DuplexExperimentTest(unittest.TestCase):
    def setUp(self):
        self.base = "https://example.test"
        self.client = server.app.test_client()
        server._duplex_offer_times.clear()
        self.patches = [
            patch.object(server, "USERNAME", "nicole"),
            patch.object(server, "PASSWORD", "synthetic-password"),
            patch.object(server, "SESSION_SECRET", "synthetic-session-key-at-least-32-characters"),
            patch.dict(os.environ, {"DASHSCOPE_API_KEY": "synthetic-provider-key",
                                 "DASHSCOPE_WORKSPACE_ID": "llm-test-workspace",
                                 "POC_DUPLEX_ENABLED": "1"}),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.offer = b"v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"

    def _send(self, origin=None, data=None):
        return self.client.post("/api/duplex/offer", base_url=self.base,
                                headers={"Origin": origin or self.base},
                                content_type="application/sdp",
                                data=self.offer if data is None else data)

    def _login(self):
        response = self.client.post("/api/login", base_url=self.base,
                                    headers={"Origin": self.base},
                                    json={"username": "nicole", "password": "synthetic-password"})
        self.assertEqual(response.status_code, 200)

    def test_login_and_origin_required(self):
        self.assertEqual(self._send().status_code, 401)
        self._login()
        self.assertEqual(self._send("https://other.test").status_code, 403)

    def test_disabled_and_malformed_offer(self):
        self._login()
        with patch.dict(os.environ, {"POC_DUPLEX_ENABLED": "0"}):
            self.assertEqual(self._send().status_code, 503)
        self.assertEqual(self._send(data=b"no audio").status_code, 400)

    def test_key_stays_server_side_and_url_is_pinned(self):
        self._login()
        with patch.object(server, "urlopen", return_value=_Answer()) as post:
            response = self._send()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, self.offer)
        upstream = post.call_args.args[0]
        self.assertEqual(upstream.full_url, "https://llm-test-workspace.cn-beijing.maas.aliyuncs.com/api/v1/webrtc/realtime?model=qwen3.8-omni-flash-realtime")
        self.assertEqual(upstream.data, self.offer)
        self.assertEqual(upstream.get_header("Authorization"), "Bearer synthetic-provider-key")
        self.assertNotIn(b"synthetic-provider-key", response.data)
        self.assertNotIn("synthetic-provider-key", self.client.get("/api/duplex/status", base_url=self.base).get_data(as_text=True))

    def test_paid_offer_retry_limit(self):
        self._login()
        with patch.object(server, "urlopen", return_value=_Answer()):
            responses = [self._send().status_code for _ in range(5)]
        self.assertEqual(responses, [200, 200, 200, 200, 429])


if __name__ == "__main__":
    unittest.main()
