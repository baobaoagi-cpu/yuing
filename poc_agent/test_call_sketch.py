import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from poc_agent.call_sketch import (
    ACK_TOPIC, CAPABILITY_TOPIC, TOPIC, CallSketch, validate_drawing,
)


class Room:
    name = "ailivex-yuling-test"

    def __init__(self):
        self.local_participant = SimpleNamespace(identity="agent", publish_data=AsyncMock())
        self.remote_participants = {"phone": SimpleNamespace(identity="phone")}
        self.listeners = {}

    def on(self, event, callback):
        self.listeners[event] = callback

    def off(self, event, callback):
        if self.listeners.get(event) == callback:
            self.listeners.pop(event)

    def packet(self, topic, payload, identity="phone"):
        self.listeners["data_received"](SimpleNamespace(
            topic=topic, data=json.dumps(payload).encode(),
            participant=SimpleNamespace(identity=identity)))

    def ready(self, enabled=True):
        self.packet(CAPABILITY_TOPIC, {"call_id": self.name, "enabled": enabled})


class SketchTests(unittest.IsolatedAsyncioTestCase):
    def test_validates_bounded_structure_and_rejects_markup(self):
        data = validate_drawing("strokes", {"x": 0.2, "y": 0.6}, 0.15, "#ffaa33", 8000,
                                strokes=[[[0, 0], [1, 1]]])
        self.assertEqual(data["strokes"], [[[0, 0], [1, 1]]])
        for bad in ("<svg>", "ABC<script>", "\nABC"):
            with self.assertRaises(ValueError):
                validate_drawing("text", {"x": 0.5, "y": 0.5}, 0.1, "#ffffff", 8000, text=bad)
        with self.assertRaises(ValueError):
            validate_drawing("sun", {"x": float("nan"), "y": 0.5}, 0.1, "#ffffff", 8000)
        with self.assertRaises(ValueError):
            validate_drawing("sun", {"x": 0.5, "y": 0.5}, 0.1, "url(foo)", 8000)
        with self.assertRaises(ValueError):
            validate_drawing("strokes", {"x": 0.5, "y": 0.5}, 0.1, "#ffffff", 8000,
                             strokes=[[[0.5, 0.5]]])

    async def test_requires_ready_and_real_render_ack(self):
        room = Room()
        sketch = CallSketch(room, room.name, ack_timeout=0.1)
        kwargs = dict(kind="sun", anchor={"x": 0.5, "y": 0.5}, size=0.2, color="#ffee00")
        self.assertIn("尚未准备", await sketch.draw(**kwargs))
        room.ready()
        task = asyncio.create_task(sketch.draw(**kwargs))
        await asyncio.sleep(0)
        data = json.loads(room.local_participant.publish_data.await_args.args[0])
        self.assertEqual(room.local_participant.publish_data.await_args.kwargs["topic"], TOPIC)
        self.assertEqual(data["call_id"], room.name)
        self.assertEqual(data["sequence"], 1)
        self.assertEqual(data["duration_ms"], 8000)
        room.packet(ACK_TOPIC, {"call_id": "other", "sequence": 1, "status": "rendered"})
        room.packet(ACK_TOPIC, {"call_id": room.name, "sequence": 1, "status": "rendered"}, "intruder")
        room.packet(ACK_TOPIC, {"call_id": room.name, "sequence": 1, "status": []})
        self.assertFalse(task.done())
        room.packet(ACK_TOPIC, {"call_id": room.name, "sequence": 1, "status": "rendered"})
        self.assertIn("已确认", await task)
        sketch.close()

    async def test_timeout_disable_and_disconnect_are_not_success(self):
        room = Room()
        sketch = CallSketch(room, room.name, ack_timeout=0.01)
        kwargs = dict(kind="heart", anchor={"x": 0.5, "y": 0.5}, size=0.2, color="#ff0000")
        room.ready()
        self.assertIn("没有确认", await sketch.draw(**kwargs))
        room.ready(False)
        self.assertIn("尚未准备", await sketch.draw(**kwargs))
        room.ready()
        task = asyncio.create_task(sketch.draw(**kwargs))
        await asyncio.sleep(0)
        room.listeners["participant_disconnected"](SimpleNamespace(identity="phone"))
        self.assertIn("失败", await task)
        sketch.close()


if __name__ == "__main__":
    unittest.main()
