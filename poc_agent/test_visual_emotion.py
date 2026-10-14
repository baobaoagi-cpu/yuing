import asyncio
import json
import unittest
from unittest.mock import AsyncMock

from poc_agent.visual_emotion import (
    TOPIC, VisualEmotionEmitter, choose_visual_state, make_packet,
)


class VisualEmotionTests(unittest.IsolatedAsyncioTestCase):
    def test_high_confidence_only_and_sensitive_reset(self):
        self.assertIsNone(choose_visual_state("我听到了，你继续说。"))
        self.assertEqual(choose_visual_state("逗你的啦。"), "playful_face")
        self.assertEqual(choose_visual_state("太棒了，真替你高兴。"), "delighted")
        self.assertEqual(choose_visual_state("太棒了，但这涉及你的密码。"), "neutral")

    def test_packet_contains_only_bounded_public_fields(self):
        data = json.loads(make_packet("ailivex-yuling-test", 1, "happy", 1234567))
        self.assertEqual(set(data), {"call_id", "sequence", "state", "issued_at", "ttl_ms"})
        self.assertEqual(data["ttl_ms"], 8000)
        self.assertNotIn("prompt", str(data))
        with self.assertRaises(ValueError):
            make_packet("r", 1, "secret", 123)

    async def test_publish_sdk_schema_ready_cooldown_and_neutral(self):
        class Room:
            name = "ailivex-yuling-test"
            local_participant = type("Participant", (), {})()

        room = Room()
        room.local_participant.publish_data = AsyncMock()
        emitter = VisualEmotionEmitter(room, room.name)
        emitter.on_assistant_part("逗你的啦")
        self.assertEqual(room.local_participant.publish_data.await_count, 0)
        emitter.ready = True
        emitter.browser_ready = True
        emitter.on_assistant_part("逗你的啦")
        emitter.on_assistant_part("真好呀")  # bounded cooldown
        emitter.on_assistant_part("说到密码，我们先不要谈细节。")  # immediate safety reset
        await asyncio.sleep(0)
        await emitter.close()
        self.assertEqual(room.local_participant.publish_data.await_count, 2)
        packets = [json.loads(c.args[0]) for c in room.local_participant.publish_data.await_args_list]
        self.assertEqual([p["state"] for p in packets], ["playful_face", "neutral"])
        self.assertEqual([p["sequence"] for p in packets], [1, 2])
        for call in room.local_participant.publish_data.await_args_list:
            self.assertEqual(call.kwargs, {"reliable": True, "topic": TOPIC})

    async def test_split_assistant_text_is_classified_once(self):
        class Room:
            name = "ailivex-yuling-split"
            local_participant = type("Participant", (), {})()

        room = Room()
        room.local_participant.publish_data = AsyncMock()
        emitter = VisualEmotionEmitter(room, room.name)
        emitter.ready = True
        emitter.browser_ready = True
        for part in ("太", "棒", "了", "！", "真替你高兴。"):
            emitter.on_assistant_part(part)
        await asyncio.sleep(0)
        await emitter.close()
        self.assertEqual(room.local_participant.publish_data.await_count, 1)
        packet = json.loads(room.local_participant.publish_data.await_args.args[0])
        self.assertEqual(packet["state"], "delighted")

    async def test_new_reply_discards_previous_text_without_resetting_sequence(self):
        class Room:
            name = "ailivex-yuling-turns"
            local_participant = type("Participant", (), {})()

        room = Room()
        room.local_participant.publish_data = AsyncMock()
        emitter = VisualEmotionEmitter(room, room.name)
        emitter.ready = True
        emitter.browser_ready = True
        emitter.on_assistant_part("太棒了！")
        emitter.start_reply()
        self.assertEqual(emitter._recent_text, "")
        emitter.on_assistant_part("我听着，你继续说。")
        self.assertEqual(emitter.sequence, 1)
        emitter.last_at = float("-inf")  # simulate the next allowed cue interval
        emitter.start_reply()
        emitter.on_assistant_part("逗你的啦。")
        await asyncio.sleep(0)
        await emitter.close()
        packets = [json.loads(c.args[0]) for c in room.local_participant.publish_data.await_args_list]
        self.assertEqual([p["state"] for p in packets], ["delighted", "playful_face"])
        self.assertEqual([p["sequence"] for p in packets], [1, 2])


if __name__ == "__main__":
    unittest.main()
