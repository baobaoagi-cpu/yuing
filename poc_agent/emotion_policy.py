"""Bounded, TTS-only emotion cues for MiniMax Speech 2.8 calls.

The LLM stream remains the source of transcripts and history. This adapter
modifies only the copy passed to the voice synthesizer. No second model call
or mutable provider-wide emotion setting is involved.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterable, AsyncIterator, Callable


SUPPORTED_MODELS = frozenset({"speech-2.8-hd", "speech-2.8-turbo"})
_MAX_LOOKAHEAD = 128
_MAX_CUES_PER_REPLY = 2
_SENTENCE_END = frozenset("。！？!?，,；;\n")
_SENSITIVE = re.compile(r"(?:自杀|自傷|自伤|死亡|去世|生病|受伤|受傷|难过|難過|崩溃|崩潰|哭了|痛苦|被欺负|被欺負)")
_QUOTE_OR_QUESTION = re.compile(r"[“”‘’\"'《》]|(?:什么是|什麼是|为什么|為什麼|是不是|怎么|怎麼)")

# All tags below appear in the official MiniMax Speech 2.8 interjection list.
_DIRECT_CUES = (
    (re.compile(r"^(\s*)(?:哈哈哈|哈哈哈哈)([，,。！？!?]?)(?=\S|$)"), "laughs"),
    (re.compile(r"^(\s*)(?:嘿嘿|哈哈|嘻嘻)([，,。！？!?]?)(?=\S|$)"), "chuckle"),
    (re.compile(r"^(\s*)(?:唉|哎呀)([，,。！？!?]?)(?=\S|$)"), "sighs"),
    (re.compile(r"^(\s*)(?:咦|哇)([，,。！？!?]?)(?=\S|$)"), "gasps"),
    (re.compile(r"^(\s*)(?:嗯……|嗯\.\.\.|呃……)([，,。！？!?]?)(?=\S|$)"), "emm"),
)

# Clear intent phrases can suggest a sound even without an opening filler.
# General positive/negative topics do not trigger a cue: that would overact.
_PHRASE_CUES = (
    (re.compile(r"(?:逗你的|我开个玩笑|今天开个玩笑)"), "chuckle"),
    (re.compile(r"(?:真没想到|居然是这样|这也太巧了)"), "gasps"),
    (re.compile(r"(?:还好没事|总算松了口气)"), "exhale"),
)


def choose_tts_cue(text: str) -> tuple[str, str | None]:
    """Return TTS-only text and selected cue, without changing the transcript."""
    if _SENSITIVE.search(text) or _QUOTE_OR_QUESTION.search(text):
        return text, None
    for pattern, category in _DIRECT_CUES:
        match = pattern.match(text)
        if match:
            return f"{match.group(1)}({category}){match.group(2)}{text[match.end():]}", category
    for pattern, category in _PHRASE_CUES:
        if pattern.search(text):
            whitespace = len(text) - len(text.lstrip())
            return f"{text[:whitespace]}({category}){text[whitespace:]}", category
    return text, None


def tts_only_interjection(text: str) -> tuple[str, bool]:
    """Compatibility wrapper for callers testing the TTS-only boundary."""
    altered, category = choose_tts_cue(text)
    return altered, category is not None


def _ready_parts(text: str, *, flush: bool = False) -> tuple[list[str], str]:
    """Release clauses, retaining enough context for phrases split across LLM chunks."""
    parts: list[str] = []
    while text:
        boundary = next((i + 1 for i, ch in enumerate(text[:_MAX_LOOKAHEAD]) if ch in _SENTENCE_END), None)
        if boundary is None:
            if len(text) < _MAX_LOOKAHEAD and not flush:
                break
            boundary = min(len(text), _MAX_LOOKAHEAD)
        parts.append(text[:boundary])
        text = text[boundary:]
    return parts, text


async def minimax_tts_text(
    text: AsyncIterable[str],
    *,
    model: str,
    on_decision: Callable[[str | None], None] | None = None,
    on_source_part: Callable[[str], None] | None = None,
) -> AsyncIterator[str]:
    """Select at most two cues per reply, only for MiniMax Speech 2.8.

    ``on_decision`` receives selected cue categories, or one ``None`` when
    the whole reply had no cue. Segment boundaries are not decisions.
    """
    if model not in SUPPORTED_MODELS:
        if on_decision:
            on_decision(None)
        async for chunk in text:
            if on_source_part:
                on_source_part(chunk)
            yield chunk
        return

    pending = ""
    cues_used = 0
    sensitive_reply = False

    def adapt(part: str) -> str:
        nonlocal cues_used, sensitive_reply
        if on_source_part:
            on_source_part(part)
        sensitive_reply = sensitive_reply or bool(_SENSITIVE.search(part))
        if cues_used < _MAX_CUES_PER_REPLY and not sensitive_reply:
            altered, category = choose_tts_cue(part)
        else:
            altered, category = part, None
        if category is not None:
            cues_used += 1
        if on_decision and category is not None:
            on_decision(category)
        return altered

    async for chunk in text:
        pending += chunk
        ready, pending = _ready_parts(pending)
        for part in ready:
            yield adapt(part)

    ready, pending = _ready_parts(pending, flush=True)
    for part in ready:
        yield adapt(part)
    if cues_used == 0 and on_decision:
        on_decision(None)
