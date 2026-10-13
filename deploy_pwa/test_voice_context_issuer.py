"""Authenticated token issuance must not turn browser identity into memory access."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import jwt
from flask import Flask
from werkzeug.security import generate_password_hash

from deploy_pwa import server
from deploy_pwa.chat_store import ChatStore
from poc_agent.signed_context import ContextRejected, signed_context_text
import routes  # same module instance registered by deploy_pwa.server


SIGNING_KEY = "synthetic-signing-key-at-least-thirty-two-bytes"
JWT_SECRET = "synthetic-livekit-secret-at-least-32-bytes"
CREDS = {"url": "wss://example.invalid", "key": "synthetic-livekit-key",
         "secret": JWT_SECRET, "access": ""}


class VoiceContextIssuerTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db_path = str(Path(self.directory.name) / "chat.sqlite3")
        self.patches = [
            patch.object(server, "USERNAME", "nicole"),
            patch.object(server, "PASSWORD", "synthetic-nicole-password"),
            patch.object(server, "SESSION_SECRET", "synthetic-session-key-at-least-thirty-two-bytes"),
            patch.object(server, "EXTRA_ACCOUNTS", {"friend": generate_password_hash(
                "synthetic-friend-password", method="pbkdf2:sha256:1000000")}),
            patch.object(routes, "_load_livekit_creds", return_value=CREDS),
            patch.dict(os.environ, {
                "POC_VOICE_CONTEXT_ENABLED": "1",
                "POC_VOICE_CONTEXT_HMAC_KEY": SIGNING_KEY,
                "POC_CHAT_DB_PATH": self.db_path,
                "POC_LEGACY_OWNER": "nicole",
            }),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.client = server.app.test_client()
        self.base = "https://example.test"
        self.headers = {"Origin": self.base}

    def _login(self, username="nicole", password="synthetic-nicole-password"):
        response = self.client.post("/api/login", base_url=self.base, headers=self.headers,
                                    json={"username": username, "password": password})
        self.assertEqual(response.status_code, 200)

    def _call(self, headers=None, **body):
        return self.client.post("/api/yuling/token", base_url=self.base,
                                headers=self.headers if headers is None else headers,
                                json=body)

    def _agent_metadata(self, response):
        claims = jwt.decode(response.json["token"], JWT_SECRET, algorithms=["HS256"],
                            options={"verify_aud": False})
        return claims["roomConfig"]["agents"][0]["metadata"], claims

    def test_account_is_from_login_not_browser_body(self):
        alice = ChatStore(self.db_path, "nicole", legacy_owner_id="nicole")
        alice.add_memory("妮可喜欢安静", source_event_id=None)
        other = ChatStore(self.db_path, "friend", legacy_owner_id="nicole")
        other.add_memory("朋友喜欢散步", source_event_id=None)
        self._login()
        response = self._call(identity="friend", owner_id="friend", userId="friend",
                              metadata={"owner_id": "friend", "memories": other.memories()},
                              room="ailivex-yuling-forged")
        self.assertEqual(response.status_code, 200)
        metadata, claims = self._agent_metadata(response)
        self.assertNotEqual(claims["sub"], "friend")
        self.assertNotEqual(response.json["room"], "ailivex-yuling-forged")
        self.assertNotIn("妮可喜欢安静", response.json)
        context = signed_context_text(metadata, SIGNING_KEY, response.json["room"])
        self.assertIn("妮可喜欢安静", context)
        self.assertNotIn("朋友喜欢散步", context)
        with self.assertRaises(ContextRejected):
            signed_context_text(metadata, SIGNING_KEY, "ailivex-yuling-another-room")
        self.assertEqual(self.client.post("/api/logout", base_url=self.base,
                                          headers=self.headers).status_code, 200)
        self._login("friend", "synthetic-friend-password")
        friend_response = self._call(identity="nicole", owner_id="nicole")
        self.assertEqual(friend_response.status_code, 200)
        friend_metadata, _ = self._agent_metadata(friend_response)
        friend_context = signed_context_text(friend_metadata, SIGNING_KEY,
                                             friend_response.json["room"])
        self.assertIn("朋友喜欢散步", friend_context)
        self.assertNotIn("妮可喜欢安静", friend_context)

    def test_untrusted_server_mount_and_cross_site_request_fail_closed(self):
        isolated = Flask("isolated-route")
        isolated.register_blueprint(routes.yuling_bp)
        unauthenticated = isolated.test_client().post("/api/yuling/token", json={"identity": "nicole"})
        self.assertEqual(unauthenticated.status_code, 401)
        self._login()
        cross_site = self._call(headers={"Origin": "https://attacker.example"})
        self.assertEqual(cross_site.status_code, 403)

    def test_missing_key_or_store_never_issues_context(self):
        self._login()
        with patch.dict(os.environ, {"POC_VOICE_CONTEXT_HMAC_KEY": ""}):
            self.assertEqual(self._call().status_code, 503)
        with patch.dict(os.environ, {"POC_CHAT_DB_PATH": ""}):
            self.assertEqual(self._call().status_code, 503)

    def test_legacy_client_when_feature_off_has_no_signed_memory(self):
        self._login()
        with patch.dict(os.environ, {"POC_VOICE_CONTEXT_ENABLED": "0"}):
            response = self._call(identity="legacy-user")
        self.assertEqual(response.status_code, 200)
        metadata, _ = self._agent_metadata(response)
        self.assertIsNone(signed_context_text(metadata, SIGNING_KEY, response.json["room"]))

    def test_issuer_filters_sources_and_rejects_tamper(self):
        from deploy_pwa.voice_context_issuer import issue_voice_context
        room = "ailivex-yuling-test-room"
        metadata = issue_voice_context(SIGNING_KEY, room, "nicole", [
            {"source_kind": "inference_candidate", "text": "未核实的喜好"},
            {"source_kind": "user_explicit", "text": "喜欢散步"},
        ], now=100)
        self.assertIn("喜欢散步", signed_context_text(metadata, SIGNING_KEY, room, now=101))
        self.assertNotIn("未核实", signed_context_text(metadata, SIGNING_KEY, room, now=101))
        parsed = json.loads(metadata)
        token = parsed["voice_context"]
        parsed["voice_context"] = ("A" if token[0] != "A" else "B") + token[1:]
        with self.assertRaises(ContextRejected):
            signed_context_text(json.dumps(parsed), SIGNING_KEY, room, now=101)
        with self.assertRaises(ContextRejected):
            signed_context_text(metadata, SIGNING_KEY, room, now=190)

    def test_long_explicit_memories_remain_within_receiver_limit(self):
        from deploy_pwa.voice_context_issuer import issue_voice_context
        room = "ailivex-yuling-long-memories"
        metadata = issue_voice_context(SIGNING_KEY, room, "nicole", [
            {"source_kind": "user_explicit", "text": "中" * 400} for _ in range(8)
        ], now=100)
        self.assertLessEqual(len(json.loads(metadata)["voice_context"]), 11_000)
        self.assertIn("中" * 400, signed_context_text(metadata, SIGNING_KEY, room, now=101))


if __name__ == "__main__":
    unittest.main()
