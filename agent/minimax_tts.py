"""
MiniMax TTS — 自訂 LiveKit TTS Plugin（WebSocket 真串流版）

兩條路徑：
  - 主路徑：WebSocket 真串流（streaming=True）。整段回話餵進一個 WS session，
    維持跨句語調脈絡 → 語氣連貫、低延遲。
  - Fallback：WS 握手失敗時退回 REST SSE 串流（單次請求），語音永不靜音。

兩條都先做繁→簡硬轉（opencc），不靠 LLM prompt 拜託模型輸出簡體。

用法：
    tts = MiniMaxCustomTTS(api_key=..., group_id=..., voice_id=...)
"""
import asyncio
import json
import logging
from dataclasses import dataclass
from typing import AsyncIterator

import aiohttp
from livekit.agents import tts, utils

logger = logging.getLogger(__name__)

MINIMAX_REST_URL = "https://api.minimax.io/v1/t2a_v2"
MINIMAX_WS_URL = "wss://api.minimax.io/ws/v1/t2a_v2"

# 在這些字元後把累積的文字送一次 task_continue（保留逗號在句內，維持語氣流動）
# 不含 \n：換行不是句尾，否則會切出空白片段造成微停頓/碎裂感
_SENTENCE_END = "。！？!?…"
_MAX_SEGMENT_CHARS = 40

# ── 繁→簡硬轉（確定性保證 MiniMax 收到簡體）──
_cc = None
_cc_failed = False


def _to_simplified(text: str) -> str:
    global _cc, _cc_failed
    if not text or _cc_failed:
        return text
    try:
        if _cc is None:
            from opencc import OpenCC
            _cc = OpenCC("t2s")
        return _cc.convert(text)
    except Exception as e:  # opencc 缺失/異常 → 不擋語音，原文送出並警告
        _cc_failed = True
        logger.warning(f"opencc 繁→簡失敗，改送原文: {e}")
        return text


@dataclass
class MiniMaxTTSOptions:
    api_key: str
    group_id: str
    voice_id: str
    model: str = "speech-02-turbo"
    speed: float = 1.0
    vol: float = 1.0
    pitch: float = 0.0
    emotion: str = ""   # 空字串 = API 自動推斷
    sample_rate: int = 24000


def _voice_setting(opts: MiniMaxTTSOptions) -> dict:
    vs = {
        "voice_id": opts.voice_id,
        "speed": float(opts.speed),
        "vol": float(opts.vol),
        "pitch": int(opts.pitch),
    }
    if opts.emotion:
        vs["emotion"] = opts.emotion
    return vs


async def _rest_synthesize(
    session: aiohttp.ClientSession, opts: MiniMaxTTSOptions, text: str
) -> AsyncIterator[bytes]:
    """REST SSE 串流，逐塊 yield PCM。擋掉 status==2 的整段重送（見 LESSONS 06-10）。"""
    url = (f"{MINIMAX_REST_URL}?GroupId={opts.group_id}" if opts.group_id else MINIMAX_REST_URL)
    headers = {"Authorization": f"Bearer {opts.api_key}", "Content-Type": "application/json"}
    payload = {
        "text": text,
        "model": opts.model,
        "stream": True,
        "stream_options": {"exclude_aggregated_audio": True},
        "voice_setting": _voice_setting(opts),
        "audio_setting": {"sample_rate": int(opts.sample_rate), "format": "pcm"},
    }
    async with session.post(url, json=payload, headers=headers) as resp:
        if resp.status != 200:
            logger.error(f"MiniMax REST {resp.status}: {(await resp.text())[:200]}")
            return
        buf = b""
        async for raw in resp.content.iter_chunked(65536):
            buf += raw
            while b"\n" in buf:
                raw_line, buf = buf.split(b"\n", 1)
                line = raw_line.decode("utf-8", "ignore").strip()
                if not line.startswith("data:"):
                    continue
                ds = line[5:].strip()
                if ds == "[DONE]":
                    return
                try:
                    chunk = json.loads(ds)
                except json.JSONDecodeError:
                    continue
                sc = chunk.get("base_resp", {}).get("status_code")
                if sc is not None and sc != 0:
                    logger.error(f"MiniMax REST stream error: {chunk.get('base_resp', {})}")
                    return
                data = chunk.get("data", {}) or {}
                if data.get("status") == 2:   # 最後一塊是整段重送，跳過
                    continue
                audio_hex = data.get("audio", "")
                if audio_hex:
                    yield bytes.fromhex(audio_hex)


