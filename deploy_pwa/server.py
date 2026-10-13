"""Authenticated PWA preview with optional, isolated test accounts."""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import timedelta
from functools import wraps
from hashlib import sha256
import hmac
import io
import json
import os
import re
import sys
import threading
import time
import zipfile
from email.parser import BytesParser
from email import policy
from xml.etree import ElementTree as ET
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from pathlib import Path
from flask import Flask, abort, jsonify, redirect, request, send_from_directory, session, send_file
from werkzeug.security import check_password_hash
from uuid import UUID

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))
from routes import yuling_bp  # noqa: E402
from deploy_pwa.chat_store import ChatStore, StoreUnavailable, PageReadQuotaExceeded
from deploy_pwa.chat_api import reply_to, ProviderError
from deploy_pwa.public_page_reader import PageReadError, _validated_url, read_public_page
USERNAME = os.environ.get("POC_PREVIEW_USER", "")
PASSWORD = os.environ.get("POC_PREVIEW_PASSWORD", "")
SESSION_SECRET = os.environ.get("POC_SESSION_SECRET", "")


def _load_extra_accounts():
    """Read optional username -> Werkzeug PBKDF2 hash entries, failing closed."""
    raw = os.environ.get("POC_EXTRA_ACCOUNTS_JSON", "").strip()
    if not raw:
        return {}
    try:
        accounts = json.loads(raw)
    except ValueError as exc:
        raise RuntimeError("POC_EXTRA_ACCOUNTS_JSON must be valid JSON") from exc
    if not isinstance(accounts, dict) or not 1 <= len(accounts) <= 20:
        raise RuntimeError("POC_EXTRA_ACCOUNTS_JSON must contain 1 to 20 accounts")
    for username, password_hash in accounts.items():
        if (not isinstance(username, str) or
                not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{2,63}", username) or
                username == USERNAME or not isinstance(password_hash, str) or
                not re.fullmatch(r"pbkdf2:sha256:1000000\$[A-Za-z0-9]{16}\$[0-9a-f]{64}",
                                 password_hash)):
            raise RuntimeError("POC_EXTRA_ACCOUNTS_JSON has an invalid account entry")
    return accounts


EXTRA_ACCOUNTS = _load_extra_accounts()
BRIDGE = os.environ.get("POC_LOCAL_BRIDGE_URL", "").rstrip("/")
_bridge_url = urlparse(BRIDGE)
if BRIDGE and (_bridge_url.scheme != "http" or _bridge_url.hostname not in {"127.0.0.1", "localhost"}):
    raise RuntimeError("POC_LOCAL_BRIDGE_URL must be a loopback HTTP URL")
MAX_JSON = 16_384
MAX_AUDIO = 2_000_000
MAX_FILE = 5_000_000
REMEMBER_SECONDS = 30 * 24 * 60 * 60
SHORT_SECONDS = 12 * 60 * 60
app = Flask(__name__, static_folder=None)
app.register_blueprint(yuling_bp)
app.config.update(
    SECRET_KEY=SESSION_SECRET or os.urandom(32),
    SESSION_COOKIE_NAME="yuling_preview",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=not bool(BRIDGE),
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    SESSION_REFRESH_EACH_REQUEST=False,
    MAX_CONTENT_LENGTH=MAX_FILE + 10_000,
)
_failures = defaultdict(deque)
_failure_lock = threading.Lock()
_page_read_lock = threading.Lock()


@app.before_request
def _protect_original_yuling_api():
    if request.path.startswith("/api/yuling/"):
        guard = _require_login(api=True)
        if guard:
            return guard
        if request.path == "/api/yuling/token" and request.method == "POST":
            if not _same_origin():
                return jsonify(error="来源不符"), 403
            request.environ["poc.same_origin"] = True
        # This value is written after the server validates its login cookie.
        # The token route must never derive memory ownership from browser JSON.
        request.environ["poc.authenticated_owner"] = session["account"]


def _configured():
    return bool(USERNAME and PASSWORD and SESSION_SECRET and len(SESSION_SECRET) >= 32)


