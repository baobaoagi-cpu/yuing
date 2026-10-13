"""Offline contract for the MiniMax audio format sent to LiveKit."""

import os
import unittest
from unittest.mock import patch

from poc_agent.tts_config import minimax_tts_options


class MiniMaxTTSConfigTest(unittest.TestCase):
    def test_stream_is_raw_pcm_at_matching_sample_rate(self):
        # Provider accepts PCM bytes; LiveKit's PCM emitter must receive the
        # same declared rate as the provider's audio_setting.
        with patch.dict(os.environ, {"MINIMAX_TTS_MODEL": "speech-2.8-turbo"}):
            opts = minimax_tts_options("test-only-key")
        self.assertEqual((opts["audio_format"], opts["sample_rate"]), ("pcm", 24000))
        self.assertEqual(opts["model"], "speech-2.8-turbo")
        self.assertEqual(opts["api_key"], "test-only-key")

    def test_audio_format_cannot_be_overridden_by_stale_environment(self):
        with patch.dict(os.environ, {"MINIMAX_TTS_AUDIO_FORMAT": "mp3"}):
            self.assertEqual(minimax_tts_options("test-only-key")["audio_format"], "pcm")


if __name__ == "__main__":
    unittest.main()