class MiniMaxCustomTTS(tts.TTS):
    """MiniMax TTS — WS 真串流主路徑 + REST fallback，支援任意 voice_id（含克隆聲紋）"""

    def __init__(
        self,
        *,
        api_key: str,
        group_id: str,
        voice_id: str,
        model: str = "speech-02-turbo",
        speed: float = 1.0,
        vol: float = 1.0,
        pitch: float = 0.0,
        emotion: str = "",
        sample_rate: int = 24000,
    ):
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=True),
            sample_rate=sample_rate,
            num_channels=1,
        )
        self._opts = MiniMaxTTSOptions(
            api_key=api_key, group_id=group_id, voice_id=voice_id, model=model,
            speed=speed, vol=vol, pitch=pitch, emotion=emotion, sample_rate=sample_rate,
        )
        self._session: aiohttp.ClientSession | None = None

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    def synthesize(self, text: str, *, conn_options=None) -> "MiniMaxChunkedStream":
        return MiniMaxChunkedStream(
            tts=self, input_text=text, opts=self._opts,
            session=self._ensure_session(), conn_options=conn_options,
        )

    def stream(self, *, conn_options=None) -> "MiniMaxSynthesizeStream":
        return MiniMaxSynthesizeStream(tts=self, conn_options=conn_options)

    async def aclose(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


class MiniMaxChunkedStream(tts.ChunkedStream):
    """REST 非串流路徑（保留作為相容 / 直接 synthesize 呼叫）"""

    def __init__(self, *, tts, input_text, opts, session, conn_options=None):
        from livekit.agents import APIConnectOptions
        if conn_options is None:
            conn_options = APIConnectOptions()
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._opts = opts
        self._session = session

    async def _run(self, output_emitter) -> None:
        text = _to_simplified(self._input_text)
        logger.info(f"MiniMax REST text: {text[:120]!r}")
        output_emitter.initialize(
            request_id=utils.shortuuid(), sample_rate=self._opts.sample_rate,
            num_channels=1, mime_type="audio/pcm",
        )
        total = 0
        first_audio = True
        async for pcm in _rest_synthesize(self._session, self._opts, text):
            output_emitter.push(pcm)
            total += len(pcm)
            if first_audio:
                first_audio = False
                try:
                    _t = getattr(self, "_tts", None)
                    _cid = getattr(_t, "_call_id", "") if _t else ""
                    if _cid:
                        from services.yuling_loader import record_latency_event
                        record_latency_event(_cid, "tts_first_audio", {"bytes": len(pcm), "mode": "rest"})
                except Exception:
                    pass
            try:
                _t = getattr(self, "_tts", None)
                _cid = getattr(_t, "_call_id", "") if _t else ""
                if _cid:
                    from services.yuling_loader import append_call_audio
                    append_call_audio(_cid, pcm)
            except Exception:
                pass
        output_emitter.flush()
        logger.info(f"MiniMax REST done: {total} bytes")


class MiniMaxSynthesizeStream(tts.SynthesizeStream):
    """WS 真串流路徑（主）— 整段回話一個 session，維持跨句語調脈絡"""

    def __init__(self, *, tts: MiniMaxCustomTTS, conn_options=None):
        from livekit.agents import APIConnectOptions
        if conn_options is None:
            conn_options = APIConnectOptions()
        super().__init__(tts=tts, conn_options=conn_options)
        self._tts = tts
        self._opts = tts._opts

    async def _run(self, output_emitter) -> None:
        opts = self._opts
        session = self._tts._ensure_session()
        output_emitter.initialize(
            request_id=utils.shortuuid(), sample_rate=opts.sample_rate,
            num_channels=1, mime_type="audio/pcm", stream=True,
        )
        output_emitter.start_segment(segment_id=utils.shortuuid())

        headers = {"Authorization": f"Bearer {opts.api_key}"}
        ws = None
        try:
            ws = await session.ws_connect(MINIMAX_WS_URL, headers=headers, heartbeat=30)
            await asyncio.wait_for(ws.receive_json(), timeout=10)  # connected_success
            await ws.send_json({
                "event": "task_start",
                "model": opts.model,
                "voice_setting": _voice_setting(opts),
                "audio_setting": {"sample_rate": int(opts.sample_rate), "format": "pcm", "channel": 1},
            })
            started = await asyncio.wait_for(ws.receive_json(), timeout=10)
            if started.get("event") != "task_started":
                raise RuntimeError(f"task_start 失敗: {started.get('base_resp')}")
        except Exception as e:
            logger.error(f"MiniMax WS 握手失敗 → REST fallback: {e}")
            if ws is not None and not ws.closed:
                await ws.close()
            await self._rest_fallback(output_emitter)
            return

        logger.info("MiniMax WS streaming started")
        try:
            def _seg(b: str) -> str:
                # 折疊換行/多空白為單一空白，避免送出空白片段造成碎裂
                return " ".join(_to_simplified(b).split())

            async def _forward_input() -> None:
                buf = ""
                async for data in self._input_ch:
                    if isinstance(data, self._FlushSentinel):
                        seg = _seg(buf)
                        if seg:
                            await ws.send_json({"event": "task_continue", "text": seg})
                        buf = ""
                        continue
                    buf += data
                    if buf and (buf[-1] in _SENTENCE_END or len(buf) >= _MAX_SEGMENT_CHARS):
                        seg = _seg(buf)
                        if seg:
                            await ws.send_json({"event": "task_continue", "text": seg})
                        buf = ""
                seg = _seg(buf)
                if seg:
                    await ws.send_json({"event": "task_continue", "text": seg})
                await ws.send_json({"event": "task_finish"})

            async def _recv_audio() -> int:
                total = 0
                first_audio = True
                while True:
                    msg = await asyncio.wait_for(ws.receive_json(), timeout=30)
                    data = msg.get("data", {}) or {}
                    audio_hex = data.get("audio", "")
                    if audio_hex:
                        pcm = bytes.fromhex(audio_hex)
                        output_emitter.push(pcm)
                        total += len(pcm)
                        if first_audio:
                            first_audio = False
                            try:
                                _t = getattr(self, "_tts", None)
                                _cid = getattr(_t, "_call_id", "") if _t else ""
                                if _cid:
                                    from services.yuling_loader import record_latency_event
                                    record_latency_event(_cid, "tts_first_audio", {"bytes": len(pcm), "mode": "ws"})
                            except Exception:
                                pass
                        try:
                            _t = getattr(self, "_tts", None)
                            _cid = getattr(_t, "_call_id", "") if _t else ""
                            if _cid:
                                from services.yuling_loader import append_call_audio
                                append_call_audio(_cid, pcm)
                        except Exception:
                            pass
                    if msg.get("event") == "task_finished":
                        break
                logger.info(f"MiniMax WS streaming done: {total} bytes")
                return total

            _, total = await asyncio.gather(_forward_input(), _recv_audio())
            if total <= 0:
                raise RuntimeError("MiniMax WS produced 0 bytes")
            output_emitter.flush()
        except Exception as e:
            logger.error(f"MiniMax WS streaming failed ? REST fallback: {e}")
            if ws is not None and not ws.closed:
                await ws.close()
            await self._rest_fallback(output_emitter)
            return
        finally:
            if ws is not None and not ws.closed:
                await ws.close()

    async def _rest_fallback(self, output_emitter) -> None:
        """WS 不可用時：把整段輸入收齊，走 REST 串流，語音不靜音。"""
        parts = []
        async for data in self._input_ch:
            if isinstance(data, self._FlushSentinel):
                continue
            parts.append(data)
        text = _to_simplified("".join(parts))
        if text.strip():
            async for pcm in _rest_synthesize(self._tts._ensure_session(), self._opts, text):
                output_emitter.push(pcm)
        output_emitter.flush()
