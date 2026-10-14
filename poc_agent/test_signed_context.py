"""Offline security checks for the optional voice-context envelope."""

import base64
import hashlib
import hmac
import json
import unittest

from poc_agent.signed_context import ContextRejected, signed_context_text, verified_context_claims


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

    def test_nicole_name_is_available_without_saved_memories(self):
        context = signed_context_text(envelope(owner_id="nicole", memories=[], display_name="Nicole"),
                                      KEY, ROOM, now=110)
        self.assertIn("当前通话用户叫 Nicole", context)
        self.assertNotIn("用户主动保存的记忆", context)
        self.assertIsNone(signed_context_text(
            envelope(owner_id="friend", memories=[]), KEY, ROOM, now=110))

    def test_owner_id_alone_never_implies_another_users_name(self):
        self.assertIsNone(signed_context_text(
            envelope(owner_id="nicole", memories=[]), KEY, ROOM, now=110))
        context = signed_context_text(envelope(owner_id="WAITIN", memories=[],
                                               display_name="威廷"), KEY, ROOM, now=110)
        self.assertIn("当前通话用户叫 威廷", context)
        self.assertNotIn("Nicole", context)

    def test_ming_name_is_explicit_for_identity_question(self):
        seed = {"display_name": {"value": "王跃明",
                                 "source_kind": "account_setup_user_provided",
                                 "source_date": "2026-10-14",
                                 "confidence": "user_provided_unverified"}}
        context = signed_context_text(envelope(owner_id="MING", memories=[],
                                               account_seed=seed), KEY, ROOM, now=110)
        self.assertIn("王跃明", context)
        self.assertIn("当前登录账号的称呼", context)
        self.assertIn("不要把这些资料说成自己从声音认出来的", context)

    def test_sourced_seed_for_synthetic_owner_and_wrong_room_rejected(self):
        seed = {"expertise": {"value": "合成领域甲",
                 "source_kind": "account_setup_user_provided",
                 "source_date": "2026-10-14",
                 "confidence": "user_provided_unverified"}}
        metadata = envelope(owner_id="account-a", memories=[], account_seed=seed)
        self.assertIn("合成领域甲", signed_context_text(metadata, KEY, ROOM, now=110))
        with self.assertRaises(ContextRejected):
            signed_context_text(metadata, KEY, "other-room", now=110)

    def test_signed_name_and_recent_turns_are_labeled_unverified(self):
        context = signed_context_text(envelope(owner_id="WAITIN", memories=[],
            display_name="威廷", recent_turns=[{"role":"user", "text":"談到分鏡",
            "source_kind":"livekit_browser_transcript"}]), KEY, ROOM, now=110)
        self.assertIn("当前账号是威廷", context)
        self.assertIn("談到分鏡", context)
        self.assertIn("不是已核实的个人事实", context)
        with self.assertRaises(ContextRejected):
            signed_context_text(envelope(recent_turns=[{"role":"user", "text":"偽造",
                "source_kind":"unknown"}]), KEY, ROOM, now=110)

    def test_verified_owner_for_metering_requires_room_and_signature(self):
        metadata = envelope(owner_id="WAITIN", memories=[], display_name="威廷")
        claims = verified_context_claims(metadata, KEY, ROOM, now=110)
        self.assertEqual(claims["owner_id"], "WAITIN")
        self.assertEqual(claims["display_name"], "威廷")
        self.assertIsNone(verified_context_claims(metadata, "", ROOM, now=110))
        with self.assertRaises(ContextRejected):
            verified_context_claims(metadata, KEY, "ailivex-yuling-other", now=110)
        with self.assertRaises(ContextRejected):
            verified_context_claims(envelope(owner_id="nicole", display_name="Nicole\nBAD"),
                                    KEY, ROOM, now=110)

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
                "text": "x" * 501, "source_kind": "user_explicit"}]), KEY, ROOM, now=110)


if __name__ == "__main__":
    unittest.main()
