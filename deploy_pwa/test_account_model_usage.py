"""Offline accounting checks; no MiniMax or LiveKit requests."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from deploy_pwa import chat_api, server
from deploy_pwa.chat_store import ChatStore, StoreUnavailable
from deploy_pwa.model_usage import response_usage


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class AccountModelUsageTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = str(Path(directory.name) / "chat.sqlite3")
        patches = [
            patch.object(server, "USERNAME", "nicole"),
            patch.object(server, "PASSWORD", "synthetic-nicole"),
            patch.object(server, "SESSION_SECRET", "synthetic-session-key-at-least-thirty-two-bytes"),
            patch.object(server, "EXTRA_ACCOUNTS", {
                "WAITIN": generate_password_hash("synthetic-waitin", method="pbkdf2:sha256:1000000"),
                "MING": generate_password_hash("synthetic-ming", method="pbkdf2:sha256:1000000"),
            }),
            patch.dict(os.environ, {"POC_CHAT_DB_PATH": self.path,
                                 "POC_LEGACY_OWNER": "nicole",
                                 "MINIMAX_API_KEY": "synthetic-never-sent"}),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def test_three_accounts_provider_usage_separate_and_missing_explicit(self):
        owner_event_ids = {}
        for owner in ("nicole", "WAITIN", "MING"):
            store = ChatStore(self.path, owner, legacy_owner_id="nicole")
            session = store.create_session()
            event = store.add_event(session, "user", "你好", source_kind="user_explicit")
            owner_event_ids[owner] = event["id"]
            observation = response_usage({"model": "MiniMax-M3", "usage": {
                "input_tokens": 12, "output_tokens": 3,
                "cache_read_input_tokens": 2}, "content": []}, "MiniMax-M3")
            store.record_text_model_usage(scope="chat", scope_id=event["id"],
                                          observation=observation)
            self.assertEqual(store.text_usage_summary()["totals"]["chat"]["requests"], 1)
            self.assertEqual(store.text_usage_summary()["totals"]["chat"]["input_tokens"], 12)
            self.assertEqual(store.text_usage_summary()["recent"][0]["counters"]["cache_read_input_tokens"], 2)
            store.record_text_model_usage(scope="chat", scope_id=event["id"],
                                          observation=response_usage({"content": []}, "MiniMax-M3"))
            self.assertEqual(store.text_usage_summary()["totals"]["chat"]["missing_usage"], 1)
            self.assertEqual(store.text_usage_summary()["cost_status"],
                             "unknown_no_rate_or_invoice")
        waitin = ChatStore(self.path, "WAITIN", legacy_owner_id="nicole")
        with self.assertRaises(StoreUnavailable):
            waitin.record_text_model_usage(scope="chat", scope_id=owner_event_ids["nicole"],
                observation=response_usage({}, "MiniMax-M3"))

    def test_provider_response_callback_before_reply_compaction(self):
        observed = []
        payload = {"model": "MiniMax-M3", "usage": {"input_tokens": 100,
                   "output_tokens": 20}, "content": [{"type": "text", "text": "你好。"}]}
        with patch.object(chat_api, "urlopen", return_value=_Response()), \
             patch.object(chat_api.json, "load", return_value=payload):
            result = chat_api.reply_to("你好", [], usage_observer=observed.append)
        self.assertEqual(result, "你好。")
        self.assertEqual(observed[0]["counters"]["input_tokens"], 100)
        self.assertEqual(observed[0]["source_status"], "provider_response_reported")

    def test_authenticated_usage_api_never_selects_another_owner(self):
        for owner in ("nicole", "WAITIN", "MING"):
            store = ChatStore(self.path, owner, legacy_owner_id="nicole")
            sid = store.create_session()
            event = store.add_event(sid, "user", "测试", source_kind="user_explicit")
            store.record_text_model_usage(scope="chat", scope_id=event["id"],
                observation=response_usage({"usage": {"input_tokens": 10}}, "MiniMax-M3"))
        client = server.app.test_client()
        base = "https://example.test"
        for owner, password in (("nicole", "synthetic-nicole"),
                                ("WAITIN", "synthetic-waitin"),
                                ("MING", "synthetic-ming")):
            logged = client.post("/api/login", base_url=base,
                                 headers={"Origin": base},
                                 json={"username": owner, "password": password})
            self.assertEqual(logged.status_code, 200)
            usage = client.get("/api/yuling/usage?owner_id=nicole", base_url=base)
            self.assertEqual(usage.status_code, 200)
            self.assertEqual(len(usage.json["text_llm"]["recent"]), 1)
            self.assertEqual(usage.json["text_llm"]["recent"][0]["scope"], "chat")
            self.assertEqual(client.post("/api/logout", base_url=base,
                                         headers={"Origin": base}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
