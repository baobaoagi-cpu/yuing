"""Optional preview accounts cannot inherit the legacy user's data or session."""
import hmac
import json
import os
import tempfile
import unittest
from hashlib import sha256
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from deploy_pwa import server


class PreviewAccountsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.friend_hash = generate_password_hash(
            "synthetic-friend-password", method="pbkdf2:sha256:1000000")
        cls.rotated_hash = generate_password_hash(
            "synthetic-rotated-password", method="pbkdf2:sha256:1000000")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        settings = [
            patch.object(server, "USERNAME", "nicole"),
            patch.object(server, "PASSWORD", "synthetic-nicole-password"),
            patch.object(server, "SESSION_SECRET", "synthetic-secret-at-least-thirty-two-characters"),
            patch.object(server, "EXTRA_ACCOUNTS", {"friend": self.friend_hash}),
            patch.dict(os.environ, {"POC_CHAT_DB_PATH": os.path.join(self.tmp.name, "chat.sqlite3"),
                                 "POC_LEGACY_OWNER": "nicole", "MINIMAX_API_KEY": "synthetic-key"}),
        ]
        for setting in settings:
            setting.start()
            self.addCleanup(setting.stop)
        self.client = server.app.test_client()
        self.base = "https://example.test"
        self.headers = {"Origin": self.base}

    def login(self, username, password, remember=False):
        return self.client.post("/api/login", base_url=self.base, headers=self.headers,
                                json={"username": username, "password": password,
                                      "remember": remember})

    def test_extra_account_configuration_and_legacy_cookie_tag(self):
        with patch.dict(os.environ, {"POC_EXTRA_ACCOUNTS_JSON": json.dumps(
                {"friend": self.friend_hash})}):
            self.assertEqual(server._load_extra_accounts(), {"friend": self.friend_hash})
        with patch.dict(os.environ, {"POC_EXTRA_ACCOUNTS_JSON": json.dumps(
                {"FRIEND": self.friend_hash, "friend": self.friend_hash})}):
            with self.assertRaises(RuntimeError):
                server._load_extra_accounts()
        for bad in ('{"friend":"plaintext"}', '{"nicole":"plaintext"}',
                    '{"friend":"pbkdf2:sha256:1000$bad$bad"}', '[]', '{broken'):
            with self.subTest(bad=bad), patch.dict(os.environ, {"POC_EXTRA_ACCOUNTS_JSON": bad}):
                with self.assertRaises(RuntimeError):
                    server._load_extra_accounts()
        old_tag = hmac.new(server.SESSION_SECRET.encode(),
                           f"{server.USERNAME}\0{server.PASSWORD}".encode(), sha256).hexdigest()[:24]
        self.assertEqual(server._credential_tag("nicole"), old_tag)

    def test_accounts_have_separate_history_and_memory(self):
        self.assertEqual(self.login("nicole", "synthetic-nicole-password", True).status_code, 200)
        self.assertEqual(self.client.get("/api/v2/account", base_url=self.base).json["account"], "nicole")
        with self.client.session_transaction(base_url=self.base) as cookie:
            self.assertEqual(cookie["account"], "nicole")
            self.assertTrue(cookie.permanent)
            self.assertGreater(cookie["until"], __import__("time").time() + 29 * 86400)
        saved = self.client.post("/api/v2/memories", base_url=self.base, headers=self.headers,
                                 json={"text": "private nicole preference"})
        self.assertEqual(saved.status_code, 201)
        with patch("deploy_pwa.server.reply_to", return_value="hello"):
            chat = self.client.post("/api/v2/chat", base_url=self.base, headers=self.headers,
                                    json={"text": "private nicole chat"})
        self.assertEqual(chat.status_code, 200)
        nicole_sid = chat.json["session_id"]

        self.assertEqual(self.client.post("/api/logout", base_url=self.base,
                                          headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get("/api/v2/history", base_url=self.base).status_code, 401)
        self.assertEqual(self.login("中文帐号", "wrong-password").status_code, 401)
        self.assertEqual(self.login("friend", "wrong-password").status_code, 401)
        self.assertEqual(self.login("friend", "synthetic-friend-password").status_code, 200)
        self.assertEqual(self.login(" Friend ", "synthetic-friend-password").status_code, 200)
        with self.client.session_transaction(base_url=self.base) as cookie:
            self.assertEqual(cookie["account"], "friend")
        self.assertEqual(self.client.get("/api/v2/account", base_url=self.base).json["account"], "friend")
        ringback = self.client.get("/yuling-media/ringback.wav", base_url=self.base)
        self.assertEqual(ringback.status_code, 200)
        self.assertEqual(ringback.mimetype, "audio/wav")
        self.assertTrue(ringback.data.startswith(b"RIFF"))
        ringback.close()
        self.assertEqual(self.client.get("/api/v2/history?session_id=" + nicole_sid,
                                         base_url=self.base).status_code, 404)
        self.assertEqual(self.client.get("/api/v2/history", base_url=self.base).json["sessions"], [])
        self.assertEqual(self.client.get("/api/v2/memories", base_url=self.base).json["memories"], [])
        own = self.client.post("/api/v2/memories", base_url=self.base, headers=self.headers,
                               json={"text": "private friend preference"})
        self.assertEqual(own.status_code, 201)

        self.assertEqual(self.client.post("/api/logout", base_url=self.base,
                                          headers=self.headers).status_code, 200)
        self.assertEqual(self.login("nicole", "synthetic-nicole-password").status_code, 200)
        memories = self.client.get("/api/v2/memories", base_url=self.base).json["memories"]
        self.assertEqual([row["text"] for row in memories], ["private nicole preference"])
        self.assertEqual(self.client.get("/api/v2/history?session_id=" + nicole_sid,
                                         base_url=self.base).status_code, 200)

    def test_friend_session_invalidates_when_hash_changes(self):
        self.assertEqual(self.login("friend", "synthetic-friend-password").status_code, 200)
        page = self.client.get("/chat", base_url=self.base)
        self.assertEqual(page.status_code, 200)
        page.close()
        server.EXTRA_ACCOUNTS = {"friend": self.rotated_hash}
        self.assertEqual(self.client.get("/api/v2/history", base_url=self.base).status_code, 401)
        self.assertEqual(self.client.get("/chat", base_url=self.base).status_code, 303)
        self.assertEqual(self.login("friend", "synthetic-friend-password").status_code, 401)
        self.assertEqual(self.login("friend", "synthetic-rotated-password").status_code, 200)
        self.assertEqual(self.client.get("/api/v2/history", base_url=self.base).status_code, 200)
        with self.client.session_transaction(base_url=self.base) as cookie:
            cookie.pop("account")
        self.assertEqual(self.client.get("/api/v2/history", base_url=self.base).status_code, 401)


if __name__ == "__main__":
    unittest.main()