def _credential_tag(account):
    if account == USERNAME:
        # Preserve cookies issued before optional test accounts were added.
        material = f"{USERNAME}\0{PASSWORD}"
    elif isinstance(account, str) and account in EXTRA_ACCOUNTS:
        material = f"extra\0{account}\0{EXTRA_ACCOUNTS[account]}"
    else:
        return None
    return hmac.new(SESSION_SECRET.encode(), material.encode(), sha256).hexdigest()[:24]


def _signed_in():
    account = session.get("account")
    tag = _credential_tag(account)
    return (_configured() and tag is not None and
            isinstance(session.get("credential_tag"), str) and
            hmac.compare_digest(session["credential_tag"], tag) and
            isinstance(session.get("until"), (int, float)) and session["until"] > time.time())


def _require_login(api=False):
    if _signed_in():
        return None
    if api:
        return jsonify(error="请先登录"), 401
    login_url = "/login?next=" + request.path if request.path in {"/call", "/chat"} else "/login"
    return redirect(login_url, code=303)


def _same_origin():
    origin = request.headers.get("Origin", "")
    scheme = "http" if BRIDGE else "https"
    return origin == f"{scheme}://{request.host}" and request.headers.get("Sec-Fetch-Site", "same-origin") != "cross-site"


@app.after_request
def _headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    if request.path.startswith("/api/") or response.mimetype == "text/html":
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/healthz")
def healthz():
    return ("", 200 if _configured() else 503)


@app.get("/login")
def login_page():
    if not _configured():
        abort(503)
    if _signed_in():
        requested = request.args.get("next")
        return redirect(requested if requested in {"/call", "/chat"} else "/chat", code=303)
    return send_from_directory(ROOT, "login.html")


@app.get("/call")
def original_call_page():
    guard = _require_login()
    if guard:
        return guard
    return send_from_directory(PROJECT_ROOT / "frontend", "index.html")


@app.get("/sense")
def sound_preview_page():
    guard = _require_login()
    if guard:
        return guard
    return send_from_directory(ROOT, "index.html")


@app.get("/chat")
def conversation_page():
    guard = _require_login()
    if guard:
        return guard
    return send_from_directory(PROJECT_ROOT / "frontend", "chat.html")


@app.get("/chat.css")
@app.get("/chat.js")
def conversation_asset():
    guard = _require_login()
    if guard:
        return guard
    return send_from_directory(PROJECT_ROOT / "frontend", request.path.lstrip("/"))


@app.get("/manifest.webmanifest")
def original_manifest():
    guard = _require_login()
    if guard:
        return guard
    return send_from_directory(PROJECT_ROOT / "frontend", "manifest.webmanifest")


@app.get("/yuling-assets/<path:filename>")
def original_yuling_asset(filename):
    guard = _require_login()
    if guard:
        return guard
    return send_from_directory(PROJECT_ROOT / "frontend" / "vendor", filename)


@app.get("/yuling-media/<path:filename>")
def original_yuling_media(filename):
    guard = _require_login()
    if guard:
        return guard
    if filename not in {"chen-dunni-loop.m4v", "chen-dunni-poster.jpg"}:
        abort(404)
    mimetype = "video/mp4" if filename.endswith(".m4v") else "image/jpeg"
    return send_from_directory(PROJECT_ROOT / "frontend" / "media", filename,
                               mimetype=mimetype, conditional=True)


@app.post("/api/login")
def login():
    if not _configured():
        return jsonify(error="登录服务未设置"), 503
    if not _same_origin() or request.mimetype != "application/json" or request.content_length is None or request.content_length > 4096:
        return jsonify(error="请求格式不正确"), 403
    key = request.remote_addr or "unknown"
    now = time.time()
    with _failure_lock:
        recent = _failures[key]
        while recent and now - recent[0] > 900:
            recent.popleft()
        if len(recent) >= 8:
            return jsonify(error="请稍后再试"), 429
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="请求格式不正确"), 400
    username, password = data.get("username"), data.get("password")
    if not isinstance(username, str) or not isinstance(password, str):
        return jsonify(error="帐号或密码不正确"), 401
    if username == USERNAME:
        correct = hmac.compare_digest(password, PASSWORD)
    elif username in EXTRA_ACCOUNTS:
        correct = check_password_hash(EXTRA_ACCOUNTS[username], password)
    else:
        correct = False
    if not correct:
        with _failure_lock:
            _failures[key].append(now)
        return jsonify(error="帐号或密码不正确"), 401
    with _failure_lock:
        _failures.pop(key, None)
    remember = data.get("remember") is True
    session.clear()
    session.permanent = remember
    session["account"] = username
    session["credential_tag"] = _credential_tag(username)
    session["until"] = now + (REMEMBER_SECONDS if remember else SHORT_SECONDS)
    return jsonify(ok=True, remember=remember)


