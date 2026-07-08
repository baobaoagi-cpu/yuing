import json
import logging
import os
import queue
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass, field


_VENDOR_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "vendor")
if os.path.isdir(_VENDOR_DIR) and _VENDOR_DIR not in sys.path:
    sys.path.insert(0, _VENDOR_DIR)

import websocket

from storage import ensure_meeting_session, save_meeting_chunk, save_meeting_live_slice


logger = logging.getLogger(__name__)


STREAM_SAMPLE_RATE = 16000
ROLLING_STREAM_MS = 25 * 60 * 1000
KEEPALIVE_IDLE_SEC = 3.0
HEARTBEAT_SEC = 10.0


def _deepgram_key() -> str:
    return os.environ.get("DEEPGRAM_API_KEY", "").strip()


def _bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    return str(v or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class LiveMeetingSession:
    session_id: str
    save_audio: bool = False
    sample_rate: int = STREAM_SAMPLE_RATE
    model: str = "nova-3"
    language: str = "zh-TW"
    lock: threading.RLock = field(default_factory=threading.RLock)
    subscribers: list = field(default_factory=list)
    ws: object | None = None
    ws_generation: int = 0
    current_stream_start_ms: int = 0
    current_stream_audio_ms: int = 0
    total_audio_ms: int = 0
    current_pcm: bytearray = field(default_factory=bytearray)
    current_pcm_start_ms: int = 0
    chunk_index: int = 0
    slice_index: int = 0
    last_final_end_ms: int = 0
    last_send_at: float = 0.0
    created_at: float = field(default_factory=time.time)
    stopped: bool = False
    last_error: str = ""

    def _mark_stream_dead(self, generation: int, message: str = ""):
        with self.lock:
            if generation != self.ws_generation:
                return False
            self.last_error = str(message or self.last_error or "").strip()
            ws = self.ws
            self.ws = None
            self.ws_generation += 1
            current_audio_ms = self.current_stream_audio_ms
            total_audio_ms = self.total_audio_ms
        try:
            if ws:
                ws.close()
        except Exception:
            pass
        logger.warning(
            "yuling_live_stt stream_dead session=%s generation=%s current_audio_ms=%s total_audio_ms=%s reason=%s",
            self.session_id,
            generation,
            current_audio_ms,
            total_audio_ms,
            self.last_error or "",
        )
        self.publish({
            "type": "status",
            "status": "stream-dead",
            "stream_index": generation,
            "current_audio_ms": current_audio_ms,
            "total_audio_ms": total_audio_ms,
            "reason": self.last_error or "",
        })
        return True

    def publish(self, event: dict):
        payload = dict(event or {})
        payload.setdefault("session_id", self.session_id)
        payload.setdefault("ts", time.time())
        dead = []
        for q in list(self.subscribers):
            try:
                q.put_nowait(payload)
            except Exception:
                dead.append(q)
        for q in dead:
            try:
                self.subscribers.remove(q)
            except ValueError:
                pass

    def subscribe(self):
        q = queue.Queue()
        with self.lock:
            self.subscribers.append(q)
        q.put({
            "type": "ready",
            "session_id": self.session_id,
            "save_audio": self.save_audio,
        })
        return q

    def unsubscribe(self, q):
        with self.lock:
            try:
                self.subscribers.remove(q)
            except ValueError:
                pass

    def _stream_url(self) -> str:
        params = {
            "model": self.model,
            "language": self.language,
            "encoding": "linear16",
            "sample_rate": str(self.sample_rate),
            "channels": "1",
            "interim_results": "true",
            "punctuate": "true",
            "smart_format": "true",
            "endpointing": "500",
            "vad_events": "true",
        }
        return "wss://api.deepgram.com/v1/listen?" + urllib.parse.urlencode(params)

    def _connect_stream(self, base_start_ms: int):
        key = _deepgram_key()
        if not key:
            raise RuntimeError("missing DEEPGRAM_API_KEY")
        ws = websocket.create_connection(
            self._stream_url(),
            header=[f"Authorization: Token {key}"],
            timeout=10,
            enable_multithread=True,
        )
        self.ws = ws
        self.ws_generation += 1
        gen = self.ws_generation
        self.current_stream_start_ms = max(0, int(base_start_ms or 0))
        self.current_stream_audio_ms = 0
        self.current_pcm_start_ms = self.current_stream_start_ms
        self.last_send_at = time.time()
        threading.Thread(target=self._recv_loop, args=(gen,), daemon=True).start()
        threading.Thread(target=self._keepalive_loop, args=(gen,), daemon=True).start()
        logger.info(
            "yuling_live_stt stream_open session=%s generation=%s base_start_ms=%s sample_rate=%s save_audio=%s",
            self.session_id,
            gen,
            self.current_stream_start_ms,
            self.sample_rate,
            self.save_audio,
        )
        self.publish({
            "type": "status",
            "status": "stream-open",
            "stream_index": gen,
            "base_start_ms": self.current_stream_start_ms,
        })

    def start(self):
        with self.lock:
            self._connect_stream(base_start_ms=0)

    def _keepalive_loop(self, generation: int):
        while True:
            time.sleep(KEEPALIVE_IDLE_SEC)
            with self.lock:
                if generation != self.ws_generation or self.stopped or not self.ws:
                    return
                if time.time() - self.last_send_at < KEEPALIVE_IDLE_SEC:
                    continue
                ws = self.ws
            try:
                ws.send(json.dumps({"type": "KeepAlive"}))
            except Exception:
                return

    def _recv_loop(self, generation: int):
        while True:
            with self.lock:
                if generation != self.ws_generation or self.stopped or not self.ws:
                    return
                ws = self.ws
            try:
                msg = ws.recv()
            except Exception as err:
                message = str(err)
                self._mark_stream_dead(generation, message)
                self.publish({"type": "error", "message": message})
                return
            if not msg:
                continue
            try:
                payload = json.loads(msg)
            except Exception:
                continue
            self._handle_message(payload)

    def _range_from_payload(self, payload: dict) -> tuple[int, int]:
        start_sec = payload.get("start")
        dur_sec = payload.get("duration")
        if isinstance(start_sec, (int, float)) and isinstance(dur_sec, (int, float)):
            start_ms = self.current_stream_start_ms + int(max(0.0, start_sec) * 1000)
            end_ms = start_ms + int(max(0.0, dur_sec) * 1000)
        else:
            end_ms = max(self.total_audio_ms, self.last_final_end_ms)
            start_ms = self.last_final_end_ms or max(0, end_ms - 1800)
        if end_ms < start_ms:
            end_ms = start_ms
        return start_ms, end_ms

    def _handle_message(self, payload: dict):
        if not isinstance(payload, dict):
            return
        alt = (((payload.get("channel") or {}).get("alternatives") or [{}])[0] or {})
        transcript = str((alt.get("transcript") or "")).strip()
        if not transcript:
            return
        start_ms, end_ms = self._range_from_payload(payload)
        if _bool(payload.get("is_final")):
            self.slice_index += 1
            self.last_final_end_ms = max(self.last_final_end_ms, end_ms)
            saved = save_meeting_live_slice(
                self.session_id,
                self.slice_index,
                start_ms,
                end_ms,
                transcript=transcript,
            )
            self.publish({
                "type": "final",
                "slice_index": self.slice_index,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "transcript": transcript,
                "saved": bool(saved),
            })
        else:
            self.publish({
                "type": "interim",
                "start_ms": start_ms,
                "end_ms": end_ms,
                "transcript": transcript,
            })

    def _flush_audio_chunk(self, end_ms: int):
        if not self.save_audio or not self.current_pcm:
            self.current_pcm = bytearray()
            self.current_pcm_start_ms = end_ms
            return
        self.chunk_index += 1
        save_meeting_chunk(
            session_id=self.session_id,
            chunk_index=self.chunk_index,
            start_ms=self.current_pcm_start_ms,
            end_ms=end_ms,
            audio_bytes=bytes(self.current_pcm),
            mime_type="audio/pcm",
        )
        self.current_pcm = bytearray()
        self.current_pcm_start_ms = end_ms

    def _close_ws(self):
        ws = self.ws
        self.ws = None
        if not ws:
            return
        try:
            ws.send(json.dumps({"type": "CloseStream"}))
        except Exception:
            pass
        try:
            ws.close()
        except Exception:
            pass

    def rollover_if_needed(self, next_end_ms: int):
        if next_end_ms - self.current_stream_start_ms < ROLLING_STREAM_MS:
            return
        end_ms = max(self.total_audio_ms, next_end_ms)
        self._flush_audio_chunk(end_ms)
        self._close_ws()
        self.publish({
            "type": "status",
            "status": "rollover",
            "at_ms": end_ms,
        })
        self._connect_stream(base_start_ms=end_ms)

    def append(self, audio_bytes: bytes, start_ms: int, end_ms: int):
        if not audio_bytes:
            return {"ok": False, "error": "empty audio"}
        with self.lock:
            if self.stopped:
                return {"ok": False, "error": "stopped"}
            if not self.ws:
                logger.info(
                    "yuling_live_stt append_reconnect session=%s start_ms=%s end_ms=%s last_error=%s",
                    self.session_id,
                    start_ms,
                    end_ms,
                    self.last_error or "",
                )
                self.publish({
                    "type": "status",
                    "status": "append-reconnect",
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "reason": self.last_error or "",
                })
                self._connect_stream(base_start_ms=start_ms)
            self.rollover_if_needed(end_ms)
            self.total_audio_ms = max(self.total_audio_ms, end_ms)
            self.current_stream_audio_ms = max(0, end_ms - self.current_stream_start_ms)
            if self.save_audio:
                self.current_pcm.extend(audio_bytes)
            ws = self.ws
            self.last_send_at = time.time()
        try:
            ws.send_binary(audio_bytes)
        except Exception as err:
            message = str(err)
            self._mark_stream_dead(self.ws_generation, message)
            logger.warning(
                "yuling_live_stt append_send_failed session=%s start_ms=%s end_ms=%s reason=%s",
                self.session_id,
                start_ms,
                end_ms,
                message,
            )
            self.publish({"type": "error", "message": message})
            return {"ok": False, "error": message}
        return {"ok": True, "session_id": self.session_id, "end_ms": end_ms}

    def stop(self):
        with self.lock:
            if self.stopped:
                return {"ok": True, "session_id": self.session_id}
            self.stopped = True
            end_ms = self.total_audio_ms
            self._flush_audio_chunk(end_ms)
            self._close_ws()
        logger.info(
            "yuling_live_stt session_stop session=%s end_ms=%s save_audio=%s",
            self.session_id,
            end_ms,
            self.save_audio,
        )
        self.publish({
            "type": "closed",
            "session_id": self.session_id,
            "end_ms": end_ms,
        })
        return {"ok": True, "session_id": self.session_id, "end_ms": end_ms}


class MeetingLiveSessionManager:
    def __init__(self):
        self.lock = threading.RLock()
        self.sessions: dict[str, LiveMeetingSession] = {}

    def start_session(self, session_id: str = "", save_audio: bool = False) -> dict:
        meta = ensure_meeting_session(session_id=session_id, save_audio=save_audio)
        if not meta:
            return {}
        sid = meta["session_id"]
        with self.lock:
            old = self.sessions.get(sid)
            if old:
                old.stop()
            session = LiveMeetingSession(session_id=sid, save_audio=bool(save_audio))
            self.sessions[sid] = session
        try:
            session.start()
        except Exception:
            with self.lock:
                if self.sessions.get(sid) is session:
                    self.sessions.pop(sid, None)
            return {}
        return {
            "session_id": sid,
            "save_audio": bool(save_audio),
            "sample_rate": session.sample_rate,
            "rollover_ms": ROLLING_STREAM_MS,
        }

    def get(self, session_id: str) -> LiveMeetingSession | None:
        with self.lock:
            return self.sessions.get(session_id)

    def append_audio(self, session_id: str, audio_bytes: bytes, start_ms: int, end_ms: int) -> dict:
        session = self.get(session_id)
        if not session:
            return {"ok": False, "error": "missing session"}
        return session.append(audio_bytes, max(0, int(start_ms or 0)), max(0, int(end_ms or 0)))

    def stop_session(self, session_id: str) -> dict:
        with self.lock:
            session = self.sessions.get(session_id)
        if not session:
            return {"ok": False, "error": "missing session"}
        result = session.stop()
        with self.lock:
            if self.sessions.get(session_id) is session:
                self.sessions.pop(session_id, None)
        return result

    def events(self, session_id: str):
        session = self.get(session_id)
        if not session:
            yield "event: error\ndata: {\"error\":\"missing session\"}\n\n"
            return
        q = session.subscribe()
        last_beat = time.time()
        try:
            while True:
                timeout = max(0.2, HEARTBEAT_SEC - (time.time() - last_beat))
                try:
                    evt = q.get(timeout=timeout)
                except queue.Empty:
                    last_beat = time.time()
                    yield "event: heartbeat\ndata: {}\n\n"
                    if session.stopped:
                        return
                    continue
                event_type = str(evt.get("type") or "message")
                yield f"event: {event_type}\ndata: {json.dumps(evt, ensure_ascii=False)}\n\n"
                if event_type == "closed":
                    return
        finally:
            session.unsubscribe(q)


manager = MeetingLiveSessionManager()
