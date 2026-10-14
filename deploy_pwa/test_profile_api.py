"""Authenticated opt-in and provenance checks for the companion profile API."""
import os
import sqlite3
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from deploy_pwa import server
from deploy_pwa.chat_api import reply_to


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
                "synthetic-other-password", method="pbkdf2:sha256:1000000"),
                "MING": generate_password_hash("synthetic-ming-password", method="pbkdf2:sha256:1000000"),
                "WAITIN": generate_password_hash("synthetic-waitin-password", method="pbkdf2:sha256:1000000")}),
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

    def test_ming_seed_is_owner_scoped_sourced_and_revisable(self):
        self.login(username="MING", password="synthetic-ming-password")
        profile = self.client.get("/api/v2/profile", base_url=self.base).json
        self.assertEqual(profile["identity"]["display_name"], "王跃明")
        self.assertEqual(set(profile["account_seed"]),
                         {"display_name", "gender", "occupation", "expertise",
                          "relationship", "collaboration", "account_purpose"})
        self.assertEqual(profile["account_seed"]["relationship"]["value"], "威廷的好朋友")
        self.assertIn("独立陪伴使用者", profile["account_seed"]["account_purpose"]["value"])
        self.assertEqual(profile["account_seed"]["occupation"]["source_kind"],
                         "account_setup_user_provided")
        self.assertEqual(profile["account_seed"]["occupation"]["confidence"],
                         "user_provided_unverified")
        self.assertEqual(profile["account_seed"]["occupation"]["source_date"], "2026-10-14")
        with patch.object(server, "reply_to", return_value="听见了。") as model:
            response = self.client.post("/api/v2/chat", base_url=self.base,
                                        headers=self.headers, json={"text": "你好"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(model.call_args.kwargs["account_seed"]["expertise"]["value"],
                         "熟悉 AI 与后端")
        with patch("deploy_pwa.chat_api.urlopen") as upstream:
            upstream.return_value.__enter__.return_value = io.BytesIO(
                b'{"content":[{"type":"text","text":"ok"}]}')
            reply_to("你好", [], owner_id="MING", display_name=profile["identity"]["display_name"],
                     account_seed=profile["account_seed"])
            request_body = json.loads(upstream.call_args.args[0].data)
        self.assertIn("王跃明", request_body["system"])
        self.assertIn("不预设他只想谈技术", request_body["system"])
        self.assertIn("不表示可以主动私讯或安排联络", request_body["system"])
        wrong_origin = self.client.patch("/api/v2/profile/account-seed/occupation",
                                         base_url=self.base, json={"value": "工程师"})
        self.assertEqual(wrong_origin.status_code, 403)
        corrected = self.client.patch("/api/v2/profile/account-seed/occupation",
                                      base_url=self.base, headers=self.headers,
                                      json={"value": "软件工程师"})
        self.assertEqual(corrected.status_code, 200)
        self.assertEqual(corrected.json["item"]["source_kind"], "authenticated_user_correction")
        self.assertEqual(self.client.get("/api/v2/profile", base_url=self.base).json
                         ["account_seed"]["occupation"]["value"], "软件工程师")

        retracted = self.client.patch("/api/v2/profile/account-seed/gender",
                                      base_url=self.base, headers=self.headers,
                                      json={"value": None})
        self.assertEqual(retracted.status_code, 200)
        self.assertEqual(retracted.json["item"]["status"], "retracted")
        with sqlite3.connect(str(Path(self.profile_root) / "memory.sqlite3")) as db:
            revisions = db.execute("SELECT source_kind FROM account_seed_revisions "
                                   "WHERE owner_id='MING' AND field='occupation' "
                                   "ORDER BY recorded_at,rowid").fetchall()
        self.assertEqual([row[0] for row in revisions],
                         ["account_setup_user_provided", "authenticated_user_correction"])
        other = server.app.test_client()
        self.login(other, "other", "synthetic-other-password")
        self.assertEqual(other.get("/api/v2/profile", base_url=self.base).json["account_seed"], {})
        self.assertEqual(other.patch("/api/v2/profile/account-seed/occupation",
                                     base_url=self.base, headers=self.headers,
                                     json={"value": "冒名修改"}).status_code, 400)
        self.assertEqual(self.client.get("/api/v2/profile", base_url=self.base).json
                         ["account_seed"]["occupation"]["value"], "软件工程师")

    def test_waitin_seed_and_memory_are_isolated_from_ming(self):
        self.login(username="waitin", password="synthetic-waitin-password")
        profile = self.client.get("/api/v2/profile", base_url=self.base).json
        self.assertEqual(profile["identity"]["display_name"], "威廷")
        self.assertEqual(profile["account_seed"]["relationship"]["value"], "陈端倪的创作者")
        self.assertEqual(profile["account_seed"]["relationship"]["confidence"],
                         "user_provided_unverified")
        _, kwargs = self.chat("你好")
        self.assertEqual(kwargs["owner_id"], "WAITIN")
        self.assertEqual(kwargs["account_seed"]["display_name"]["value"], "威廷")
        self.assertEqual(self.client.post("/api/v2/memories", base_url=self.base,
                                          headers=self.headers, json={"text": "仅威廷的记忆"}).status_code, 201)
        other = server.app.test_client()
        self.login(other, "MING", "synthetic-ming-password")
        self.assertEqual(other.get("/api/v2/memories", base_url=self.base).json["memories"], [])
        self.assertEqual(other.get("/api/v2/profile", base_url=self.base).json
                         ["account_seed"]["display_name"]["value"], "王跃明")

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
