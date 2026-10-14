"""Text chat loads only topical memory from the authenticated account."""
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from deploy_pwa import chat_api, server
from deploy_pwa.chat_store import ChatStore


class ProgressiveChatMemoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hashes = {
            "MING": generate_password_hash("synthetic-ming-pass", method="pbkdf2:sha256:1000000"),
            "WAITIN": generate_password_hash("synthetic-waitin-pass", method="pbkdf2:sha256:1000000"),
        }

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = str(Path(self.temp.name) / "chat.sqlite3")
        self.base = "https://example.test"
        self.headers = {"Origin": self.base}
        settings = [
            patch.object(server, "USERNAME", "nicole"),
            patch.object(server, "PASSWORD", "synthetic-nicole-pass"),
            patch.object(server, "SESSION_SECRET", "synthetic-secret-at-least-thirty-two-characters"),
            patch.object(server, "EXTRA_ACCOUNTS", self.hashes),
            patch.dict(os.environ, {
                "POC_CHAT_DB_PATH": path, "POC_LEGACY_OWNER": "nicole",
                "POC_COMPANION_ROOT": str(Path(self.temp.name) / "companion"),
                "MINIMAX_API_KEY": "synthetic-key-not-sent",
            }),
        ]
        for setting in settings:
            setting.start()
            self.addCleanup(setting.stop)
        self.stores = {}
        for account, topic, phrase in (
                ("nicole", "爵士音乐", "妮可喜欢爵士音乐"),
                ("MING", "航天模型", "王跃明喜欢航天模型"),
                ("WAITIN", "咖啡豆", "威廷喜欢咖啡豆")):
            store = ChatStore(path, account, legacy_owner_id="nicole")
            sid = store.create_session()
            source = store.add_event(sid, "user", phrase, source_kind="user_explicit")
            store.add_memory(phrase, source["id"])
            self.stores[account] = store
        self.stores["WAITIN"].add_memory("威廷的旧咖啡豆记录没有来源")

    def _ask(self, username, password, text):
        client = server.app.test_client()
        login = client.post("/api/login", base_url=self.base, headers=self.headers,
                            json={"username": username, "password": password})
        self.assertEqual(login.status_code, 200)
        captured = []

        def fake_open(request, timeout):
            captured.append(json.loads(request.data))
            return io.BytesIO('{"content":[{"type":"text","text":"明白。"}]}'.encode())

        with patch.object(chat_api, "urlopen", side_effect=fake_open):
            response = client.post("/api/v2/chat", base_url=self.base,
                                   headers=self.headers, json={"text": text})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(len(captured), 1)
        return captured[0]["system"]

    def test_three_accounts_retrieve_only_their_topic_and_provenance(self):
        cases = (
            ("Nicole", "synthetic-nicole-pass", "爵士音乐", "妮可喜欢爵士音乐"),
            ("ming", "synthetic-ming-pass", "航天模型", "王跃明喜欢航天模型"),
            ("WAITIN", "synthetic-waitin-pass", "咖啡豆", "威廷喜欢咖啡豆"),
        )
        for account, password, topic, expected in cases:
            with self.subTest(account=account):
                prompt = self._ask(account, password, "你记得我喜欢的" + topic + "吗？")
                self.assertIn(expected, prompt)
                for other in ("妮可喜欢爵士音乐", "王跃明喜欢航天模型", "威廷喜欢咖啡豆"):
                    if other != expected:
                        self.assertNotIn(other, prompt)
        self.assertIn("来源未绑定，待本人确认", prompt)
        self.assertIn("威廷的旧咖啡豆记录没有来源", prompt)
        self.assertIn("不要把未绑定来源的内容说成已核实事实", prompt)

    def test_generic_turn_does_not_load_latest_memory(self):
        prompt = self._ask("WAITIN", "synthetic-waitin-pass", "你好")
        self.assertNotIn("威廷喜欢咖啡豆", prompt)
        self.assertNotIn("威廷的旧咖啡豆记录", prompt)


if __name__ == "__main__":
    unittest.main()