@app.post("/api/logout")
def logout():
    guard = _require_login(api=True)
    if guard:
        return guard
    if not _same_origin():
        return jsonify(error="来源不符"), 403
    session.clear()
    return jsonify(ok=True)


def _bridge(path, body=None, content_type=None):
    headers = {"Host": _bridge_url.netloc, "Origin": BRIDGE}
    if content_type:
        headers["Content-Type"] = content_type
    call = Request(BRIDGE + path, data=body, headers=headers,
                   method="POST" if body is not None else "GET")
    try:
        with urlopen(call, timeout=90) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        try:
            return exc.code, json.load(exc)
        except (ValueError, UnicodeDecodeError):
            return exc.code, {"error": "本机桥接服务失败"}
    except (URLError, TimeoutError):
        return 503, {"error": "本机桥接服务无法连接"}


def _chat_store():
    try:
        owner_id = session.get("account")
        if not isinstance(owner_id, str) or not owner_id:
            return None
        return ChatStore(os.environ.get("POC_CHAT_DB_PATH", ""), owner_id,
                         legacy_owner_id=os.environ.get("POC_LEGACY_OWNER", USERNAME))
    except (StoreUnavailable, OSError):
        return None


def _uuid(value):
    try:
        return str(UUID(value)) if isinstance(value, str) else None
    except ValueError:
        return None


def _v2_guard(write=False):
    guard = _require_login(api=True)
    if guard:
        return guard
    if write and not _same_origin():
        return jsonify(error="来源不符"), 403
    return None


@app.get("/api/v2/status")
def conversation_status():
    guard = _v2_guard()
    if guard:
        return guard
    store = _chat_store()
    store_path = os.path.realpath(str(store.path)) if store else ""
    temporary = store_path.startswith(("/tmp/", "/private/tmp/"))
    persistence = ("unavailable" if store is None else
                   "ephemeral" if temporary else "path_configured_unverified")
    return jsonify(text_model=bool(os.environ.get("MINIMAX_API_KEY", "").strip()),
                   persistent_history=store is not None and not temporary,
                   memory_persistence=persistence,
                   image_upload=store is not None,
                   image_understanding=bool(os.environ.get("MINIMAX_API_KEY", "").strip()),
                   image_understanding_verified=False, vector_search=False,
                   sourced_knowledge=store is not None,
                   graph_reasoning=False, hermes=False)


@app.post("/api/v2/upload-image")
def upload_chat_image():
    guard = _v2_guard(write=True)
    if guard:
        return guard
    store = _chat_store()
    if store is None:
        return jsonify(error="持久化存储尚未配置"), 503
    file = request.files.get("file")
    if file is None:
        return jsonify(error="请选择图片"), 400
    data = file.stream.read(4_000_001)
    if not data or len(data) > 4_000_000:
        return jsonify(error="图片大小需在 4 MB 内"), 413
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 20_000_000:
                return jsonify(error="图片像素过大"), 413
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 20_000_000:
                return jsonify(error="图片像素过大"), 413
            mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(image.format)
            if mime is None:
                return jsonify(error="仅支持 PNG、JPEG、WebP"), 415
            # Re-encode pixels into a fresh image. This removes EXIF/GPS and
            # other embedded metadata before any bytes enter the database.
            image = ImageOps.exif_transpose(image)
            image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
            mode = "RGBA" if image.mode in {"RGBA", "LA"} else "RGB"
            fresh = Image.new(mode, image.size)
            fresh.paste(image.convert(mode))
            cleaned = io.BytesIO()
            if mime == "image/jpeg":
                fresh.convert("RGB").save(cleaned, "JPEG", quality=85)
            elif mime == "image/webp":
                fresh.save(cleaned, "WEBP", quality=85)
            else:
                fresh.save(cleaned, "PNG", optimize=True)
            data = cleaned.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return jsonify(error="图片格式不正确"), 400
    if len(data) > 4_000_000:
        return jsonify(error="处理后图片超过 4 MB"), 413
    iid = store.put_image(data, mime)
    return jsonify(image_id=iid, url=f"/api/v2/image/{iid}", mime_type=mime,
                   image_understanding="待发送并验证"), 201


