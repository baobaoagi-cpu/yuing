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
ACK_TOPIC = "dunni.emotion.ack.v1"
CAPABILITY_TOPIC = "dunni.emotion.capability.v1"
VIDEO_STATES = frozenset({
    "happy", "curious", "unhappy", "sad", "angry", "delighted", "playful_face",
})
STATES = VIDEO_STATES | {"neutral"}
TTL_MS = 8000
ACK_TIMEOUT_SECONDS = 9.0
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


def is_sensitive_text(text: str) -> bool:
    return isinstance(text, str) and bool(_SENSITIVE.search(text))


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
        self.browser_ready = False
        self.closed = False
        self._browser_identity: str | None = None
        self._ack_waiters: dict[int, asyncio.Future[str]] = {}
        self.results: dict[int, str] = {}
        self._explicit_next_reply = False
        self._explicit_at = 0.0
        self._explicit_this_reply = False
        self._voice_intent_next_reply: str | None = None
        self._voice_intent_at = 0.0
        self._sensitive_user_turn = False
        self.sequence = 0
        self.last_state: str | None = None
        self.last_at = float("-inf")
        self._recent_text = ""
        self._window_state: str | None = None
        self._pending: set[asyncio.Task] = set()
        if hasattr(room, "on"):
            room.on("data_received", self._on_data)
            room.on("participant_disconnected", self._on_participant_disconnected)

    def _on_participant_disconnected(self, participant: Any) -> None:
        if getattr(participant, "identity", None) != self._browser_identity:
            return
        self.browser_ready = False
        for waiter in self._ack_waiters.values():
            if not waiter.done():
                waiter.set_result("failed")

    def _on_data(self, packet: Any) -> None:
        topic = getattr(packet, "topic", None)
        if self.closed or topic not in {ACK_TOPIC, CAPABILITY_TOPIC}:
            return
        participant = getattr(packet, "participant", None)
        identity = getattr(participant, "identity", None)
        remotes = getattr(self.room, "remote_participants", {})
        if (not isinstance(identity, str) or identity not in remotes or len(remotes) != 1 or
                identity == getattr(self.room.local_participant, "identity", None) or
                (self._browser_identity is not None and identity != self._browser_identity)):
            return
        data = getattr(packet, "data", None)
        if not isinstance(data, bytes) or len(data) > 1024:
            return
        try:
            message = json.loads(data)
        except (UnicodeDecodeError, ValueError):
            return
        if not isinstance(message, dict) or message.get("call_id") != self.call_id:
            return
        if topic == CAPABILITY_TOPIC:
            if set(message) == {"call_id", "enabled"} and type(message["enabled"]) is bool:
                self._browser_identity = identity
                self.browser_ready = message["enabled"]
                if not self.browser_ready:
                    for waiter in self._ack_waiters.values():
                        if not waiter.done():
                            waiter.set_result("skipped")
            return
        if (set(message) != {"call_id", "sequence", "status"} or not self.browser_ready or
                type(message["sequence"]) is not int or
                not isinstance(message["status"], str) or
                message["status"] not in {"presented", "skipped", "failed"}):
            return
        waiter = self._ack_waiters.get(message["sequence"])
        if waiter is not None and not waiter.done():
            waiter.set_result(message["status"])

    def set_voice_intent(self, intent: str) -> None:
        self._voice_intent_next_reply = intent
        self._voice_intent_at = time.monotonic()

    def observe_user_text(self, text: str) -> None:
        # Ephemeral per-turn signal only. Never retain or log the transcript.
        self._sensitive_user_turn = is_sensitive_text(text)
        # A newly finalized caller turn supersedes any expression selected for
        # a reply that never started. This is conservative if STT arrives late:
        # losing an effect is safer than applying the old mood to a new turn.
        self._voice_intent_next_reply = None
        self._explicit_next_reply = False
        # Caller speech interrupts an expressive clip, even if that clip was
        # already presented. Neutral is immediate and bypasses the cooldown.
        # Only one reset is needed until another expressive state is chosen.
        if self.last_state not in (None, "neutral"):
            self.request_state("neutral")

    def take_voice_intent(self) -> str | None:
        intent = self._voice_intent_next_reply
        self._voice_intent_next_reply = None
        return intent if time.monotonic() - self._voice_intent_at <= 12 else None

    def request_state(self, state: str, *, explicit: bool = False) -> str:
        if state not in STATES:
            return "invalid"
        if self.closed or not self.ready or not self.browser_ready:
            return "unavailable"
        if state != "neutral" and self._sensitive_user_turn:
            return "skipped"
        now = time.monotonic()
        if state != "neutral" and now - self.last_at < COOLDOWN_SECONDS:
            return "skipped"
        if explicit:
            self._explicit_next_reply = True
            self._explicit_at = now
        self.last_state = state
        self.last_at = now
        self.sequence += 1
        sequence = self.sequence
        packet = make_packet(self.call_id, sequence, state, int(time.time() * 1000))
        self.results[sequence] = "accepted"
        logger.info("call_stage expression_video sequence=%d state=%s result=accepted", sequence, state)
        # Keep result history bounded and anonymous.
        if len(self.results) > 20:
            self.results.pop(next(iter(self.results)))
        task = asyncio.create_task(self._publish_and_wait(packet, sequence))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)
        return "accepted"

    def latest_result(self) -> tuple[int, str]:
        if not self.results:
            return 0, "unavailable"
        sequence = next(reversed(self.results))
        return sequence, self.results[sequence]

    def start_reply(self) -> None:
        """Discard text from the previous assistant reply, preserving call order."""
        self._recent_text = ""
        self._window_state = None
        self._explicit_this_reply = self._explicit_next_reply and time.monotonic() - self._explicit_at <= 12
        self._explicit_next_reply = False

    def on_assistant_part(self, text: str) -> None:
        """Run synchronously on the TTS source stream; publish asynchronously."""
        if not self.ready or not self.browser_ready:
            return
        if not isinstance(text, str):
            return
        # LiveKit may split a sentence mid-word. Keep a bounded window of
        # *assistant output only*; never inspect the user's transcript.
        self._recent_text = (self._recent_text + text)[-LOOKBACK_CHARS:]
        state = choose_visual_state(self._recent_text)
        if self._explicit_this_reply and state != "neutral":
            return
        if state is None:
            self._window_state = None
            return
        if state == self._window_state:
            return
        self._window_state = state
        self.request_state(state)

    async def _publish_and_wait(self, packet: bytes, sequence: int) -> None:
        if self.closed:
            self.results[sequence] = "failed"
            return
        waiter: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._ack_waiters[sequence] = waiter
        try:
            await self.room.local_participant.publish_data(
                packet, reliable=True, topic=TOPIC,
            )
            status = await asyncio.wait_for(waiter, ACK_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            logger.info("call_stage visual_emotion_ack_timeout")
            status = "failed"
        except Exception as exc:
            # SDK errors may include provider details; log only the type.
            logger.warning("call_stage visual_emotion_delivery_failed error_type=%s", type(exc).__name__)
            status = "failed"
        except asyncio.CancelledError:
            status = "failed"
        finally:
            self._ack_waiters.pop(sequence, None)
        self.results[sequence] = status
        logger.info("call_stage expression_video sequence=%d result=%s", sequence, status)

    async def close(self) -> None:
        self.ready = False
        self.browser_ready = False
        self.closed = True
        for waiter in self._ack_waiters.values():
            if not waiter.done():
                waiter.set_result("failed")
        for task in self._pending:
            task.cancel()
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)
        if hasattr(self.room, "off"):
            try:
                self.room.off("data_received", self._on_data)
                self.room.off("participant_disconnected", self._on_participant_disconnected)
            except ValueError:
                pass
