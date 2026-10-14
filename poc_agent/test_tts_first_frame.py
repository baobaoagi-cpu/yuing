"""First-frame watchdog tests; no provider calls or spoken text logging."""

import asyncio
import logging
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent import Agent, ChenDunni


async def _text():
    yield "你好。"


class FirstFrameTest(unittest.IsolatedAsyncioTestCase):
    async def test_first_frame_is_forwarded(self):
        frame = SimpleNamespace(sample_rate=24000, samples_per_channel=4800)

        async def fake_tts(_agent, _text, _settings):
            yield frame

        with patch.object(Agent.default, "tts_node", fake_tts):
            output = [item async for item in ChenDunni().tts_node(_text(), None)]
        self.assertEqual(output, [frame])

    async def test_stalled_provider_fails_in_bounded_time(self):
        async def stuck_tts(_agent, _text, _settings):
            await asyncio.Event().wait()
            yield SimpleNamespace(sample_rate=24000, samples_per_channel=1)

        with patch.object(Agent.default, "tts_node", stuck_tts), \
             patch("agent.TTS_FIRST_FRAME_TIMEOUT", 0.01), \
             self.assertLogs("duanni.call", logging.ERROR) as logs:
            with self.assertRaises(asyncio.TimeoutError):
                async for _ in ChenDunni().tts_node(_text(), None):
                    pass
        self.assertIn("tts_first_frame_timeout", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
