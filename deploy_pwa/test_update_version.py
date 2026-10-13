"""Deployment version and refresh asset contract."""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("POC_PREVIEW_USER", "test-user")
os.environ.setdefault("POC_PREVIEW_PASSWORD", "test-password")
os.environ.setdefault("POC_SESSION_SECRET", "test-secret-value-with-more-than-thirty-two-chars")

from deploy_pwa.server import app  # noqa: E402


class UpdateVersionTest(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.base = "https://example.test"

    def login(self):
        response = self.client.post("/api/login", base_url=self.base,
                                    headers={"Origin": self.base},
                                    json={"username": "test-user", "password": "test-password"})
        self.assertEqual(response.status_code, 200)

    def test_version_requires_login(self):
        self.assertEqual(self.client.get("/api/v2/version", base_url=self.base).status_code, 401)
        self.assertEqual(self.client.get("/update.js", base_url=self.base).status_code, 303)

    def test_footer_shows_commit_and_refresh_uses_deployment_id(self):
        self.login()
        with patch.dict(os.environ, {
            "RAILWAY_DEPLOYMENT_ID": "12345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "RAILWAY_GIT_COMMIT_SHA": "a" * 40,
        }):
            response = self.client.get("/api/v2/version", base_url=self.base)
        self.assertEqual(response.json["version"], "aaaaaaa")
        self.assertEqual(response.json["cache_key"], "12345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        self.assertEqual(response.json["commit"], "aaaaaaaa")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        for path in ("/chat", "/chat.css", "/chat.js", "/update.js"):
            response = self.client.get(path, base_url=self.base)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            response.close()

    def test_unknown_version_is_not_invented(self):
        self.login()
        with patch.dict(os.environ, {"RAILWAY_DEPLOYMENT_ID": "invalid",
                                      "RAILWAY_GIT_COMMIT_SHA": "not-a-sha"}):
            response = self.client.get("/api/v2/version", base_url=self.base)
        self.assertIsNone(response.json["version"])
        self.assertIsNone(response.json["cache_key"])


if __name__ == "__main__":
    unittest.main()
