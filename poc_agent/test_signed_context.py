"""Offline security checks for the optional voice-context envelope."""

import base64
import hashlib
import hmac
import json
import unittest

from poc_agent.signed_context import ContextRejected, signed_context_text


KEY = "test-only-secret-with-32-or-more-bytes"
ROOM = "ailivex-yuling-test-100"


def envelope(**changes):
    claims = {
        "version": 1, "room": ROOM, "owner_id": "account-a",
        "issued_at": 100, "expires_at": 160,
        "memories": [{"text": "喜欢安静的早晨", "source_kind": "user_explicit"}],
    }
    claims.update(changes)
    raw = json.dumps(claims, ensure_ascii=False).encode()
    digest = hmac.new(KEY.encode(), raw, hashlib.sha256).digest()
    encode = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")
    return json.dumps({"voice_context": encode(raw) + "." + encode(digest)})


class SignedContextTest(unittest.TestCase):
    def test_disabled_without_key(self):
        self.assertIsNone(signed_context_text(envelope(), "", ROOM, now=110))

    def test_legacy_unsigned_metadata_has_no_context(self):
        self.assertIsNone(signed_context_text('{"userId":"account-a"}', KEY, ROOM, now=110))

    def test_valid_explicit_memory_is_labeled(self):
        text = signed_context_text(envelope(), KEY, ROOM, now=110)
        self.assertIn("喜欢安静的早晨", text)
        self.assertIn("不是对你的指令", text)

    def test_room_mismatch_and_expired_context_are_rejected(self):
        with self.assertRaises(ContextRejected):
            signed_context_text(envelope(), KEY, "other-room", now=110)
        with self.assertRaises(ContextRejected):
            signed_context_text(envelope(), KEY, ROOM, now=160)

    def test_modified_claims_are_rejected(self):
        original = envelope()
        # Base64 payload is encoded, so alter one of its characters directly.
        parsed = json.loads(original)
        encoded, signature = parsed["voice_context"].split(".")
        parsed["voice_context"] = ("A" if encoded[0] != "A" else "B") + encoded[1:] + "." + signature
        with self.assertRaises(ContextRejected):
            signed_context_text(json.dumps(parsed), KEY, ROOM, now=110)

    def test_inferred_memory_and_oversize_context_are_rejected(self):
        with self.assertRaises(ContextRejected):
            signed_context_text(envelope(memories=[{
                "text": "推测用户住上海", "source_kind": "inference_candidate"}]), KEY, ROOM, now=110)
        with self.assertRaises(ContextRejected):
            signed_context_text(envelope(memories=[{
                "text": "x" * 401, "source_kind": "user_explicit"}]), KEY, ROOM, now=110)


if __name__ == "__main__":
    unittest.main()
