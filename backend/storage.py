"""storage.py — 通話 / 會議的儲存層（通用骨架版，檔案式）

routes.py（token + meetings 端點）需要這些函式來存取「通話」與「會議」。
原專案這裡接的是一個完整資料層 + 長期記憶大腦；開源版用最簡單的「檔案式」
實作讓整套能跑起來。你可以換成 SQLite / Postgres / 你自己的儲存。

會議逐字稿 / 記要存成 JSON 於 DATA_DIR/meetings/{session_id}.json。
"""
from __future__ import annotations

import json
import os
import struct

DATA_DIR = os.environ.get("DATA_DIR", "data")
_MEET = os.path.join(DATA_DIR, "meetings")
_CALL = os.path.join(DATA_DIR, "calls")
_KEPT = os.path.join(DATA_DIR, "kept.json")

# Open-source skeleton: the hosted preview has no persistent call/inner store.
CALL_HISTORY_HEALTH = "not_connected"
INNER_MONOLOGUE_HEALTH = "not_connected"


def _safe(s) -> str:
    return "".join(c for c in str(s) if c.isalnum() or c in "-_") or "x"


def _ensure(d):
    os.makedirs(d, exist_ok=True)


def _mpath(sid):
    return os.path.join(_MEET, f"{_safe(sid)}.json")


def _load_meeting(sid):
    p = _mpath(sid)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {"session_id": sid, "slices": [], "events": [],
            "analysis": [], "minute_note": "", "finalized": False}


def _save_meeting(sid, m):
    _ensure(_MEET)
    with open(_mpath(sid), "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)


# ══════════ 會議（模組 B）══════════
def save_meeting_live_slice(session_id, slice_data=None, **kw):
    m = _load_meeting(session_id)
    m["slices"].append(slice_data if slice_data is not None else kw)
    _save_meeting(session_id, m)


def save_meeting_chunk(session_id, chunk=None, **kw):
    return save_meeting_live_slice(session_id, chunk, **kw)


def save_meeting_chunk_analysis(session_id, analysis=None, **kw):
    m = _load_meeting(session_id)
    m["analysis"].append(analysis if analysis is not None else kw)
    _save_meeting(session_id, m)


def save_meeting_minute_note(session_id, note="", **kw):
    m = _load_meeting(session_id)
    m["minute_note"] = note
    _save_meeting(session_id, m)


def save_formal_meeting_record(session_id, record=None, **kw):
    m = _load_meeting(session_id)
    m["record"] = record if record is not None else kw
    m["finalized"] = True
    _save_meeting(session_id, m)


def record_audio_event(call_id, event=None, **kw):
    """Store browser sound candidates separately from meeting records.

    This open-source file store is a POC only; hosted calls are not durable until
    DATA_DIR is mounted on persistent storage.
    """
    _ensure(_CALL)
    path = os.path.join(_CALL, f"{_safe(call_id)}.events.json")
    try:
        with open(path, encoding="utf-8") as f:
            events = json.load(f)
        if not isinstance(events, list):
            events = []
    except FileNotFoundError:
        events = []
    events.append(event if event is not None else kw)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(events[-200:], f, ensure_ascii=False)
    return True


def read_meeting_session(session_id, **kw):
    return _load_meeting(session_id)


def list_meeting_sessions(limit=8, **kw):
    _ensure(_MEET)
    files = sorted(os.listdir(_MEET), reverse=True)[:int(limit)]
    out = []
    for fn in files:
        try:
            with open(os.path.join(_MEET, fn), encoding="utf-8") as f:
                out.append(json.load(f))
        except Exception:
            pass
    return out


# ══════════ 通話（模組 A；原接長期記憶，骨架版為最小實作/佔位）══════════
def list_calls(*args, **kw):
    return []


def read_call(*args, **kw):
    return {}


def list_inner(*args, **kw):
    return []


def call_audio_path(call_id="", **kw):
    return os.path.join(_CALL, f"{_safe(call_id)}.wav")


def wav_header(sample_rate=16000, channels=1, bits=16, data_len=0) -> bytes:
    """產一個標準 WAV header（給前端播放通話錄音用）。"""
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    return (b"RIFF" + struct.pack("<I", 36 + data_len) + b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, sample_rate,
                                    byte_rate, block_align, bits)
            + b"data" + struct.pack("<I", data_len))


def _load_kept():
    if os.path.exists(_KEPT):
        with open(_KEPT, encoding="utf-8") as f:
            return json.load(f)
    return {}


def set_call_kept(call_id, kept=True, **kw):
    _ensure(DATA_DIR)
    d = _load_kept()
    d[str(call_id)] = bool(kept)
    with open(_KEPT, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)


def is_call_kept(call_id, **kw):
    return bool(_load_kept().get(str(call_id), False))