@app.get("/api/v2/image/<image_id>")
def chat_image(image_id):
    guard = _v2_guard()
    if guard:
        return guard
    store = _chat_store()
    if store is None:
        return jsonify(error="持久化存储尚未配置"), 503
    iid = _uuid(image_id)
    row = store.get_image(iid) if iid else None
    if row is None:
        abort(404)
    return send_file(io.BytesIO(row[1]), mimetype=row[0], conditional=False,
                     download_name="image")


@app.get("/api/v2/history")
def conversation_history():
    guard = _v2_guard()
    if guard:
        return guard
    store = _chat_store()
    if store is None:
        return jsonify(error="持久化存储尚未配置"), 503
    sid = _uuid(request.args.get("session_id"))
    if request.args.get("session_id") and not sid:
        return jsonify(error="会话编号不正确"), 400
    if sid:
        if not store.has_session(sid):
            return jsonify(error="会话不存在"), 404
        return jsonify(session_id=sid, events=store.history(sid))
    return jsonify(sessions=store.recent_sessions(), events=[])


@app.post("/api/v2/page-read")
def read_conversation_page():
    """Opt-in read of an existing user link card; never feeds text to the model."""
    guard = _v2_guard(write=True)
    if guard:
        return guard
    if os.environ.get("POC_PUBLIC_WEB_READ_ENABLED") != "1":
        return jsonify(error="网页读取尚未启用"), 503
    if (request.mimetype != "application/json" or not request.content_length or
            request.content_length > 1024):
        return jsonify(error="网页读取请求格式不正确"), 415
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {"source_event_id"}:
        return jsonify(error="只需提供来源消息编号"), 400
    source_event_id = _uuid(data["source_event_id"])
    if not source_event_id:
        return jsonify(error="来源消息编号不正确"), 400
    store = _chat_store()
    if store is None:
        return jsonify(error="对话存储尚未配置"), 503
    source_url = store.page_read_source(source_event_id)
    if source_url is None:
        return jsonify(error="来源消息不存在或没有网址"), 404
    try:
        _, canonical_url = _validated_url(source_url)
    except PageReadError:
        return jsonify(error="仅支持公开 HTTPS 网页"), 400
    if not _page_read_lock.acquire(blocking=False):
        return jsonify(error="网页读取忙，请稍后再试"), 429
    try:
        try:
            page_id, remaining = store.reserve_page_read(source_event_id, canonical_url)
        except StoreUnavailable:
            return jsonify(error="来源消息不存在或没有网址"), 404
        except PageReadQuotaExceeded:
            return jsonify(error="今日网页读取次数已用完"), 429
        try:
            result = read_public_page(source_url)
        except PageReadError:
            store.finish_page_read(page_id)
            return jsonify(page_read=store.page_read(page_id),
                           remaining_today=remaining,
                           error="网页暂时无法读取"), 502
        except Exception:
            # Unexpected parser/transport failures still consume the attempt
            # and leave an honest failed artifact, without exposing details.
            store.finish_page_read(page_id)
            return jsonify(page_read=store.page_read(page_id),
                           remaining_today=remaining,
                           error="网页暂时无法读取"), 502
        store.finish_page_read(page_id, result)
        return jsonify(page_read=store.page_read(page_id),
                       remaining_today=remaining), 201
    finally:
        _page_read_lock.release()


@app.get("/api/v2/page-read/<page_id>")
def get_conversation_page_read(page_id):
    guard = _v2_guard()
    if guard:
        return guard
    if os.environ.get("POC_PUBLIC_WEB_READ_ENABLED") != "1":
        return jsonify(error="网页读取尚未启用"), 503
    store = _chat_store()
    if store is None:
        return jsonify(error="对话存储尚未配置"), 503
    artifact = store.page_read(_uuid(page_id)) if _uuid(page_id) else None
    if artifact is None:
        return jsonify(error="网页读取记录不存在"), 404
    return jsonify(page_read=artifact)


