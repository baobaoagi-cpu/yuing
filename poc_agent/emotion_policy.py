"""Conservative MiniMax Speech 2.8 interjections for audio only.

The LLM and transcript retain plain spoken text. Only this TTS input adapter
may introduce provider markup, so the markup cannot enter chat history or
on-screen captions through the normal LiveKit transcript path.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterable, AsyncIterator


_INTERJECTIONS = (
    (re.compile(r"^(\s*)(?:嘿嘿|哈哈)([，,。！？!?]?)(?=\S|$)"), r"\1(chuckle)\2"),
    (re.compile(r"^(\s*)唉([，,。!?！？]?)(?=\S|$)"), r"\1(sighs)\2"),
    (re.compile(r"^(\s*)咦([，,!?！？]?)(?=\S|$)"), r"\1(gasps)\2"),
)
_SENTENCE_END = frozenset("。！？!?，,")
_MAX_LOOKAHEAD = 80


def tts_only_interjection(text: str) -> tuple[str, bool]:
    """Map a natural, utterance-initial sound to one supported TTS tag."""
    for pattern, replacement in _INTERJECTIONS:
        altered, count = pattern.subn(replacement, text, count=1)
        if count:
            return altered, True
    return text, False


async def minimax_tts_text(
    text: AsyncIterable[str], *, model: str
) -> AsyncIterator[str]:
    """Inject at most one nonverbal cue per reply; keep latency bounded.

    Speech 2.8 officially supports these tags. On other models, pass every
    chunk through exactly as received. The adapter never exposes its output
    to the LLM context or the transcript stream.
    """
    if model not in {"speech-2.8-hd", "speech-2.8-turbo"}:
        async for chunk in text:
            yield chunk
        return

    pending = ""
    tagged = False
    async for chunk in text:
        if tagged:
            yield chunk
            continue
        pending += chunk
        if not any(ch in pending for ch in _SENTENCE_END) and len(pending) < _MAX_LOOKAHEAD:
            continue
        altered, tagged = tts_only_interjection(pending)
        yield altered
        pending = ""
    if pending:
        altered, _ = tts_only_interjection(pending) if not tagged else (pending, False)
        yield altered
