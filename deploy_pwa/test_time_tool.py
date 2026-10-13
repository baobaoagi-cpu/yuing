"""Meaningful auth, input, and daylight-saving checks for the local tool."""
import os
import unittest

os.environ.setdefault("POC_PREVIEW_USER", "test-user")
os.environ.setdefault("POC_PREVIEW_PASSWORD", "test-password")
os.environ.setdefault("POC_SESSION_SECRET", "test-secret-value-with-more-than-thirty-two-chars")

from deploy_pwa import server  # noqa: E402
from deploy_pwa.time_tool import TimeToolError, convert_time  # noqa: E402


class TimeToolTest(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()
        self.base = "https://example.test"
        self.headers = {"Origin": self.base}

    def login(self):
        reply = self.client.post("/api/login", base_url=self.base,
                                 headers=self.headers,
                                 json={"username": "test-user", "password": "test-password"})
        self.assertEqual(reply.status_code, 200)

    def request_tool(self, **overrides):
        body = {"time": "2026-01-15T09:00", "source_timezone": "Asia/Shanghai",
                "target_timezone": "America/New_York"}
        body.update(overrides)
        return self.client.post("/api/v2/tools/time", base_url=self.base,
                                headers=self.headers, json=body)

    def test_auth_and_origin_are_required(self):
        self.assertEqual(self.client.get("/api/v2/tools/time", base_url=self.base).status_code, 401)
        self.assertEqual(self.request_tool().status_code, 401)
        self.login()
        page = self.client.get("/tools", base_url=self.base)
        self.assertEqual(page.status_code, 200)
        page.close()
        self.assertEqual(self.client.get("/api/v2/tools/time", base_url=self.base).json["permissions"], [])
        self.assertEqual(self.client.post("/api/v2/tools/time", base_url=self.base,
                                          json={"time": "2026-01-15T09:00",
                                                "source_timezone": "Asia/Shanghai",
                                                "target_timezone": "UTC"}).status_code, 403)

    def test_china_to_new_york_and_different_season(self):
        self.login()
        winter = self.request_tool()
        self.assertEqual(winter.status_code, 200)
        self.assertEqual(winter.json["result"]["target"], "2026-01-14T20:00-05:00")
        summer = self.request_tool(time="2026-07-15T09:00")
        self.assertEqual(summer.json["result"]["target"], "2026-07-14T21:00-04:00")

    def test_rejects_invalid_fields_and_dst_ambiguity(self):
        self.login()
        self.assertEqual(self.request_tool(target_timezone="Etc/GMT+99").status_code, 400)
        self.assertEqual(self.request_tool(time="2026-11-01T01:30",
                                           source_timezone="America/New_York").status_code, 400)
        self.assertEqual(self.request_tool(time="2026-03-08T02:30",
                                           source_timezone="America/New_York").status_code, 400)
        with self.assertRaises(TimeToolError):
            convert_time("2026-01-15T09:00", [], "UTC")


if __name__ == "__main__":
    unittest.main()