@app.post("/api/v2/call-transcript")
def save_call_transcript():
    """Import final LiveKit segments observed by this authenticated call browser."""
    guard = _v2_guard(write=True)
    if guard:
        return guard
    if request.mimetype != "application/json" or not request.content_length or request.content_length > MAX_JSON:
        return jsonify(error="逐字稿格式不正确"), 415
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="逐字稿格式不正确"), 400
    call_id = data.get("call_id")
    speaker_identity = data.get("speaker_identity")
    segment_id = data.get("segment_id")
    role = data.get("role")
    text_value = data.get("text")
    if (not isinstance(call_id, str) or not 1 <= len(call_id) <= 128 or
            not call_id.startswith("ailivex-yuling-") or
            not re.fullmatch(r"[A-Za-z0-9_-]+", call_id)):
        return jsonify(error="通话编号不正确"), 400
    if (not isinstance(speaker_identity, str) or not 1 <= len(speaker_identity) <= 128 or
            not re.fullmatch(r"[A-Za-z0-9_.:-]+", speaker_identity)):
        return jsonify(error="说话者编号不正确"), 400
    if (not isinstance(segment_id, str) or not 1 <= len(segment_id) <= 128 or
            not re.fullmatch(r"[A-Za-z0-9_.:-]+", segment_id)):
        return jsonify(error="片段编号不正确"), 400
    if role not in {"user", "assistant"}:
        return jsonify(error="说话者类型不正确"), 400
    if not isinstance(text_value, str) or not 1 <= len(text_value.strip()) <= 4000:
        return jsonify(error="逐字稿长度不正确"), 400
    sid_raw = data.get("session_id")
    sid = _uuid(sid_raw) if sid_raw else None
    if sid_raw and not sid:
        return jsonify(error="会话编号不正确"), 400
    store = _chat_store()
    if store is None:
        return jsonify(error="对话存储尚未配置"), 503
    try:
        event, created = store.add_call_transcript(
            sid, call_id, speaker_identity, segment_id, role, text_value.strip())
    except StoreUnavailable:
        return jsonify(error="会话不存在"), 404
    return jsonify(session_id=event["session_id"], event=event,
                   created=created), 201 if created else 200


@app.post("/api/v2/chat")
def conversation_chat():
    guard = _v2_guard(write=True)
    if guard:
        return guard
    if request.mimetype != "application/json" or not request.content_length or request.content_length > MAX_JSON:
        return jsonify(error="文字格式不正确"), 415
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="文字格式不正确"), 400
    text_value = data.get("text", "")
    if not isinstance(text_value, str) or len(text_value) > 4000:
        return jsonify(error="文字最多 4000 字"), 400
    text_value = text_value.strip()
    image_id = _uuid(data.get("image_id")) if data.get("image_id") else None
    if data.get("image_id") and not image_id:
        return jsonify(error="图片编号不正确"), 400
    page_read_id = _uuid(data.get("page_read_id")) if data.get("page_read_id") else None
    if data.get("page_read_id") and not page_read_id:
        return jsonify(error="网页读取编号不正确"), 400
    if page_read_id and os.environ.get("POC_PUBLIC_WEB_READ_ENABLED") != "1":
        return jsonify(error="网页读取尚未启用"), 503
    link_url = data.get("url")
    if link_url is not None:
        if (not isinstance(link_url, str) or len(link_url) > 2048 or
                urlparse(link_url).scheme not in {"http", "https"} or not urlparse(link_url).hostname):
            return jsonify(error="网址格式不正确"), 400
    if not text_value and not image_id and not link_url and not page_read_id:
        return jsonify(error="请输入文字、图片或网址"), 400
    store = _chat_store()
    if store is None:
        return jsonify(error="持久化存储尚未配置"), 503
    if not os.environ.get("MINIMAX_API_KEY", "").strip():
        return jsonify(error="对话模型尚未配置"), 503
    image = store.get_image(image_id) if image_id else None
    if image_id and image is None:
        return jsonify(error="图片不存在"), 404
    sid_raw = data.get("session_id")
    sid = _uuid(sid_raw) if sid_raw else None
    if sid_raw and not sid:
        return jsonify(error="会话编号不正确"), 400
    if page_read_id and sid is None:
        return jsonify(error="引用网页需要原会话编号"), 400
    if sid and not store.has_session(sid):
        return jsonify(error="会话不存在"), 404
    page_context = store.usable_page_context(page_read_id, sid) if page_read_id else None
    if page_read_id and page_context is None:
        return jsonify(error="网页读取记录不存在或已过期"), 404
    sid = sid or store.create_session()
    history = store.history(sid, limit=20)
    try:
        user_event = store.add_event(sid, "user", text_value, image_id=image_id,
                                     link_url=link_url, source_kind="user_explicit",
                                     page_read_id=page_read_id)
    except StoreUnavailable:
        return jsonify(error="网页读取记录不存在或已过期"), 404
    try:
        answer = reply_to(text_value, history, store.memories(), image=image,
                          link_url=link_url, page_context=page_context)
    except ProviderError as exc:
        return jsonify(error=str(exc), session_id=sid, user_event=user_event), 502
    assistant_event = store.add_event(sid, "assistant", answer,
                                      source_kind="model_generated",
                                      page_read_id=page_read_id)
    return jsonify(session_id=sid, reply=answer,
                   user_event=user_event, assistant_event=assistant_event)


