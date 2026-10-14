"""Checks for expression state, phone receipts, and TTS-only voice intent."""

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from poc_agent.emotion_policy import minimax_tts_text
from poc_agent.visual_emotion import (
    ACK_TOPIC, CAPABILITY_TOPIC, TOPIC, VisualEmotionEmitter,
)


async def chunks(*parts):
    for part in parts:
        yield part


class Room:
    name = "ailivex-yuling-expression"

    def __init__(self):
        self.local_participant = SimpleNamespace(identity="agent", publish_data=AsyncMock())
        self.remote_participants = {"phone": SimpleNamespace(identity="phone")}
        self.listeners = {}

    def on(self, event, callback):
        self.listeners[event] = callback

    def off(self, event, callback):
        self.listeners.pop(event, None)

    def packet(self, topic, payload, identity="phone"):
        self.listeners["data_received"](SimpleNamespace(
            topic=topic, data=json.dumps(payload).encode(),
            participant=SimpleNamespace(identity=identity)))

    def capability(self, enabled=True):
        self.packet(CAPABILITY_TOPIC, {"call_id": self.name, "enabled": enabled})


class ExpressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_video_requires_phone_ack_and_blocks_auto_override(self):
        room = Room()
        emitter = VisualEmotionEmitter(room, room.name)
        emitter.ready = True
        self.assertEqual(emitter.request_state("happy", explicit=True), "unavailable")
        room.capability()
        self.assertEqual(emitter.request_state("happy", explicit=True), "accepted")
        emitter.start_reply()
        emitter.on_assistant_part("逗你的啦。")
        self.assertEqual(emitter.sequence, 1)  # Explicit choice keeps its turn.
        await asyncio.sleep(0)
        packet = json.loads(room.local_participant.publish_data.await_args.args[0])
        self.assertEqual(room.local_participant.publish_data.await_args.kwargs["topic"], TOPIC)
        self.assertEqual(packet["state"], "happy")
        self.assertEqual(emitter.latest_result(), (1, "accepted"))
        room.packet(ACK_TOPIC, {"call_id": "other", "sequence": 1, "status": "presented"})
        room.packet(ACK_TOPIC, {"call_id": room.name, "sequence": 1, "status": "presented"}, "intruder")
        self.assertEqual(emitter.latest_result(), (1, "accepted"))
        room.packet(ACK_TOPIC, {"call_id": room.name, "sequence": 1, "status": "presented"})
        await asyncio.wait_for(asyncio.gather(*emitter._pending), 0.2)
        self.assertEqual(emitter.latest_result(), (1, "presented"))
        await emitter.close()

    async def test_sensitive_reset_and_disable(self):
        room = Room()
        emitter = VisualEmotionEmitter(room, room.name)
        emitter.ready = True
        room.capability()
        emitter.observe_user_text("我的密码被泄露")
        self.assertEqual(emitter.request_state("delighted", explicit=True), "skipped")
        self.assertEqual(emitter.request_state("neutral", explicit=True), "accepted")
        await asyncio.sleep(0)
        room.capability(False)
        await asyncio.wait_for(asyncio.gather(*emitter._pending), 0.2)
        self.assertEqual(emitter.latest_result(), (1, "skipped"))
        self.assertEqual(emitter.request_state("happy"), "unavailable")
        await emitter.close()

    async def test_sensitive_assistant_text_resets_explicit_video(self):
        room = Room()
        emitter = VisualEmotionEmitter(room, room.name)
        emitter.ready = True
        room.capability()
        self.assertEqual(emitter.request_state("playful_face", explicit=True), "accepted")
        emitter.start_reply()
        emitter.on_assistant_part("这件事涉及你的密码，我们认真说。")
        self.assertEqual(emitter.sequence, 2)
        await asyncio.sleep(0)
        packets = [json.loads(call.args[0]) for call in room.local_participant.publish_data.await_args_list]
        self.assertEqual([packet["state"] for packet in packets], ["playful_face", "neutral"])
        await emitter.close()

    async def test_timeout_and_disconnect_cannot_be_called_presented(self):
        room = Room()
        emitter = VisualEmotionEmitter(room, room.name)
        emitter.ready = True
        room.capability()
        with patch("poc_agent.visual_emotion.ACK_TIMEOUT_SECONDS", 0.01):
            self.assertEqual(emitter.request_state("happy"), "accepted")
            await asyncio.sleep(0.03)
        self.assertEqual(emitter.latest_result(), (1, "failed"))
        emitter.last_at = float("-inf")
        self.assertEqual(emitter.request_state("curious"), "accepted")
        await asyncio.sleep(0)
        room.listeners["participant_disconnected"](SimpleNamespace(identity="phone"))
        await asyncio.wait_for(asyncio.gather(*emitter._pending), 0.2)
        self.assertEqual(emitter.latest_result(), (2, "failed"))
        await emitter.close()

    async def test_explicit_tts_cue_only_changes_audio_copy(self):
        source = ["今天", "我听着。"]
        transcript = []
        audio = "".join([part async for part in minimax_tts_text(
            chunks(*source), model="speech-2.8-turbo", preferred_cue="chuckle",
            suppress_auto=True, on_source_part=transcript.append,
        )])
        self.assertEqual(audio, "(chuckle)今天我听着。")
        self.assertEqual("".join(transcript), "".join(source))
        natural = "".join([part async for part in minimax_tts_text(
            chunks("哈哈，你好。"), model="speech-2.8-turbo", suppress_auto=True,
        )])
        self.assertEqual(natural, "哈哈，你好。")
        unsupported = "".join([part async for part in minimax_tts_text(
            chunks("你好。"), model="speech-2.6-turbo", preferred_cue="chuckle",
            suppress_auto=True,
        )])
        self.assertEqual(unsupported, "你好。")
        sensitive = "".join([part async for part in minimax_tts_text(
            chunks("你很难过，我先听你说。"), model="speech-2.8-turbo",
            preferred_cue="chuckle", suppress_auto=True,
        )])
        self.assertEqual(sensitive, "你很难过，我先听你说。")

    async def test_voice_intent_consumed_once_and_expires(self):
        room = Room()
        emitter = VisualEmotionEmitter(room, room.name)
        emitter.set_voice_intent("playful")
        self.assertEqual(emitter.take_voice_intent(), "playful")
        self.assertIsNone(emitter.take_voice_intent())
        emitter.set_voice_intent("delighted")
        emitter._voice_intent_at -= 13
        self.assertIsNone(emitter.take_voice_intent())
        await emitter.close()

    async def test_new_final_user_turn_clears_unspoken_expression(self):
        room = Room()
        emitter = VisualEmotionEmitter(room, room.name)
        emitter.ready = True
        room.capability()
        emitter.set_voice_intent("playful")
        self.assertEqual(emitter.request_state("playful_face", explicit=True), "accepted")
        emitter.observe_user_text("我们换个话题。")
        emitter.start_reply()
        self.assertIsNone(emitter.take_voice_intent())
        self.assertFalse(emitter._explicit_this_reply)
        await emitter.close()

    async def test_new_user_turn_cancels_presented_video_once(self):
        room = Room()
        emitter = VisualEmotionEmitter(room, room.name)
        emitter.ready = True
        room.capability()
        self.assertEqual(emitter.request_state("playful_face", explicit=True), "accepted")
        await asyncio.sleep(0)
        room.packet(ACK_TOPIC, {"call_id": room.name, "sequence": 1, "status": "presented"})
        await asyncio.wait_for(asyncio.gather(*emitter._pending), 0.2)
        self.assertEqual(emitter.latest_result(), (1, "presented"))

        emitter.observe_user_text("换个话题吧。")
        await asyncio.sleep(0)
        packets = [json.loads(call.args[0]) for call in room.local_participant.publish_data.await_args_list]
        self.assertEqual([packet["state"] for packet in packets], ["playful_face", "neutral"])
        self.assertEqual([packet["sequence"] for packet in packets], [1, 2])
        self.assertTrue(all(set(packet) == {"call_id", "sequence", "state", "issued_at", "ttl_ms"} for packet in packets))
        self.assertEqual(emitter.latest_result(), (2, "accepted"))

        emitter.observe_user_text("继续说。")
        await asyncio.sleep(0)
        self.assertEqual(room.local_participant.publish_data.await_count, 2)
        room.packet(ACK_TOPIC, {"call_id": room.name, "sequence": 2, "status": "presented"})
        await asyncio.wait_for(asyncio.gather(*emitter._pending), 0.2)
        self.assertEqual(emitter.latest_result(), (2, "presented"))
        await emitter.close()

    async def test_expression_without_new_user_turn_reaches_next_tts_only_once(self):
        room = Room()
        emitter = VisualEmotionEmitter(room, room.name)
        emitter.set_voice_intent("thoughtful")
        emitter.start_reply()
        self.assertEqual(emitter.take_voice_intent(), "thoughtful")
        self.assertIsNone(emitter.take_voice_intent())
        await emitter.close()


if __name__ == "__main__":
    unittest.main()
