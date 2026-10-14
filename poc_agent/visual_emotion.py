"""Conservative, room-bound visual emotion cues for the call UI.

These cues are separate from MiniMax TTS interjections. Only a small event is
sent to the authenticated call room; no transcript, prompt, or user detail is
included in the packet or logs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any


TOPIC = "dunni.emotion.v1"
VIDEO_STATES = frozenset({
    "happy", "curious", "unhappy", "sad", "angry", "delighted", "playful_face",
})
STATES = VIDEO_STATES | {"neutral"}
TTL_MS = 5000
COOLDOWN_SECONDS = 8.0
LOOKBACK_CHARS = 80
logger = logging.getLogger("duanni.visual_emotion")

# A sensitive topic must reset the visual even if a happy cue was shown just
# before it. Do not infer emotion from the user's words or a transcript.
_SENSITIVE = re.compile(
    r"自杀|自残|死亡|去世|丧礼|葬礼|重病|癌症|受伤|流血|虐待|暴力|"
    r"分手|离婚|失业|骚扰|侵犯|隐私|密码|金钥|密钥|提示词|人格宪法|"
    r"system\s*prompt|api\s*key",
    re.IGNORECASE,
)
_CUES = (
    ("playful_face", re.compile(r"逗你的|开个玩笑|骗你的啦|故意逗你")),
    ("delighted", re.compile(r"太棒了|太开心了|真替你高兴|我也好开心")),
    ("happy", re.compile(r"真好呀|好喜欢|真不错|好开心")),
    ("curious", re.compile(r"真的吗|真的假的|这倒有趣|我有点好奇")),
    ("unhappy", re.compile(r"我有点不开心|我有点失望")),
    ("sad", re.compile(r"我有点难过|我有些难过")),
    ("angry", re.compile(r"我有点生气|这让我生气")),
)


def choose_visual_state(assistant_text: str) -> str | None:
    """Classify only a clear cue in Dunni's own output; None means neutral."""
    if not isinstance(assistant_text, str) or _SENSITIVE.search(assistant_text):
        return "neutral" if isinstance(assistant_text, str) and _SENSITIVE.search(assistant_text) else None
    for state, pattern in _CUES:
        if pattern.search(assistant_text):
            return state
    return None


def make_packet(call_id: str, sequence: int, state: str, issued_at_ms: int) -> bytes:
    if not isinstance(call_id, str) or not call_id or len(call_id) > 128:
        raise ValueError("invalid call id")
    if not isinstance(sequence, int) or sequence < 1 or state not in STATES:
        raise ValueError("invalid visual emotion event")
    return json.dumps({
        "call_id": call_id,
        "sequence": sequence,
        "state": state,
        "issued_at": issued_at_ms,
        "ttl_ms": TTL_MS,
    }, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


class VisualEmotionEmitter:
    def __init__(self, room: Any, call_id: str) -> None:
        self.room = room
        self.call_id = call_id
        self.ready = False
        self.sequence = 0
        self.last_state: str | None = None
        self.last_at = float("-inf")
        self._recent_text = ""
        self._window_state: str | None = None
        self._pending: set[asyncio.Task] = set()

    def start_reply(self) -> None:
        """Discard text from the previous assistant reply, preserving call order."""
        self._recent_text = ""
        self._window_state = None

    def on_assistant_part(self, text: str) -> None:
        """Run synchronously on the TTS source stream; publish asynchronously."""
        if not self.ready:
            return
        if not isinstance(text, str):
            return
        # LiveKit may split a sentence mid-word. Keep a bounded window of
        # *assistant output only*; never inspect the user's transcript.
        self._recent_text = (self._recent_text + text)[-LOOKBACK_CHARS:]
        state = choose_visual_state(self._recent_text)
        if state is None:
            self._window_state = None
            return
        if state == self._window_state:
            return
        self._window_state = state
        now = time.monotonic()
        # Neutral safety reset may interrupt an expressive clip immediately.
        if state != "neutral" and now - self.last_at < COOLDOWN_SECONDS:
            return
        if state == self.last_state and now - self.last_at < COOLDOWN_SECONDS:
            return
        self.last_state = state
        self.last_at = now
        self.sequence += 1
        packet = make_packet(self.call_id, self.sequence, state, int(time.time() * 1000))
        task = asyncio.create_task(self._publish(packet))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _publish(self, packet: bytes) -> None:
        try:
            await self.room.local_participant.publish_data(
                packet, reliable=True, topic=TOPIC,
            )
        except Exception as exc:
            # SDK errors may include provider details; log only the type.
            logger.warning("call_stage visual_emotion_delivery_failed error_type=%s", type(exc).__name__)

    async def close(self) -> None:
        self.ready = False
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)