@app.get("/api/v2/memories")
def explicit_memories():
    guard = _v2_guard()
    if guard:
        return guard
    store = _chat_store()
    if store is None:
        return jsonify(error="持久化存储尚未配置"), 503
    return jsonify(memories=store.memories())


@app.post("/api/v2/memories")
def save_explicit_memory():
    guard = _v2_guard(write=True)
    if guard:
        return guard
    data = request.get_json(silent=True) if request.mimetype == "application/json" else None
    if not isinstance(data, dict) or not isinstance(data.get("text"), str) or not 1 <= len(data["text"].strip()) <= 500:
        return jsonify(error="记忆文字需在 1 至 500 字内"), 400
    store = _chat_store()
    if store is None:
        return jsonify(error="持久化存储尚未配置"), 503
    mid = store.add_memory(data["text"].strip())
    return jsonify(id=mid, source_kind="user_explicit"), 201


@app.delete("/api/v2/memories/<memory_id>")
def forget_explicit_memory(memory_id):
    guard = _v2_guard(write=True)
    if guard:
        return guard
    store = _chat_store()
    if store is None:
        return jsonify(error="持久化存储尚未配置"), 503
    mid = _uuid(memory_id)
    if not mid or not store.delete_memory(mid):
        return jsonify(error="记忆不存在"), 404
    return jsonify(ok=True)


@app.get("/api/v2/knowledge")
def list_knowledge_items():
    guard = _v2_guard()
    if guard:
        return guard
    store = _chat_store()
    if store is None:
        return jsonify(error="知识存储尚未配置"), 503
    return jsonify(items=store.knowledge_items())


@app.post("/api/v2/knowledge")
def save_knowledge_item():
    """Save a user-supplied note linked to its original conversation event."""
    guard = _v2_guard(write=True)
    if guard:
        return guard
    if request.mimetype != "application/json" or not request.content_length or request.content_length > MAX_JSON:
        return jsonify(error="知识格式不正确"), 415
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="知识格式不正确"), 400
    title, content = data.get("title"), data.get("content")
    source_event_id = _uuid(data.get("source_event_id"))
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120:
        return jsonify(error="标题需在 1 至 120 字内"), 400
    if not isinstance(content, str) or not 1 <= len(content.strip()) <= 4000:
        return jsonify(error="内容需在 1 至 4000 字内"), 400
    if not source_event_id:
        return jsonify(error="请提供本账号的来源消息"), 400
    store = _chat_store()
    if store is None:
        return jsonify(error="知识存储尚未配置"), 503
    try:
        item = store.add_knowledge_item(title.strip(), content.strip(),
                                        source_event_id, source_kind="user_explicit")
    except StoreUnavailable:
        return jsonify(error="来源消息不存在"), 404
    return jsonify(item=item), 201


@app.delete("/api/v2/knowledge/<item_id>")
def delete_knowledge_item(item_id):
    guard = _v2_guard(write=True)
    if guard:
        return guard
    store = _chat_store()
    if store is None:
        return jsonify(error="知识存储尚未配置"), 503
    kid = _uuid(item_id)
    if not kid or not store.delete_knowledge_item(kid):
        return jsonify(error="知识条目不存在"), 404
    return jsonify(ok=True)


