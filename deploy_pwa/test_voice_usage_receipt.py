"""Agent-signed metering receipts must remain isolated by account."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from deploy_pwa import server
from poc_agent.call_usage_meter import signed_receipt


KEY = "synthetic-usage-key-at-least-thirty-two-bytes"
BASE = "https://example.test"


class VoiceUsageReceiptTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = str(Path(directory.name) / "chat.sqlite3")
        patches = [
            patch.object(server, "USERNAME", "nicole"),
            patch.object(server, "PASSWORD", "synthetic-nicole-password"),
            patch.object(server, "SESSION_SECRET", "synthetic-session-key-at-least-thirty-two-bytes"),
            patch.object(server, "EXTRA_ACCOUNTS", {
                "WAITIN": generate_password_hash("synthetic-waitin-password", method="pbkdf2:sha256:1000000"),
                "MING": generate_password_hash("synthetic-ming-password", method="pbkdf2:sha256:1000000"),
            }),
            patch.dict(os.environ, {"POC_CHAT_DB_PATH": self.path,
                                 "POC_LEGACY_OWNER": "nicole",
                                 "POC_USAGE_HMAC_KEY": KEY}),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.client = server.app.test_client()

    def _receipt(self, owner="WAITIN", room="ailivex-yuling-usage-test"):
        return signed_receipt(
            verified_owner_id=owner, room=room,
            sdk_usage={"type": "llm_usage", "provider": "test-provider",
                       "model": "test-model", "input_tokens": 120,
                       "output_tokens": 25, "total_requests": 1}, secret=KEY)

    def _post(self, raw, signature):
        return self.client.post("/api/yuling/usage/ingest", base_url=BASE, data=raw,
                                headers={"Content-Type": "application/json",
                                         "X-Usage-Signature": signature})

    def _login(self, name, password):
        result = self.client.post("/api/login", base_url=BASE,
                                  headers={"Origin": BASE},
                                  json={"username": name, "password": password})
        self.assertEqual(result.status_code, 200)

    def test_signed_receipt_owner_scope_and_replay(self):
        raw, signature = self._receipt()
        self.assertEqual(self._post(raw, signature).status_code, 201)
        replay = self._post(raw, signature)
        self.assertEqual(replay.status_code, 200)
        self.assertFalse(replay.json["changed"])
        self.assertEqual(self.client.get("/api/yuling/usage", base_url=BASE).status_code, 401)
        self._login("WAITIN", "synthetic-waitin-password")
        mine = self.client.get("/api/yuling/usage", base_url=BASE)
        self.assertEqual(mine.status_code, 200)
        self.assertEqual(mine.json["totals"][0]["input_tokens"], 120)
        self.assertEqual(len(mine.json["recent"]), 1)
        self.assertEqual(self.client.post("/api/logout", base_url=BASE,
                                          headers={"Origin": BASE}).status_code, 200)
        self._login("MING", "synthetic-ming-password")
        other = self.client.get("/api/yuling/usage?owner_id=WAITIN", base_url=BASE)
        self.assertEqual(other.status_code, 200)
        self.assertEqual(other.json["recent"], [])
        self.assertEqual(other.json["totals"], [])

    def test_invalid_signature_and_browser_payload_cannot_write(self):
        raw, signature = self._receipt()
        altered = json.loads(raw)
        altered["owner_id"] = "nicole"
        forged = json.dumps(altered).encode()
        self.assertEqual(self._post(forged, signature).status_code, 403)
        self.assertEqual(self._post(raw, "sha256=" + "0" * 64).status_code, 403)
        self._login("WAITIN", "synthetic-waitin-password")
        self.assertEqual(self._post(raw, "").status_code, 403)
        self.assertEqual(self.client.get("/api/yuling/usage", base_url=BASE).json["recent"], [])

    def test_stale_unknown_owner_and_cross_owner_event_id_rejected(self):
        raw, signature = self._receipt()
        self.assertEqual(self._post(raw, signature).status_code, 201)
        from hashlib import sha256
        import hmac
        changed = json.loads(raw)
        changed["owner_id"] = "MING"
        changed_raw = json.dumps(changed, sort_keys=True, separators=(",", ":")).encode()
        changed_sig = "sha256=" + hmac.new(KEY.encode(), changed_raw, sha256).hexdigest()
        self.assertEqual(self._post(changed_raw, changed_sig).status_code, 409)
        stale_raw, stale_sig = signed_receipt(
            verified_owner_id="WAITIN", room="ailivex-yuling-old",
            sdk_usage={"type": "llm_usage", "provider": "test-provider", "model": "test-model"},
            secret=KEY, created_at=int(time.time()) - 3600)
        self.assertEqual(self._post(stale_raw, stale_sig).status_code, 400)
        unknown_raw, unknown_sig = self._receipt(owner="unknown", room="ailivex-yuling-unknown")
        self.assertEqual(self._post(unknown_raw, unknown_sig).status_code, 400)

    def test_no_volume_or_key_fails_closed(self):
        raw, signature = self._receipt()
        with patch.dict(os.environ, {"RAILWAY_SERVICE_ID": "synthetic-railway",
                                 "RAILWAY_VOLUME_MOUNT_PATH": ""}):
            self.assertEqual(self._post(raw, signature).status_code, 503)
        with patch.dict(os.environ, {"POC_USAGE_HMAC_KEY": ""}):
            self.assertEqual(self._post(raw, signature).status_code, 503)


if __name__ == "__main__":
    unittest.main()
