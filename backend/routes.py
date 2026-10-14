"""routes/yuling.py — Voice Companion語音 token 簽發 (P-Voice Companion)

Voice Companion = AI 夥伴即時語音通話 App (ailivex 地基 + AI 夥伴記憶)。
本路由簽 LiveKit access token + 派工 v9 agent 進房。
不依賴 livekit-server-sdk —— 直接用 PyJWT 手簽 (對齊 sdk 的 JWT 結構)。
使用者 2026-06-14 拍板。

1 route:
  POST /api/yuling/token  → {token, url, room, identity, agent}

安全:
  YULING_ACCESS_SECRET 設了就強制檢查 (擋陌生人連進來燒 Anthropic)。
  沒設 = 開放 (本地測試方便)。LiveKit 三把優先讀 env, 退 ~/.ailivex_secrets.env。
"""
import json
import os
import time
import urllib.error
import urllib.request
import sqlite3
from uuid import uuid4

import jwt
from flask import Blueprint, Response, jsonify, request

# OSS: 原為 import taiyi_server（共享 taiyi 大腦狀態）。開源版用本地 DATA_DIR。
DATA_DIR = os.environ.get("DATA_DIR", "data")

yuling_bp = Blueprint("yuling", __name__)

AGENT_NAME = os.environ.get("YULING_AGENT_NAME", "ailivex-realtime-v9").strip()
TOKEN_TTL_SEC = 6 * 3600  # 6 小時, 一通電話夠久
SECRETS_FILE = os.path.join(os.path.expanduser("~"), ".ailivex_secrets.env")
AGENT_LOG = os.path.join(os.path.expanduser("~"), "yuling-agent.log")
_health_cache = {"ts": 0.0, "data": None}   # 真實檢測快取 (probe 不每次打)


