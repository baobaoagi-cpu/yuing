"""The menu must reflect real server capability and expose no credentials."""
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("POC_PREVIEW_USER", "test-user")
os.environ.setdefault("POC_PREVIEW_PASSWORD", "test-password")
os.environ.setdefault("POC_SESSION_SECRET", "test-secret-value-with-more-than-thirty-two-chars")

from deploy_pwa import server  # noqa: E402


class ToolCatalogTest(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()
        self.base = "https://example.test"
        self.headers = {"Origin": self.base}
        reply = self.client.post("/api/login", base_url=self.base,
                                 headers=self.headers,
                                 json={"username": "test-user", "password": "test-password"})
        self.assertEqual(reply.status_code, 200)

    def test_catalog_is_authenticated_and_candidates_are_inert(self):
        anonymous = server.app.test_client()
        self.assertEqual(anonymous.get("/api/v2/tools/catalog", base_url=self.base).status_code, 401)
        response = self.client.get("/api/v2/tools/catalog", base_url=self.base)
        self.assertEqual(response.status_code, 200)
        groups = response.json["groups"]
        names = {item["name"] for group in groups for item in group["items"]}
        self.assertEqual(len(names), 12)
        self.assertIn("和风天气", names)
        self.assertIn("腾讯会议", names)
        self.assertIn("时区换算", names)
        candidates = groups[1]["items"] + groups[2]["items"]
        self.assertTrue(all(item["state"] != "ready" and "href" not in item
                            for item in candidates))
        self.assertNotIn("API_KEY", response.get_data(as_text=True))
        page = self.client.get("/chat", base_url=self.base)
        self.assertIn(b'id="tool-menu-button"', page.data)
        page.close()

    def test_page_reader_only_gets_action_when_enabled_and_storage_exists(self):
        with patch.dict(os.environ, {"POC_PUBLIC_WEB_READ_ENABLED": ""}):
            item = self.client.get("/api/v2/tools/catalog", base_url=self.base).json["groups"][0]["items"][1]
            self.assertEqual(item["state"], "waiting")
            self.assertNotIn("href", item)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "POC_PUBLIC_WEB_READ_ENABLED": "1",
            "POC_CHAT_DB_PATH": os.path.join(directory, "chat.sqlite3"),
        }):
            item = self.client.get("/api/v2/tools/catalog", base_url=self.base).json["groups"][0]["items"][1]
            self.assertEqual(item["state"], "ready")
            self.assertEqual(item["href"], "/chat")


if __name__ == "__main__":
    unittest.main()
