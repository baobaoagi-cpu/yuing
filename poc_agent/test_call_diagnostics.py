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

    def test_interrupted_speech_and_provider_error_are_separate(self):
        session = Session()
        with self.assertLogs("duanni.call", logging.INFO) as logs:
            _attach_call_diagnostics(session, "anothercall")
            speech = Speech()
            speech.interrupted = True
            session.handlers["speech_created"](
                SimpleNamespace(source="generate_reply", speech_handle=speech))
            speech.callback(speech)
            session.handlers["error"](
                SimpleNamespace(error=RuntimeError("secret"), source="tts"))
            session.handlers["close"](
                SimpleNamespace(reason=SimpleNamespace(value="error")))
        output = "\n".join(logs.output)
        self.assertIn("speech_interrupted=1", output)
        self.assertIn("provider_error=1", output)
        self.assertNotIn("secret", output)
