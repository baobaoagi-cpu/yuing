"""Account separation and cumulative metering without any provider spend."""

from decimal import Decimal
import hashlib
import hmac
import json
import tempfile
import unittest
from unittest.mock import patch

from poc_agent.call_usage_meter import (CallUsageLedger, Rate, UsageLedgerError,
                                        signed_receipt, send_signed_receipts)


class CallUsageLedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = CallUsageLedger(self.tmp.name + "/usage.sqlite3")

    @staticmethod
    def metric(tokens):
        return {"type": "llm_usage", "provider": "minimax", "model": "M3",
                "input_tokens": tokens, "output_tokens": 12,
                "output_reasoning_tokens": 9}

    def test_three_test_accounts_are_isolated_and_snapshots_are_not_added(self):
        accounts = ("WAITIN", "MING", "nicole")
        for index, owner in enumerate(accounts):
            room = "ailivex-yuling-room" + str(index)
            self.ledger.begin_call(verified_owner_id=owner, room=room)
            self.ledger.record_sdk_snapshot(verified_owner_id=owner, room=room,
                                             model_usage=[self.metric(10 + index)])
            self.ledger.record_sdk_snapshot(verified_owner_id=owner, room=room,
                                             model_usage=[self.metric(20 + index)])
            self.ledger.close_call(verified_owner_id=owner, room=room)
        for index, owner in enumerate(accounts):
            calls = self.ledger.account_calls(owner)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["models"][0]["sdk_usage"]["input_tokens"], 20 + index)
            self.assertEqual(calls[0]["models"][0]["cost_status"], "unpriced")
            self.assertIsNone(calls[0]["models"][0]["estimated_cost"])
            self.assertEqual(len(self.ledger.export_account_jsonl(owner).splitlines()), 1)
            self.assertNotIn("nicole", self.ledger.export_account_jsonl("WAITIN"))
        self.assertEqual(self.ledger.account_calls("unknown"), [])

    def test_room_cannot_cross_account(self):
        room = "ailivex-yuling-private"
        self.ledger.begin_call(verified_owner_id="WAITIN", room=room)
        with self.assertRaises(UsageLedgerError):
            self.ledger.begin_call(verified_owner_id="nicole", room=room)
        with self.assertRaises(UsageLedgerError):
            self.ledger.record_sdk_snapshot(verified_owner_id="nicole", room=room,
                                             model_usage=[self.metric(100)])
        with self.assertRaises(UsageLedgerError):
            self.ledger.close_call(verified_owner_id="nicole", room=room)
        self.assertEqual(self.ledger.account_calls("nicole"), [])

    def test_explicit_rate_is_preserved_as_estimate(self):
        room = "ailivex-yuling-rate"
        self.ledger.begin_call(verified_owner_id="MING", room=room)
        rate = Rate(currency="USD", measure="input_tokens", units=Decimal(1000),
                    amount=Decimal("0.5"), source="test fixture only", effective_at="2026-10-14")
        self.ledger.record_sdk_snapshot(verified_owner_id="MING", room=room,
            model_usage=[self.metric(200)], rates={("llm_usage", "minimax", "M3"): [rate]})
        usage = self.ledger.account_calls("MING")[0]["models"][0]
        self.assertEqual(usage["source_status"], "livekit_sdk_reported")
        self.assertEqual(usage["cost_status"], "operator_rate_estimate")
        self.assertEqual(Decimal(usage["estimated_cost"]), Decimal("0.1"))
        self.assertEqual(usage["rate_snapshot"][0]["source"], "test fixture only")

    def test_rejects_invalid_measurements_and_rates(self):
        room = "ailivex-yuling-validation"
        self.ledger.begin_call(verified_owner_id="WAITIN", room=room)
        for invalid in (dict(self.metric(2), input_tokens=-1),
                        dict(self.metric(2), input_tokens=True),
                        dict(self.metric(2), model="")):
            with self.assertRaises(UsageLedgerError):
                self.ledger.record_sdk_snapshot(verified_owner_id="WAITIN", room=room,
                                                 model_usage=[invalid])
        bad = Rate("USD", "input_tokens", Decimal(0), Decimal(1), "fixture", "2026-10-14")
        with self.assertRaises(UsageLedgerError):
            self.ledger.record_sdk_snapshot(verified_owner_id="WAITIN", room=room,
                model_usage=[self.metric(1)], rates={("llm_usage", "minimax", "M3"): [bad]})
        self.assertEqual(self.ledger.account_calls("WAITIN")[0]["models"], [])

    def test_signed_receipt_is_account_bound_and_unpriced(self):
        secret = "s" * 40
        room = "ailivex-yuling-receipt"
        raw, header = signed_receipt(verified_owner_id="WAITIN", room=room,
                                     sdk_usage=self.metric(45), secret=secret,
                                     created_at=123456)
        body = json.loads(raw)
        self.assertEqual(body["owner_id"], "WAITIN")
        self.assertEqual(body["component"], "llm")
        self.assertIsNone(body["estimated_usd_micros"])
        self.assertEqual(body["metadata"]["source_status"], "livekit_sdk_reported")
        self.assertEqual(header, "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest())
        again, _ = signed_receipt(verified_owner_id="WAITIN", room=room,
                                  sdk_usage=self.metric(45), secret=secret,
                                  created_at=123457)
        self.assertEqual(body["event_id"], json.loads(again)["event_id"])

    def test_send_failure_is_not_silent(self):
        with patch("poc_agent.call_usage_meter.urlopen", side_effect=TimeoutError):
            with self.assertRaises(UsageLedgerError):
                send_signed_receipts(endpoint="https://meter.example/ingest",
                                     verified_owner_id="WAITIN",
                                     room="ailivex-yuling-receipt", model_usage=[self.metric(2)],
                                     secret="s" * 40)


if __name__ == "__main__":
    unittest.main()
