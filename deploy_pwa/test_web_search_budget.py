"""The call-search trial cap is persisted and enforced before provider calls."""

import hashlib
import hmac
import json
import os
import sqlite3
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from deploy_pwa import server
from deploy_pwa.search_budget import TRIAL_SEARCH_CAP


KEY = "synthetic-usage-key-at-least-thirty-two-bytes"


class SearchBudgetTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = str(Path(directory.name) / "chat.sqlite3")
        for item in (
            patch.object(server, "USERNAME", "nicole"),
            patch.object(server, "EXTRA_ACCOUNTS", {"WAITIN": "unused-hash"}),
            patch.dict(os.environ, {"POC_CHAT_DB_PATH": self.path,
                                 "POC_LEGACY_OWNER": "nicole",
                                 "POC_USAGE_HMAC_KEY": KEY}),
        ):
            item.start()
            self.addCleanup(item.stop)
        self.client = server.app.test_client()

    def _post(self, owner="WAITIN", *, request_id=None, key=KEY):
        item = {"request_id": request_id or uuid.uuid4().hex,
                "owner_id": owner, "call_id": "ailivex-yuling-search-test",
                "created_at": int(time.time())}
        raw = json.dumps(item, sort_keys=True, separators=(",", ":")).encode()
        signature = "sha256=" + hmac.new(
            key.encode(), b"dunni-search-v1\n" + raw, hashlib.sha256
        ).hexdigest()
        response = self.client.post(
            "/api/yuling/search/reserve", base_url="https://example.test", data=raw,
            headers={"Content-Type": "application/json", "X-Search-Signature": signature},
        )
        return response, item["request_id"]

    def test_signed_reservations_stop_at_persistent_lifetime_cap(self):
        first, request_id = self._post()
        self.assertEqual(first.status_code, 201)
        self.assertEqual(self._post(request_id=request_id)[0].status_code, 201)
        for _ in range(TRIAL_SEARCH_CAP - 1):
            self.assertEqual(self._post()[0].status_code, 201)
        self.assertEqual(self._post()[0].status_code, 429)
        with sqlite3.connect(self.path) as db:
            count = db.execute("SELECT COUNT(*) FROM web_search_reservations").fetchone()[0]
        self.assertEqual(count, TRIAL_SEARCH_CAP)

    def test_bad_signature_or_owner_never_spends_budget(self):
        self.assertEqual(self._post(key="wrong-key-at-least-thirty-two-bytes")[0].status_code, 403)
        self.assertEqual(self._post(owner="UNKNOWN")[0].status_code, 400)
        self.assertEqual(self._post(owner="WAITIN")[0].status_code, 201)
        with sqlite3.connect(self.path) as db:
            count = db.execute("SELECT COUNT(*) FROM web_search_reservations").fetchone()[0]
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
