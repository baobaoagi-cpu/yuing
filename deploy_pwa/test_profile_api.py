"""Authenticated opt-in and provenance checks for the companion profile API."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from deploy_pwa import server


class ProfileApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.db_path = str(root / "chat.sqlite3")
        self.profile_root = str(root / "companion")
        self.base = "https://example.test"
        self.headers = {"Origin": self.base}
        patches = [
            patch.object(server, "USERNAME", "nicole"),
            patch.object(server, "PASSWORD", "synthetic-nicole-password"),
            patch.object(server, "SESSION_SECRET", "synthetic-secret-at-least-thirty-two-characters"),
            patch.object(server, "EXTRA_ACCOUNTS", {"other": generate_password_hash(
                "synthetic-other-password", method="pbkdf2:sha256:1000000")}),
            patch.dict(os.environ, {
                "POC_CHAT_DB_PATH": self.db_path,
                "POC_COMPANION_ROOT": self.profile_root,
                "POC_LEGACY_OWNER": "nicole",
                "MINIMAX_API_KEY": "synthetic-key-not-sent",
            }),
        ]
        for setting in patches:
            setting.start()
            self.addCleanup(setting.stop)
        self.client = server.app.test_client()

    def login(self, client=None, username="nicole", password="synthetic-nicole-password"):
        client = client or self.client
        response = client.post("/api/login", base_url=self.base, headers=self.headers,
                               json={"username": username, "password": password})
        self.assertEqual(response.status_code, 200)

    def chat(self, text, client=None):
        with patch.object(server, "reply_to", return_value="听见了。") as model:
            response = (client or self.client).post(
                "/api/v2/chat", base_url=self.base, headers=self.headers,
                json={"text": text})
        self.assertEqual(response.status_code, 200)
        return response.json, model.call_args.kwargs

    def test_explicit_consent_and_source_bound_candidate(self):
        self.assertEqual(self.client.get("/api/v2/profile", base_url=self.base).status_code, 401)
        self.login()
        profile = self.client.get("/api/v2/profile", base_url=self.base).json
        self.assertEqual(profile["identity"]["display_name"], "Nicole")
        self.assertFalse(profile["consent_enabled"])
        self.assertFalse(profile["consent_decided"])
        self.assertEqual(profile["facts"], [])
        _, model_kwargs = self.chat("我喜欢散步")
        self.assertEqual(model_kwargs["owner_id"], "nicole")
        self.assertEqual(model_kwargs["display_name"], "Nicole")
        self.assertEqual(model_kwargs["profile_candidates"], ())
        self.assertEqual(self.client.get("/api/v2/profile", base_url=self.base).json["facts"], [])
        denied = self.client.post("/api/v2/profile/consent", base_url=self.base,
                                  json={"enabled": True})
        self.assertEqual(denied.status_code, 403)
        accepted = self.client.post("/api/v2/profile/consent", base_url=self.base,
                                    headers=self.headers, json={"enabled": True})
        self.assertEqual(accepted.status_code, 200)
        self.assertTrue(accepted.json["consent_enabled"])
        exchange, model_kwargs = self.chat("我习惯早睡")
        profile = self.client.get("/api/v2/profile", base_url=self.base).json
        self.assertEqual(len(profile["facts"]), 1)
        fact = profile["facts"][0]
        self.assertEqual(fact["status"], "candidate")
        self.assertEqual(fact["source_event_id"], exchange["user_event"]["id"])
        self.assertEqual(fact["evidence_quote"], "我习惯早睡")
        self.assertEqual(model_kwargs["profile_candidates"][0]["source_event_id"], exchange["user_event"]["id"])
        self.assertTrue(Path(self.profile_root, "memory.sqlite3").exists())
        self.assertTrue(Path(self.profile_root, "graph.sqlite3").exists())
        self.assertTrue(Path(self.profile_root, "knowledge.sqlite3").exists())
        self.assertEqual(self.client.delete("/api/v2/profile/facts/" + fact["id"],
                                            base_url=self.base, headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get("/api/v2/profile", base_url=self.base).json["facts"], [])
        declined = self.client.post("/api/v2/profile/consent", base_url=self.base,
                                    headers=self.headers, json={"enabled": False})
        self.assertFalse(declined.json["consent_enabled"])
        self.assertTrue(self.client.get("/api/v2/profile", base_url=self.base).json["consent_decided"])
        self.chat("我喜欢画画")
        self.assertEqual(self.client.get("/api/v2/profile", base_url=self.base).json["facts"], [])

    def test_other_account_cannot_read_or_delete_nicole_candidates(self):
        self.login()
        self.client.post("/api/v2/profile/consent", base_url=self.base,
                         headers=self.headers, json={"enabled": True})
        self.chat("我喜欢散步")
        fact_id = self.client.get("/api/v2/profile", base_url=self.base).json["facts"][0]["id"]
        other = server.app.test_client()
        self.login(other, "other", "synthetic-other-password")
        other_profile = other.get("/api/v2/profile", base_url=self.base).json
        self.assertIsNone(other_profile["identity"])
        self.assertEqual(other_profile["facts"], [])
        self.assertEqual(other.delete("/api/v2/profile/facts/" + fact_id,
                                      base_url=self.base, headers=self.headers).status_code, 404)
        self.assertEqual(len(self.client.get("/api/v2/profile", base_url=self.base).json["facts"]), 1)

    def test_skill_need_is_logged_as_candidate_without_activation(self):
        self.login()
        exchange, _ = self.chat("我想让你学会整理购物清单")
        candidates = self.client.get("/api/v2/skill-requests", base_url=self.base).json["candidates"]
        self.assertEqual(len(candidates), 1)
        item = candidates[0]
        self.assertEqual(item["source_event_id"], exchange["user_event"]["id"])
        self.assertEqual(item["owner_id"], "nicole")
        self.assertEqual(item["user_need"], "整理购物清单")
        self.assertEqual(item["status"], "candidate")
        self.assertEqual(item["cost"], "unknown")
        self.chat("你今天好吗？")
        self.assertEqual(len(self.client.get("/api/v2/skill-requests", base_url=self.base).json["candidates"]), 1)
        other = server.app.test_client()
        self.login(other, "other", "synthetic-other-password")
        self.assertEqual(other.get("/api/v2/skill-requests", base_url=self.base).json["candidates"], [])

    def test_unconfigured_profile_does_not_claim_storage(self):
        self.login()
        with patch.dict(os.environ, {"POC_COMPANION_ROOT": ""}):
            self.assertEqual(self.client.get("/api/v2/profile", base_url=self.base).status_code, 503)
            self.assertEqual(self.client.get("/api/v2/skill-requests", base_url=self.base).status_code, 503)
            _, kwargs = self.chat("我喜欢散步")
            self.assertEqual(kwargs["owner_id"], "nicole")

    def test_railway_requires_mounted_volume_for_profile_capture(self):
        self.login()
        with patch.dict(os.environ, {"RAILWAY_SERVICE_ID": "synthetic-service",
                                     "RAILWAY_VOLUME_MOUNT_PATH": ""}):
            self.assertEqual(self.client.get("/api/v2/profile", base_url=self.base).status_code, 503)
            self.assertEqual(self.client.get("/api/v2/skill-requests", base_url=self.base).status_code, 503)
        with patch.dict(os.environ, {"RAILWAY_SERVICE_ID": "synthetic-service",
                                     "RAILWAY_VOLUME_MOUNT_PATH": self.profile_root}):
            # Profile directory is on the mount, but chat evidence is not.
            self.assertEqual(self.client.get("/api/v2/profile", base_url=self.base).status_code, 503)


if __name__ == "__main__":
    unittest.main()