@app.get("/api/v2/relations")
def list_relation_candidates():
    guard = _v2_guard()
    if guard:
        return guard
    store = _chat_store()
    if store is None:
        return jsonify(error="关系存储尚未配置"), 503
    return jsonify(items=store.relation_candidates(), status="candidate_only")


@app.post("/api/v2/relations")
def save_relation_candidate():
    """Record an explicitly sourced possibility, not an established cause."""
    guard = _v2_guard(write=True)
    if guard:
        return guard
    if request.mimetype != "application/json" or not request.content_length or request.content_length > MAX_JSON:
        return jsonify(error="关系格式不正确"), 415
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="关系格式不正确"), 400
    subject_id = _uuid(data.get("subject_id"))
    object_id = _uuid(data.get("object_id"))
    source_event_id = _uuid(data.get("source_event_id"))
    predicate = data.get("predicate")
    if not subject_id or not object_id or not source_event_id:
        return jsonify(error="请提供本账号的知识条目与来源消息"), 400
    if not isinstance(predicate, str) or not 1 <= len(predicate.strip()) <= 120:
        return jsonify(error="关系描述需在 1 至 120 字内"), 400
    store = _chat_store()
    if store is None:
        return jsonify(error="关系存储尚未配置"), 503
    try:
        relation = store.add_relation_candidate(subject_id, predicate.strip(),
                                                object_id, source_event_id)
    except StoreUnavailable:
        return jsonify(error="知识条目或来源消息不存在"), 404
    return jsonify(item=relation), 201


@app.delete("/api/v2/relations/<relation_id>")
def delete_relation_candidate(relation_id):
    guard = _v2_guard(write=True)
    if guard:
        return guard
    store = _chat_store()
    if store is None:
        return jsonify(error="关系存储尚未配置"), 503
    rid = _uuid(relation_id)
    if not rid or not store.delete_relation_candidate(rid):
        return jsonify(error="关系候选不存在"), 404
    return jsonify(ok=True)


