"""Check the deployed STT configuration against the installed LiveKit SDK."""

import unittest

from livekit.agents import inference

from poc_agent.agent import STT_OPTIONS


class SttConfigTest(unittest.TestCase):
    def test_mandarin_and_agent_name_keyterm_are_accepted_by_sdk(self):
        # Dummy credentials only construct the SDK object; this makes no API call.
        stt = inference.STT(
            **STT_OPTIONS, api_key="test-key", api_secret="test-secret"
        )
        self.assertEqual(stt._opts.model, "deepgram/nova-3")
        self.assertEqual(stt._opts.language, "zh-CN")
        self.assertEqual(stt._opts.extra_kwargs,
                         {"keyterm": ["陈端倪"]})
        self.assertTrue(stt.capabilities.keyterms)


if __name__ == "__main__":
    unittest.main()
