"""Bounded, room-bound drawing commands for the active phone call.

This channel is visual only. Drawing packets and acknowledgements never enter
the conversation transcript, and a publish success is not a render success.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from typing import Any


TOPIC = "dunni.sketch.v1"
ACK_TOPIC = "dunni.sketch.ack.v1"
CAPABILITY_TOPIC = "dunni.sketch.capability.v1"
KINDS = frozenset({"sun", "heart", "text", "strokes"})
BRUSHES = frozenset({"solid", "glow", "soft"})
_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
TTL_MS = 5000
ACK_TIMEOUT_SECONDS = 1.5
MAX_ACTIVE = 3
MAX_PACKET_BYTES = 8192
_TEXT = re.compile(r"^[^<>\x00-\x1f\x7f]{1,16}$")


def _fraction(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1


def validate_drawing(kind: str, anchor: dict, size: float, color: str,
                     duration_ms: int, text: str = "", strokes: list | None = None,
                     brush: str = "solid") -> dict:
    if not isinstance(kind, str) or kind not in KINDS or not isinstance(color, str) or not _COLOR.fullmatch(color):
        raise ValueError("unsupported drawing kind or color")
    if not isinstance(brush, str) or brush not in BRUSHES:
        raise ValueError("unsupported drawing brush")
    if not isinstance(anchor, dict) or set(anchor) != {"x", "y"} or not all(_fraction(anchor[k]) for k in ("x", "y")):
        raise ValueError("anchor must have screen-relative x and y")
    if not _fraction(size) or not 0.05 <= size <= 0.35:
        raise ValueError("size must be 0.05 to 0.35")
    if type(duration_ms) is not int or not 500 <= duration_ms <= 12000:
        raise ValueError("duration must be 500 to 12000 ms")
    if kind == "text":
        if not isinstance(text, str) or not _TEXT.fullmatch(text) or len(text.encode("utf-8")) > 48:
            raise ValueError("text must be short and contain no markup")
    elif text:
        raise ValueError("text is only valid for a text drawing")
    if kind == "strokes":
        if not isinstance(strokes, list) or not 1 <= len(strokes) <= 12:
            raise ValueError("strokes must contain 1 to 12 paths")
        if sum(len(path) for path in strokes if isinstance(path, list)) > 256:
            raise ValueError("too many drawing points")
        for path in strokes:
            if not isinstance(path, list) or not 2 <= len(path) <= 64:
                raise ValueError("each stroke needs 2 to 64 points")
            if any(not isinstance(point, list) or len(point) != 2 or not all(_fraction(v) for v in point) for point in path):
                raise ValueError("points must use relative x and y")
    elif strokes not in (None, []):
        raise ValueError("strokes are only valid for a freehand drawing")
    drawing = {"kind": kind, "anchor": {"x": anchor["x"], "y": anchor["y"]},
               "size": size, "color": color, "duration_ms": duration_ms, "brush": brush}
    if kind == "text":
        drawing["text"] = text
    if kind == "strokes":
        drawing["strokes"] = strokes
    return drawing


class CallSketch:
    def __init__(self, room: Any, call_id: str, *, ack_timeout: float = ACK_TIMEOUT_SECONDS) -> None:
        if not isinstance(call_id, str) or not call_id or len(call_id) > 128 or call_id != getattr(room, "name", None):
            raise ValueError("drawing call must match the room")
        self.room = room
        self.call_id = call_id
        self.ack_timeout = ack_timeout
        self.sequence = 0
        self.closed = False
        self._pending: dict[int, asyncio.Future[str]] = {}
        self._active_until: list[float] = []
        self._browser_identity: str | None = None
        self.ready = False
        room.on("data_received", self._on_data)
        room.on("participant_disconnected", self._on_participant_disconnected)

    def _on_participant_disconnected(self, participant: Any) -> None:
        if getattr(participant, "identity", None) != self._browser_identity:
            return
        self.ready = False
        for future in self._pending.values():
            if not future.done():
                future.set_result("failed")

    def _on_data(self, packet: Any) -> None:
        topic = getattr(packet, "topic", None)
        if self.closed or topic not in {ACK_TOPIC, CAPABILITY_TOPIC}:
            return
        participant = getattr(packet, "participant", None)
        identity = getattr(participant, "identity", None)
        # A server-sent or unrelated participant's packet cannot confirm a
        # drawing. Bind to the first browser that acknowledges this call.
        remotes = getattr(self.room, "remote_participants", {})
        if (not isinstance(identity, str) or not identity or
                identity == getattr(self.room.local_participant, "identity", None) or
                identity not in remotes or len(remotes) != 1):
            return
        if self._browser_identity is not None and identity != self._browser_identity:
            return
        data = getattr(packet, "data", None)
        if not isinstance(data, bytes) or len(data) > 1024:
            return
        try:
            ack = json.loads(data)
        except (UnicodeDecodeError, ValueError):
            return
        if topic == CAPABILITY_TOPIC:
            if (isinstance(ack, dict) and set(ack) == {"call_id", "enabled"}
                    and ack["call_id"] == self.call_id and type(ack["enabled"]) is bool):
                self._browser_identity = identity
                self.ready = ack["enabled"]
                if not self.ready:
                    for future in self._pending.values():
                        if not future.done():
                            future.set_result("skipped")
            return
        if not isinstance(ack, dict) or set(ack) != {"call_id", "sequence", "status"}:
            return
        if (ack["call_id"] != self.call_id or type(ack["sequence"]) is not int or
                not isinstance(ack["status"], str) or ack["status"] not in {"rendered", "skipped", "failed"}):
            return
        future = self._pending.get(ack["sequence"])
        if future is None or future.done() or not self.ready or identity != self._browser_identity:
            return
        self._browser_identity = identity
        future.set_result(ack["status"])

    async def draw(self, *, kind: str, anchor: dict, size: float, color: str,
                   duration_ms: int = 8000, text: str = "", strokes: list | None = None,
                   brush: str = "solid") -> str:
        if self.closed:
            return "绘画不可用：通话已结束。"
        if not self.ready:
            return "绘画暂不可用：手机画布尚未准备好或已关闭。"
        remotes = getattr(self.room, "remote_participants", {})
        if len(remotes) != 1:
            return "绘画不可用：当前没有唯一的手机参与者。"
        try:
            drawing = validate_drawing(kind, anchor, size, color, duration_ms, text, strokes, brush)
        except ValueError as exc:
            return f"绘画参数无效：{exc}。"
        now = time.monotonic()
        self._active_until = [expiry for expiry in self._active_until if expiry > now]
        if len(self._active_until) + len(self._pending) >= MAX_ACTIVE:
            return "画面已有三幅图，请稍后再画。"
        self.sequence += 1
        sequence = self.sequence
        payload = {"call_id": self.call_id, "sequence": sequence,
                   "issued_at": int(time.time() * 1000), "ttl_ms": TTL_MS,
                   **drawing}
        packet = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(packet) > MAX_PACKET_BYTES:
            return "绘画内容太多，请简化笔画。"
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._pending[sequence] = future
        try:
            await self.room.local_participant.publish_data(packet, reliable=True, topic=TOPIC)
            status = await asyncio.wait_for(future, self.ack_timeout)
        except asyncio.TimeoutError:
            return "手机没有确认画面，不能说已经画出来。"
        except Exception:
            return "绘画指令未送达手机，不能说已经画出来。"
        finally:
            self._pending.pop(sequence, None)
        if status == "rendered":
            self._active_until.append(time.monotonic() + duration_ms / 1000)
            return "手机已确认画面显示。"
        if status == "skipped":
            return "手机略过了绘画（可能已关闭画面或通话中断），不要说画好了。"
        return "手机绘画失败，不要说画好了。"

    def close(self) -> None:
        self.closed = True
        self.ready = False
        for future in self._pending.values():
            if not future.done():
                future.set_result("failed")
        self._pending.clear()
        try:
            self.room.off("data_received", self._on_data)
            self.room.off("participant_disconnected", self._on_participant_disconnected)
        except (AttributeError, ValueError):
            pass