def _load_livekit_creds():
    """讀 LiveKit 三把 + access secret。優先 env, 退 ~/.ailivex_secrets.env。"""
    creds = {
        "url": os.environ.get("LIVEKIT_URL", "").strip(),
        "key": os.environ.get("LIVEKIT_API_KEY", "").strip(),
        "secret": os.environ.get("LIVEKIT_API_SECRET", "").strip(),
        "access": os.environ.get("YULING_ACCESS_SECRET", "").strip(),
    }
    keymap = {
        "LIVEKIT_URL": "url",
        "LIVEKIT_API_KEY": "key",
        "LIVEKIT_API_SECRET": "secret",
        "YULING_ACCESS_SECRET": "access",
    }
    # 永遠讀 secrets 檔填空槽 (env 優先)。access secret 多半只在檔裡, 不能因為
    # LiveKit 三把已從 env 拿到就跳過讀檔 —— 否則防呆會被靜默跳過 (200 放行陌生人)。
    if not all(creds.values()) and os.path.exists(SECRETS_FILE):
        try:
            with open(SECRETS_FILE, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if "=" not in line or line.startswith("#"):
                        continue
                    k, v = line.split("=", 1)
                    slot = keymap.get(k.strip())
                    if slot and not creds[slot]:
                        creds[slot] = v.strip()
        except Exception:
            pass
    return creds


def _env_or_file(key):
    """讀單一 key: 優先 env, 退 ~/.ailivex_secrets.env。回 value 或空字串。"""
    v = os.environ.get(key, "").strip()
    if v:
        return v
    try:
        if os.path.exists(SECRETS_FILE):
            with open(SECRETS_FILE, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith(key + "=") and not line.startswith("#"):
                        return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""


def _mint_token(api_key, api_secret, identity, name, room, agent_meta):
    """手簽 LiveKit JWT (對齊 livekit-server-sdk 結構)。

    關鍵: roomConfig.agents 內含 agentName → 使用者進房時 LiveKit
    自動派工該具名 agent (v9) 進房。沒這段 = AI 夥伴不會加入通話。
    """
    now = int(time.time())
    claims = {
        "iss": api_key,
        "sub": identity,
        "name": name,
        "nbf": now,
        "exp": now + TOKEN_TTL_SEC,
        "video": {
            "room": room,
            "roomJoin": True,
            "canPublish": True,
            "canSubscribe": True,
            "canPublishData": True,
        },
        "roomConfig": {
            "agents": [{"agentName": AGENT_NAME, "metadata": agent_meta}],
        },
    }
    return jwt.encode(claims, api_secret, algorithm="HS256")


def _probe_livekit_auth(creds, timeout=4):
    """Verify this project's key pair with a read-only LiveKit request."""
    if not all((creds["url"], creds["key"], creds["secret"])):
        return "missing"
    now = int(time.time())
    token = jwt.encode({"iss": creds["key"], "sub": "health-check",
                        "nbf": now - 30, "exp": now + 60,
                        "video": {"roomList": True}},
                       creds["secret"], algorithm="HS256")
    base = creds["url"].replace("wss://", "https://", 1).replace("ws://", "http://", 1).rstrip("/")
    req = urllib.request.Request(base + "/twirp/livekit.RoomService/ListRooms",
                                 data=b"{}", method="POST",
                                 headers={"Authorization": "Bearer " + token,
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return "verified" if response.status == 200 else "unreachable"
    except urllib.error.HTTPError as exc:
        return "invalid" if exc.code in (401, 403) else "unreachable"
    except Exception:
        return "unreachable"


@yuling_bp.route("/api/yuling/token", methods=["POST"])
def api_yuling_token():
    """簽一張 LiveKit token 給前端連進房, 並派AI 夥伴 (v9) 進房。"""
    creds = _load_livekit_creds()
    if not all([creds["url"], creds["key"], creds["secret"]]):
        return jsonify({"error": "LiveKit 未设置 (LIVEKIT_URL/API_KEY/API_SECRET)"}), 500

    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"error": "请求格式不正确"}), 400

    # access secret 防陌生人燒錢 (設了才檢查)
    if creds["access"]:
        given = (body.get("access") or request.headers.get("X-Yuling-Access") or "").strip()
        if given != creds["access"]:
            return jsonify({"error": "forbidden"}), 403

    context_enabled = os.environ.get("POC_VOICE_CONTEXT_ENABLED", "") == "1"
    if context_enabled:
        # Only the authenticated PWA middleware may set this WSGI request key.
        # In particular, body.identity, body.userId, and browser metadata are
        # never sufficient to select another account's memory.
        owner = request.environ.get("poc.authenticated_owner")
        signing_key = os.environ.get("POC_VOICE_CONTEXT_HMAC_KEY", "")
        if not isinstance(owner, str) or not owner:
            return jsonify({"error": "请先登录"}), 401
        if request.environ.get("poc.same_origin") is not True:
            return jsonify({"error": "来源不符"}), 403
        if len(signing_key.encode("utf-8")) < 32:
            return jsonify({"error": "通话记忆尚未配置"}), 503
        try:
            from deploy_pwa.chat_store import ChatStore, StoreUnavailable
            from deploy_pwa.voice_context_issuer import issue_voice_context
            store = ChatStore(os.environ.get("POC_CHAT_DB_PATH", ""), owner,
                              legacy_owner_id=os.environ.get(
                                  "POC_LEGACY_OWNER", os.environ.get("POC_PREVIEW_USER", owner)))
            memories = store.usable_memories(limit=30)
            recent_text = store.latest_user_text(max_age_seconds=300)
            knowledge = (store.search_knowledge(recent_text, limit=2)
                         if recent_text and store.knowledge_consent_enabled() else [])
        except (OSError, ValueError, StoreUnavailable, sqlite3.Error):
            return jsonify({"error": "通话记忆暂时不可用"}), 503
        identity = "guest-" + uuid4().hex[:20]
        name = "使用者"
        room = "ailivex-yuling-" + uuid4().hex
        account_seed = {}
        if owner == "MING":
            from deploy_pwa.server import _profile_store
            try:
                profile = _profile_store(store)
                if profile is None:
                    return jsonify({"error": "账号基础资料暂时不可用"}), 503
                account_seed = profile.seed_facts()
            except (OSError, ValueError, sqlite3.Error):
                return jsonify({"error": "账号基础资料暂时不可用"}), 503
        agent_meta = issue_voice_context(signing_key, room, owner, memories,
                                         account_seed=account_seed,
                                         knowledge=knowledge)
    else:
        # Keep calls separate even before voice-memory context is enabled.
        # Browser identity is neither an account proof nor a safe room key.
        identity = "guest-" + uuid4().hex[:20]
        name = "使用者"
        room = "ailivex-yuling-" + uuid4().hex
        agent_meta = json.dumps({
            "characterId": "default",
            "userId": identity,
            "convId": f"ailivex-voice-default-{identity}",  # 通話結束寫回記憶用
        })

    token = _mint_token(creds["key"], creds["secret"], identity, name, room, agent_meta)

    return jsonify({
        "token": token,
        "url": creds["url"],
        "room": room,
        "identity": identity,
        "agent": AGENT_NAME,
    })


def _probe_gemini(key, timeout=4):
    """真打 Gemini 一個免費 models 查詢驗 key (不燒 token)。200=有效。"""
    if not key:
        return False
    try:
        url = ("https://generativelanguage.googleapis.com/v1beta/models?key="
               + key)
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= getattr(r, "status", r.getcode()) < 300
    except Exception:
        return False


def _probe_minimax_m3(key, model="MiniMax-M3", timeout=5):
    """驗 MiniMax M3 (Anthropic 相容端點)。max_tokens=1, 成本可忽略。200=有效。"""
    if not key:
        return False
    try:
        body = json.dumps({"model": model, "max_tokens": 1,
                           "messages": [{"role": "user", "content": "hi"}]}).encode()
        req = urllib.request.Request(
            "https://api.minimax.io/anthropic/v1/messages", data=body,
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= getattr(r, "status", r.getcode()) < 300
    except Exception:
        return False


def _probe_anthropic(key, model="claude-haiku-4-5", timeout=5):
    """驗 Anthropic key。max_tokens=1。200=有效。"""
    if not key:
        return False
    try:
        body = json.dumps({"model": model, "max_tokens": 1,
                           "messages": [{"role": "user", "content": "hi"}]}).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=body,
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= getattr(r, "status", r.getcode()) < 300
    except Exception:
        return False


def _scan_agent_log():
    """讀 yuling-agent.log 尾段, 只看「最後一次 registered worker 之後」的當前 instance
    真實狀態 (不被歷史 401 騙)。回 stt/tts/livekit -> ok/bad/unknown。
    誠實原則: 沒證據在運作就不給 ok。"""
    res = {"stt": "unknown", "tts": "unknown", "livekit": "bad"}
    try:
        size = os.path.getsize(AGENT_LOG)
        with open(AGENT_LOG, "rb") as f:
            if size > 200000:
                f.seek(size - 200000)
            lines = f.read().decode("utf-8", "ignore").splitlines()
    except Exception:
        return res
    reg = -1
    for i in range(len(lines) - 1, -1, -1):
        if "registered worker" in lines[i]:
            reg = i
            break
    if reg < 0:
        return res  # 沒註冊 → livekit bad, 其餘 unknown
    after = lines[reg + 1:]
    res["livekit"] = "bad" if any("worker failed" in ln for ln in after) else "ok"
    cur = lines[reg:]
    # 只看「最近一通」的結果 (修好+撥成功就轉綠, 不被同 instance 的舊錯誤永久黏住)
    call_start = -1
    for i in range(len(cur) - 1, -1, -1):
        if ("Job dispatched" in cur[i]) or ("Connected to room" in cur[i]):
            call_start = i
            break
    if call_start < 0:
        return res  # 此 instance 還沒通話 → stt/tts unknown
    seg = cur[call_start:]
    # 逐行精確比對 (避免跨行誤判: 例如 Anthropic 3a 的 "Error" 不該算 MiniMax 壞)
    stt_bad = tts_bad = False
    for ln in seg:
        low = ln.lower()
        if ("incorrect api key" in low) or ("soniox stt error" in low) or \
           ("deepgram" in low and ("error" in low or "unauthorized" in low)) or \
           ("balance exhausted" in low) or ("failed to recognize speech after" in ln):
            stt_bad = True
        if "minimax" in low and ("missing" in low or "error" in low or "失敗" in ln):
            tts_bad = True
    res["stt"] = "bad" if stt_bad else "ok"   # 最近這通有講且無錯 → 綠
    res["tts"] = "bad" if tts_bad else "ok"
    return res


@yuling_bp.route("/api/yuling/health", methods=["GET"])
def api_yuling_health():
    """Voice Companion各 API 健康燈 — 真實檢測 (不是只看 key 有沒有填)。

    ok=確認運作 / bad=壞了或缺設定 / unknown=還沒驗證過(前端顯示黃, 不給綠)。
    LLM 真打 Gemini 驗 key; STT/TTS 讀 agent 真實 log(當前 instance); LiveKit 看 worker
    有無註冊; 記憶看讀不讀得到。快取 90s 避免每次都 probe。MIC 前端自查。
    """
    now = time.time()
    if _health_cache["data"] and now - _health_cache["ts"] < 30:
        return jsonify(_health_cache["data"])

    creds = _load_livekit_creds()
    if AGENT_NAME != "ailivex-realtime-v9":
        # Hosted Builder runs outside this server. This endpoint can prove only
        # that the preview API responds and that LiveKit credentials are set.
        # Provider and Agent health require actual call events or LiveKit-side checks.
        from storage import CALL_HISTORY_HEALTH, INNER_MONOLOGUE_HEALTH
        data = {"service": "ok", "mode": "hosted_builder",
                "call_config": _probe_livekit_auth(creds),
                "agent": AGENT_NAME,
                "provider_health": "not_observable_from_preview",
                "history_health": CALL_HISTORY_HEALTH,
                "inner_health": INNER_MONOLOGUE_HEALTH,
                "integrations": {"hermes": "not_connected", "memory": "not_connected",
                                 "knowledge": "not_connected", "graph": "not_connected",
                                 "mcp": "not_connected"}}
        _health_cache["ts"] = now
        _health_cache["data"] = data
        return jsonify(data)
    log = _scan_agent_log()

    provider = (_env_or_file("YULING_REALTIME_LLM_PROVIDER") or "minimax_m3").lower()
    if provider in ("minimax", "minimax_m3", "m3"):
        m3k = _env_or_file("MINIMAX_M3_API_KEY") or _env_or_file("MINIMAX_API_KEY")
        m3model = _env_or_file("YULING_REALTIME_LLM_MODEL") or "MiniMax-M3"
        llm = "ok" if _probe_minimax_m3(m3k, m3model) else "bad"
    elif provider in ("anthropic", "claude"):
        llm = "ok" if _probe_anthropic(_env_or_file("ANTHROPIC_API_KEY")) else "bad"
    else:
        gem = _env_or_file("GEMINI_API_KEY") or _env_or_file("GOOGLE_API_KEY")
        llm = "ok" if _probe_gemini(gem) else "bad"

    stt_key = _env_or_file("DEEPGRAM_API_KEY") or _env_or_file("SONIOX_API_KEY")
    stt = "bad" if not stt_key else log["stt"]
    tts = "bad" if not _env_or_file("MINIMAX_API_KEY") else log["tts"]
    livekit = ("bad" if not (creds["url"] and creds["key"] and creds["secret"])
               else log["livekit"])
    try:
        mem = "ok" if os.path.exists(
            os.path.join(DATA_DIR, "memory_blocks.json")) else "bad"
    except Exception:
        mem = "bad"

    data = {"llm": llm, "stt": stt, "tts": tts, "livekit": livekit,
            "memory": mem, "agent": AGENT_NAME}
    _health_cache["ts"] = now
    _health_cache["data"] = data
    return jsonify(data)


# ── 對話紀錄 (使用者要的「歷史性保存」面板) ──

def _access_ok():
    """讀取對話紀錄要過 access secret (對話內容是私密的)。
    沒設 access = 開放 (本地測試)。設了 → header X-Yuling-Access 或 ?access= 要對。"""
    access = _load_livekit_creds().get("access", "")
    if not access:
        return True
    given = (request.headers.get("X-Yuling-Access")
             or request.args.get("access") or "").strip()
    return given == access


@yuling_bp.route("/api/yuling/calls", methods=["GET"])
def api_yuling_calls():
    """列出所有通話紀錄 (最新在前)。每通含起訖時間、句數、開頭預覽。"""
    if not _access_ok():
        return jsonify({"error": "forbidden"}), 403
    from storage import list_calls, CALL_HISTORY_HEALTH
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
    except (TypeError, ValueError):
        limit = 50
    return jsonify({"calls": list_calls(limit=limit),
                    "available": CALL_HISTORY_HEALTH == "ok"})


@yuling_bp.route("/api/yuling/calls/<call_id>", methods=["GET"])
def api_yuling_call_detail(call_id):
    """讀單通完整逐字稿 (每句帶年月日時分秒, 一段一段)。"""
    if not _access_ok():
        return jsonify({"error": "forbidden"}), 403
    from storage import (read_call, call_audio_path,
                                        is_call_kept, read_inner)
    data = read_call(call_id)
    data["audio"] = bool(call_audio_path(call_id))
    data["kept"] = is_call_kept(call_id)
    data["inner"] = read_inner(call_id).get("text", "")
    return jsonify(data)


@yuling_bp.route("/api/yuling/inner", methods=["GET"])
def api_yuling_inner():
    """AI 夥伴的內心獨白集 (最新在前)。她講話時不會說出口, 只在這裡保存。"""
    if not _access_ok():
        return jsonify({"error": "forbidden"}), 403
    from storage import list_inner, INNER_MONOLOGUE_HEALTH
    try:
        limit = min(int(request.args.get("limit", 100)), 300)
    except (TypeError, ValueError):
        limit = 100
    return jsonify({"inner": list_inner(limit=limit),
                    "available": INNER_MONOLOGUE_HEALTH == "ok"})


@yuling_bp.route("/api/yuling/calls/<call_id>/audio", methods=["GET"])
def api_yuling_call_audio(call_id):
    """串流AI 夥伴這通的聲音 (PCM 包成 WAV)。瀏覽器 <audio> 可直接播。"""
    if not _access_ok():
        return jsonify({"error": "forbidden"}), 403
    from storage import call_audio_path, wav_header
    path = call_audio_path(call_id)
    if not path:
        return jsonify({"error": "no audio"}), 404
    data_len = os.path.getsize(path)

    def _gen():
        yield wav_header(data_len)
        with open(path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                yield chunk

    return Response(_gen(), mimetype="audio/wav", headers={
        "Content-Length": str(44 + data_len),
        "Content-Disposition": f'inline; filename="{call_id}.wav"',
        "Cache-Control": "no-store",
    })


@yuling_bp.route("/api/yuling/calls/<call_id>/keep", methods=["POST"])
def api_yuling_call_keep(call_id):
    """正式保存切換: {keep:true}=永久保存(不被3天清掉) / {keep:false}=取消。"""
    if not _access_ok():
        return jsonify({"error": "forbidden"}), 403
    from storage import set_call_kept, is_call_kept
    body = request.get_json(silent=True) or {}
    keep = bool(body.get("keep", True))
    set_call_kept(call_id, keep)
    return jsonify({"call_id": call_id, "kept": is_call_kept(call_id)})


@yuling_bp.route("/api/yuling/meetings/chunks", methods=["POST"])
def api_yuling_meeting_chunk_upload():
    """Persist one meeting-mode audio chunk from the Yuling frontend."""
    if not _access_ok():
        return jsonify({"error": "forbidden"}), 403
    from deepgram_stt import transcribe_prerecorded
    from storage import save_meeting_chunk, save_meeting_chunk_analysis

    audio = request.files.get("audio")
    if not audio:
        return jsonify({"error": "missing audio"}), 400

    audio_bytes = audio.read()
    payload = save_meeting_chunk(
        session_id=(request.form.get("session_id") or "").strip(),
        chunk_index=request.form.get("chunk_index", 0),
        start_ms=request.form.get("start_ms", 0),
        end_ms=request.form.get("end_ms", 0),
        audio_bytes=audio_bytes,
        mime_type=(request.form.get("mime_type") or audio.mimetype or "audio/webm").strip(),
    )
    if not payload:
        return jsonify({"error": "bad chunk"}), 400
    stt = transcribe_prerecorded(audio_bytes=audio_bytes, mime_type=payload["mime_type"])
    if stt:
        save_meeting_chunk_analysis(
            payload["session_id"],
            payload["chunk_index"],
            transcript=stt.get("transcript", ""),
            summary=stt.get("summary", ""),
            utterances=stt.get("utterances") or [],
        )
        payload.update({
            "transcript": stt.get("transcript", ""),
            "summary": stt.get("summary", ""),
            "utterances": stt.get("utterances") or [],
            "stt_model": stt.get("model", ""),
        })
    return jsonify({"ok": True, **payload})


@yuling_bp.route("/api/yuling/meetings/stream/start", methods=["POST"])
def api_yuling_meeting_stream_start():
    """Start one backend streaming STT session for meeting mode."""
    if not _access_ok():
        return jsonify({"error": "forbidden"}), 403
    from meeting_live_stt import manager

    body = request.get_json(silent=True) or {}
    started = manager.start_session(
        session_id=(body.get("session_id") or "").strip(),
        save_audio=bool(body.get("save_audio", False)),
    )
    if not started:
        return jsonify({"error": "stream start failed"}), 500
    return jsonify({"ok": True, **started})


@yuling_bp.route("/api/yuling/meetings/stream/<session_id>/append", methods=["POST"])
def api_yuling_meeting_stream_append(session_id):
    """Append one PCM chunk into an active backend streaming STT session."""
    if not _access_ok():
        return jsonify({"error": "forbidden"}), 403
    from meeting_live_stt import manager

    audio_bytes = request.get_data(cache=False, as_text=False) or b""
    if not audio_bytes:
        return jsonify({"error": "empty audio"}), 400
    result = manager.append_audio(
        session_id,
        audio_bytes,
        start_ms=request.headers.get("X-Start-Ms", 0),
        end_ms=request.headers.get("X-End-Ms", 0),
    )
    code = 200 if result.get("ok") else 400
    return jsonify(result), code


@yuling_bp.route("/api/yuling/meetings/stream/<session_id>/events", methods=["GET"])
def api_yuling_meeting_stream_events(session_id):
    """Stream backend STT events to the frontend as SSE."""
    if not _access_ok():
        return jsonify({"error": "forbidden"}), 403
    from meeting_live_stt import manager

    return Response(
        manager.events(session_id),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )


@yuling_bp.route("/api/yuling/meetings/stream/<session_id>/stop", methods=["POST"])
def api_yuling_meeting_stream_stop(session_id):
    """Stop one backend streaming STT session and flush optional audio."""
    if not _access_ok():
        return jsonify({"error": "forbidden"}), 403
    from meeting_live_stt import manager

    result = manager.stop_session(session_id)
    code = 200 if result.get("ok") else 400
    return jsonify(result), code


@yuling_bp.route("/api/yuling/meetings/live-stt", methods=["POST"])
def api_yuling_meeting_live_stt():
    """Transcribe one short live recorder slice and persist the text stream."""
    if not _access_ok():
        return jsonify({"error": "forbidden"}), 403
    from deepgram_stt import transcribe_live_slice
    from storage import save_meeting_live_slice

    audio = request.files.get("audio")
    if not audio:
        return jsonify({"error": "missing audio"}), 400
    audio_bytes = audio.read()
    if not audio_bytes:
        return jsonify({"error": "empty audio"}), 400
    stt = transcribe_live_slice(
        audio_bytes=audio_bytes,
        mime_type=(request.form.get("mime_type") or audio.mimetype or "audio/webm").strip(),
    ) or {}
    saved = save_meeting_live_slice(
        session_id=(request.form.get("session_id") or "").strip(),
        slice_index=request.form.get("slice_index", 0),
        start_ms=request.form.get("start_ms", 0),
        end_ms=request.form.get("end_ms", 0),
        transcript=stt.get("transcript", ""),
    )
    if not saved:
        return jsonify({"error": "bad slice"}), 400
    return jsonify({"ok": True, **saved, "stt_model": stt.get("model", "")})


@yuling_bp.route("/api/yuling/meetings/minute-note", methods=["POST"])
def api_yuling_meeting_minute_note():
    """Persist one minute-level work note from the current transcript stream."""
    if not _access_ok():
        return jsonify({"error": "forbidden"}), 403
    from storage import save_meeting_minute_note

    body = request.get_json(silent=True) or {}
    transcript = (body.get("transcript") or "").strip()
    saved = save_meeting_minute_note(
        session_id=(body.get("session_id") or "").strip(),
        minute_index=body.get("minute_index", 0),
        start_ms=body.get("start_ms", 0),
        end_ms=body.get("end_ms", 0),
        transcript=transcript,
        summary=(body.get("summary") or "").strip(),
    )
    if not saved:
        return jsonify({"error": "bad minute note"}), 400
    return jsonify({"ok": True, **saved})


@yuling_bp.route("/api/yuling/meetings/finalize", methods=["POST"])
def api_yuling_meeting_finalize():
    """Persist one formal meeting record into durable history."""
    if not _access_ok():
        return jsonify({"error": "forbidden"}), 403
    from storage import save_formal_meeting_record

    body = request.get_json(silent=True) or {}
    saved = save_formal_meeting_record(
        session_id=(body.get("session_id") or "").strip(),
        summary=(body.get("summary") or "").strip(),
        transcript_preview=(body.get("transcript_preview") or "").strip(),
    )
    if not saved:
        return jsonify({"error": "bad formal record"}), 400
    return jsonify({"ok": True, **saved})


@yuling_bp.route("/api/yuling/meetings", methods=["GET"])
def api_yuling_meetings():
    if not _access_ok():
        return jsonify({"error": "forbidden"}), 403
    from storage import list_meeting_sessions
    try:
        limit = min(int(request.args.get("limit", 20)), 100)
    except (TypeError, ValueError):
        limit = 20
    return jsonify({"meetings": list_meeting_sessions(limit=limit)})


@yuling_bp.route("/api/yuling/meetings/<session_id>", methods=["GET"])
def api_yuling_meeting_detail(session_id):
    if not _access_ok():
        return jsonify({"error": "forbidden"}), 403
    from storage import read_meeting_session
    return jsonify(read_meeting_session(session_id))


@yuling_bp.route("/api/yuling/calls/<call_id>/event", methods=["POST"])
def api_yuling_call_event(call_id):
    """Record a browser-side non-speech audio event for this call."""
    if not _access_ok():
        return jsonify({"error": "forbidden"}), 403
    from storage import record_audio_event
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "bad event"}), 400
    label = body.get("label")
    if not isinstance(label, str) or not 1 <= len(label.strip()) <= 40:
        return jsonify({"error": "bad event"}), 400
    label = label.strip()
    try:
        confidence = float(body.get("confidence", 0.0))
    except (TypeError, ValueError):
        return jsonify({"error": "bad event"}), 400
    if not 0 <= confidence <= 1:
        return jsonify({"error": "bad event"}), 400
    meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
    if len(json.dumps(meta, ensure_ascii=False)) > 2000:
        return jsonify({"error": "bad event"}), 400
    ok = record_audio_event(call_id, {"label": label, "confidence": confidence,
                                      "meta": meta, "source_kind": "browser_candidate"})
    if not ok:
        return jsonify({"error": "bad event"}), 400
    return jsonify({"ok": True, "call_id": call_id, "label": label})
