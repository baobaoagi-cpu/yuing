"""Offline LiveKit lifecycle diagnostics contract; no audio/provider calls."""

import logging
from types import SimpleNamespace
from unittest import TestCase

from agent import _attach_call_diagnostics


class Session:
    def __init__(self):
        self.handlers = {}

    def on(self, name):
        def register(callback):
            self.handlers[name] = callback
            return callback
        return register


class Speech:
    id = "speech_test"
    interrupted = False

    def add_done_callback(self, callback):
        self.callback = callback

    def exception(self):
        return None


class DiagnosticsTest(TestCase):
    def test_counts_do_not_include_transcript(self):
        session = Session()
        with self.assertLogs("duanni.call", logging.INFO) as logs:
            _attach_call_diagnostics(session, "testcall")
            session.handlers["user_input_transcribed"](
                SimpleNamespace(is_final=True, transcript="private words"))
            speech = Speech()
            session.handlers["speech_created"](
                SimpleNamespace(source="generate_reply", speech_handle=speech))
            speech.callback(speech)
            session.handlers["close"](
                SimpleNamespace(reason=SimpleNamespace(value="user_initiated")))
        output = "\n".join(logs.output)
        self.assertIn("stt_final=1", output)
        self.assertIn("speech_queued=1", output)
        self.assertIn("speech_done=1", output)
        self.assertIn("call_ref=testcall", output)
        self.assertNotIn("private words", output)