def extract_document(filename, data):
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise ValueError("PDF 格式不正确")
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise ValueError("加密 PDF 尚不支持")
        if len(reader.pages) > 20:
            raise ValueError("PDF 最多 20 页")
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
        kind = "PDF"
    elif ext in {".docx", ".pptx"}:
        kind = "Word" if ext == ".docx" else "PowerPoint"
        parts = []
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            if len(names) > 1000 or sum(x.file_size for x in archive.infolist()) > 20_000_000:
                raise ValueError("文件内容过大")
            if ext == ".docx":
                targets = [n for n in names if re.fullmatch(r"word/(document|header\d+|footer\d+)\.xml", n)]
            else:
                targets = sorted((n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                                 key=lambda n: int(re.search(r"slide(\d+)", n).group(1)))
            for name in targets[:100]:
                raw = archive.read(name)
                if len(raw) > 2_000_000:
                    raise ValueError("单页内容过大")
                root = ET.fromstring(raw)
                parts.append(" ".join(node.text.strip() for node in root.iter()
                                      if node.tag in {"{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t",
                                                      "{http://schemas.openxmlformats.org/drawingml/2006/main}t"}
                                      and node.text and node.text.strip()))
        text = "\n".join(parts)
    else:
        raise ValueError("仅支持 PDF、DOCX、PPTX")
    clean = re.sub(r"[ \t]+", " ", text).strip()
    if not clean:
        return {"kind": kind, "summary": "未抽出文字；扫描图片或图表内容尚未支持。", "characters": 0}
    preview = clean[:2500]
    if len(clean) > 2500:
        preview += "…（仅显示前 2500 字）"
    return {"kind": kind, "summary": preview, "characters": len(clean),
            "note": "这是文字抽取预览，尚未经模型理解或摘要。"}


@app.get("/api/capabilities")
def capabilities():
    guard = _require_login(api=True)
    if guard:
        return guard
    ready = False
    if BRIDGE:
        status, payload = _bridge("/api/status")
        ready = status == 200 and bool(payload.get("ready"))
    vision_ready = ready and os.path.exists("/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk")
    return jsonify(
        text=ready, turn_voice=ready, live_voice=False,
        image=vision_ready, files=True, sound_events=True,
        image_mode="本机 OCR 与粗分类" if vision_ready else "未接入",
        file_mode="PDF/DOCX/PPTX 文字抽取预览",
        mode="local_bridge" if BRIDGE else "railway_preview",
        reason="本机 Hermes/Whisper 已连接" if ready else "云端尚未接入对话模型；生活声辨识在手机端执行",
    )


@app.post("/api/analyze")
def analyze():
    guard = _require_login(api=True)
    if guard:
        return guard
    if not _same_origin():
        return jsonify(error="来源不符"), 403
    file = request.files.get("file")
    if file is None:
        return jsonify(error="请上传一个文件"), 400
    data = file.stream.read(MAX_FILE + 1)
    if not 0 < len(data) <= MAX_FILE:
        return jsonify(error="文件大小需在 5 MB 内"), 413
    content_type = (file.content_type or "").lower()
    if content_type in {"image/png", "image/jpeg", "image/webp"}:
        if not BRIDGE:
            return jsonify(error="此网址尚未接入图片辨识"), 503
        if len(data) > 4_000_000:
            return jsonify(error="图片需在 4 MB 内"), 413
        status, result = _bridge("/api/analyze", data, content_type)
        if status != 200:
            return jsonify(result), status
        words = ", ".join(result.get("text", []))[:2000]
        labels = ", ".join(str(x.get("name", "")) for x in result.get("labels", [])[:3])
        return jsonify(kind="image", summary=f"图片文字：{words or '未辨识到'}。粗分类：{labels or '未辨识到'}。",
                       note="本机 Vision OCR 与粗分类结果；尚非完整图片问答。")
    try:
        return jsonify(extract_document(file.filename or "", data))
    except (ValueError, zipfile.BadZipFile, ET.ParseError, ImportError) as exc:
        return jsonify(error=str(exc)), 400
    except Exception:
        return jsonify(error="无法抽取此文件的文字"), 422


@app.post("/api/chat")
def chat():
    guard = _require_login(api=True)
    if guard:
        return guard
    if not _same_origin():
        return jsonify(error="来源不符"), 403
    if not BRIDGE:
        return jsonify(error="此 Railway 预览尚未接入对话服务"), 503
    if request.mimetype != "application/json" or not request.content_length or request.content_length > MAX_JSON:
        return jsonify(error="文字格式不正确"), 415
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="文字格式不正确"), 400
    text = str(body.get("message", ""))[:4000]
    status, result = _bridge("/api/chat", json.dumps({"text": text, "history": []}).encode(), "application/json")
    return jsonify(result), status


@app.post("/api/transcribe")
def transcribe():
    guard = _require_login(api=True)
    if guard:
        return guard
    if not _same_origin():
        return jsonify(error="来源不符"), 403
    if not BRIDGE:
        return jsonify(error="此 Railway 预览尚未接入回合语音"), 503
    if request.mimetype != "audio/wav" or not request.content_length or not 44 <= request.content_length <= MAX_AUDIO:
        return jsonify(error="音讯格式不正确"), 415
    status, result = _bridge("/api/transcribe", request.get_data(), "audio/wav")
    return jsonify(result), status


@app.get("/")
@app.get("/<path:filename>")
def files(filename=""):
    if not filename:
        guard = _require_login()
        if guard:
            return guard
        return redirect("/chat", code=303)
    public = {"style.css", "login.js", "assets/icon.svg", "assets/icon-180.png"}
    if filename not in public:
        guard = _require_login()
        if guard:
            return guard
    if filename in {"login.html", "server.py"} or filename.startswith("."):
        abort(404)
    return send_from_directory(ROOT, filename)


@app.errorhandler(413)
def too_large(_error):
    if request.path.startswith("/api/"):
        return jsonify(error="文件大小超过限制"), 413
    return "文件大小超过限制", 413


if __name__ == "__main__":
    if not _configured():
        raise SystemExit("Preview login variables are not configured")
    port = int(os.environ.get("PORT", "8080"))
    host = "127.0.0.1" if BRIDGE else "0.0.0.0"
    app.run(host=host, port=port, threaded=True)
