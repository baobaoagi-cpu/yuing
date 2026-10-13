"""Offline checks for the TTS-only emotion markup boundary."""

import unittest

from poc_agent.emotion_policy import minimax_tts_text, tts_only_interjection


async def _chunks(*items):
    for item in items:
        yield item


class MiniMaxEmotionPolicyTest(unittest.IsolatedAsyncioTestCase):
    async def test_supported_interjection_is_only_in_tts_copy(self):
        source = ["嘿", "嘿，你终于来了。", "我在听。"]
        spoken = "".join(
            [part async for part in minimax_tts_text(_chunks(*source), model="speech-2.8-turbo")]
        )
        self.assertEqual(spoken, "(chuckle)，你终于来了。我在听。")
        self.assertEqual("".join(source), "嘿嘿，你终于来了。我在听。")
        self.assertEqual(spoken.count("(chuckle)"), 1)

    async def test_non_28_model_does_not_get_unsupported_markup(self):
        source = ["唉，", "我听到了。"]
        spoken = "".join(
            [part async for part in minimax_tts_text(_chunks(*source), model="speech-2.6-turbo")]
        )
        self.assertEqual(spoken, "".join(source))

    async def test_plain_speech_stays_plain(self):
        source = ["今天", "先聊聊", "你。"]
        spoken = "".join(
            [part async for part in minimax_tts_text(_chunks(*source), model="speech-2.8-hd")]
        )
        self.assertEqual(spoken, "".join(source))

    def test_no_arbitrary_or_repeated_markup(self):
        self.assertEqual(tts_only_interjection("嘿嘿，你来啦。"), ("(chuckle)，你来啦。", True))
        self.assertEqual(tts_only_interjection("天气很好哈哈。"), ("天气很好哈哈。", False))


if __name__ == "__main__":
    unittest.main()
