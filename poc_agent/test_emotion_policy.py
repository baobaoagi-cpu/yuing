"""Offline checks for the TTS-only emotion markup boundary."""

import unittest

from poc_agent.emotion_policy import choose_tts_cue, minimax_tts_text, tts_only_interjection


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
        decisions = []
        spoken = "".join(
            [part async for part in minimax_tts_text(_chunks(*source), model="speech-2.8-hd", on_decision=decisions.append)]
        )
        self.assertEqual(spoken, "".join(source))
        self.assertEqual(decisions, [None])

    def test_no_arbitrary_or_repeated_markup(self):
        self.assertEqual(tts_only_interjection("嘿嘿，你来啦。"), ("(chuckle)，你来啦。", True))
        self.assertEqual(tts_only_interjection("天气很好哈哈。"), ("天气很好哈哈。", False))

    async def test_toolbox_chooses_inferred_and_mid_reply_cues(self):
        source = ["今天", "开个玩笑，", "你别当真。", "咦，", "原来你在这。"]
        decisions = []
        spoken = "".join(
            [part async for part in minimax_tts_text(
                _chunks(*source), model="speech-2.8-turbo", on_decision=decisions.append
            )]
        )
        self.assertEqual(spoken, "(chuckle)今天开个玩笑，你别当真。(gasps)，原来你在这。")
        self.assertEqual("".join(source), "今天开个玩笑，你别当真。咦，原来你在这。")
        self.assertEqual(decisions, ["chuckle", "gasps"])

    async def test_cue_cap_and_long_unpunctuated_stream(self):
        source = ["哈哈，", "嘿嘿，", "哇，", "a" * 80]
        spoken = "".join([part async for part in minimax_tts_text(
            _chunks(*source), model="speech-2.8-hd"
        )])
        self.assertEqual(spoken, "(chuckle)，(chuckle)，哇，" + "a" * 80)

    async def test_unsupported_model_is_unchanged_and_reported(self):
        decisions = []
        source = ["哈哈，", "你来啦。"]
        spoken = "".join([part async for part in minimax_tts_text(
            _chunks(*source), model="speech-2.6-turbo", on_decision=decisions.append
        )])
        self.assertEqual(spoken, "".join(source))
        self.assertEqual(decisions, [None])

    def test_toolbox_conservative_for_sensitive_context(self):
        self.assertEqual(choose_tts_cue("你说你很难过，我在听。"), ("你说你很难过，我在听。", None))

    async def test_phrase_after_old_48_character_slice_is_detected(self):
        source = ["我们刚刚聊到那次散步，我还记得你说过那条路上有一家很小的书店，门口还放着两盆花。", "这也太巧了！"]
        decisions = []
        spoken = "".join([part async for part in minimax_tts_text(
            _chunks(*source), model="speech-2.8-turbo", on_decision=decisions.append
        )])
        self.assertEqual(spoken, source[0] + "(gasps)这也太巧了！")
        self.assertEqual([cue for cue in decisions if cue], ["gasps"])

    async def test_sensitive_turn_suppresses_later_cheerful_cue(self):
        source = ["听到你说自己很难过，我先陪你。", "逗你的，哈哈。"]
        spoken = "".join([part async for part in minimax_tts_text(
            _chunks(*source), model="speech-2.8-turbo"
        )])
        self.assertEqual(spoken, "".join(source))

    async def test_tts_markup_not_sent_to_transcript_callback(self):
        source = ["这也太", "巧了，", "你来了。"]
        transcript = []
        spoken = "".join([part async for part in minimax_tts_text(
            _chunks(*source), model="speech-2.8-turbo", on_source_part=transcript.append
        )])
        self.assertEqual(spoken, "(gasps)这也太巧了，你来了。")
        self.assertEqual("".join(transcript), "".join(source))

    def test_quoted_or_questioned_phrase_is_not_acted_out(self):
        for source in ("“开个玩笑”是什么意思？", "你为什么说逗你的？", "别开个玩笑。"):
            self.assertEqual(choose_tts_cue(source), (source, None))


if __name__ == "__main__":
    unittest.main()
