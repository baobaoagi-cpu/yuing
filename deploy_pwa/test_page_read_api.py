"""Offline checks for opt-in, owner-scoped public page read artifacts."""
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("POC_PREVIEW_USER", "test-user")
os.environ.setdefault("POC_PREVIEW_PASSWORD", "test-password")
os.environ.setdefault("POC_SESSION_SECRET", "test-secret-value-with-more-than-thirty-two-chars")
os.environ.setdefault("MINIMAX_API_KEY", "test-key-not-sent")
from deploy_pwa import server  # noqa: E402
from deploy_pwa.public_page_reader import PageReadError  # noqa: E402


class PageReadApiTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        env = patch.dict(os.environ, {
            "POC_CHAT_DB_PATH": os.path.join(self.directory.name, "chat.sqlite3"),
            "POC_PUBLIC_WEB_READ_ENABLED": "1",
            "MINIMAX_API_KEY": "test-key-not-sent",
        })
        env.start()
        self.addCleanup(env.stop)
        self.client = server.app.test_client()
        self.base = "https://example.test"
        self.headers = {"Origin": self.base}

    def login(self, client=None, user="test-user", password="test-password"):
        client = client or self.client
        response = client.post("/api/login", base_url=self.base, headers=self.headers,
                               json={"username": user, "password": password})
        self.assertEqual(response.status_code, 200)

    def link_event(self, url="https://example.org/article"):
        with patch.object(server, "reply_to", return_value="收到链接。"):
            response = self.client.post("/api/v2/chat", base_url=self.base,
                                        headers=self.headers,
                                        json={"text": "请看这个", "url": url})
        self.assertEqual(response.status_code, 200)
        return response.json

    def read(self, event_id, client=None, headers=None):
        return (client or self.client).post(
            "/api/v2/page-read", base_url=self.base,
            headers=self.headers if headers is None else headers,
            json={"source_event_id": event_id},
        )

    @staticmethod
    def result():
        return {"url": "https://example.org/article", "fetched_at": "2026-10-14T00:00:00+00:00",
                "sha256": "a" * 64, "content_type": "text/html",
                "text": "来源网页内容。", "source_kind": "public_web_untrusted"}

    def test_disabled_by_default_and_auth_csrf(self):
        self.assertEqual(self.read("00000000-0000-0000-0000-000000000000").status_code, 401)
        self.login()
        event = self.link_event()["user_event"]["id"]
        with patch.dict(os.environ, {"POC_PUBLIC_WEB_READ_ENABLED": ""}), \
             patch.object(server, "read_public_page") as fetch:
            self.assertEqual(self.read(event).status_code, 503)
            self.assertEqual(self.client.get("/api/v2/page-read/" + event,
                                             base_url=self.base).status_code, 503)
            fetch.assert_not_called()
        self.assertEqual(self.read(event, headers={}).status_code, 403)

    def test_source_owner_and_url_validation(self):
        self.login()
        event = self.link_event()["user_event"]["id"]
        with patch.object(server, "read_public_page", return_value=self.result()):
            self.assertEqual(self.client.post("/api/v2/page-read", base_url=self.base,
                                              headers=self.headers,
                                              json={"source_event_id": event,
                                                    "url": "https://other.example/"}).status_code, 400)
            saved = self.read(event)
        self.assertEqual(saved.status_code, 201)
        artifact = saved.json["page_read"]
        self.assertEqual(artifact["status"], "read")
        self.assertEqual(artifact["url"], "https://example.org/article")
        self.assertEqual(artifact["sha256"], "a" * 64)
        sid = self.client.get("/api/v2/history", base_url=self.base).json["sessions"][0]["id"]
        history = self.client.get("/api/v2/history?session_id=" + sid,
                                  base_url=self.base).json["events"]
        self.assertEqual(history[0]["page_read"]["status"], "read")
        self.assertNotIn("text", history[0]["page_read"])
        self.assertEqual(self.client.get("/api/v2/page-read/" + artifact["id"],
                                         base_url=self.base).json["page_read"]["text"],
                         "来源网页内容。")
        with patch.object(server, "USERNAME", "friend"), \
             patch.object(server, "PASSWORD", "friend-password"):
            other = server.app.test_client()
            self.login(other, "friend", "friend-password")
            self.assertEqual(self.read(event, client=other).status_code, 404)
            self.assertEqual(other.get("/api/v2/page-read/" + artifact["id"],
                                       base_url=self.base).status_code, 404)

        plain = self.link_event("http://example.org/")["user_event"]["id"]
        self.assertEqual(self.read(plain).status_code, 400)
        with patch.object(server, "reply_to", return_value="收到。"):
            no_url = self.client.post("/api/v2/chat", base_url=self.base,
                                      headers=self.headers, json={"text": "没有网址"})
        self.assertEqual(self.read(no_url.json["user_event"]["id"]).status_code, 404)
        self.assertEqual(self.read(no_url.json["assistant_event"]["id"]).status_code, 404)

    def test_failed_reads_count_towards_six_attempt_quota_and_busy_rejects(self):
        self.login()
        event = self.link_event()["user_event"]["id"]
        server._page_read_lock.acquire()
        try:
            self.assertEqual(self.read(event).status_code, 429)
        finally:
            server._page_read_lock.release()
        with patch.object(server, "read_public_page", side_effect=PageReadError("失败")) as fetch:
            for remaining in range(5, -1, -1):
                response = self.read(event)
                self.assertEqual(response.status_code, 502)
                self.assertEqual(response.json["page_read"]["status"], "failed")
                self.assertEqual(response.json["remaining_today"], remaining)
            self.assertEqual(self.read(event).status_code, 429)
            self.assertEqual(fetch.call_count, 6)

    def test_unexpected_reader_failure_still_records_failed_artifact(self):
        self.login()
        event = self.link_event()["user_event"]["id"]
        with patch.object(server, "read_public_page", side_effect=UnicodeError("secret detail")):
            response = self.read(event)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json["page_read"]["status"], "failed")
        self.assertNotIn("secret detail", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
