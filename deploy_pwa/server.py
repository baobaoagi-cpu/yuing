"""Single-owner preview login and PWA API. This is not a multi-user account system."""
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
from flask import Flask, abort, jsonify, redirect, request, send_from_directory, session

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))
from routes import yuling_bp  # noqa: E402
USERNAME = os.environ.get("POC_PREVIEW_USER", "")
PASSWORD = os.environ.get("POC_PREVIEW_PASSWORD", "")
SESSION_SECRET = os.environ.get("POC_SESSION_SECRET", "")
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


@app.before_request
def _protect_original_yuling_api():
    if request.path.startswith("/api/yuling/"):
        return _require_login(api=True)


def _configured():
    return bool(USERNAME and PASSWORD and SESSION_SECRET and len(SESSION_SECRET) >= 32)


def _credential_tag():
    return hmac.new(SESSION_SECRET.encode(), f"{USERNAME}\0{PASSWORD}".encode(), sha256).hexdigest()[:24]


def _signed_in():
    return (_configured() and session.get("account") == USERNAME and
            session.get("credential_tag") == _credential_tag() and
            isinstance(session.get("until"), (int, float)) and session["until"] > time.time())


def _require_login(api=False):
    if _signed_in():
        return None
    if api:
        return jsonify(error="请先登录"), 401
    login_url = "/login?next=/call" if request.path == "/call" else "/login"
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
        return redirect("/call" if request.args.get("next") == "/call" else "/", code=303)
    return send_from_directory(ROOT, "login.html")


@app.get("/call")
def original_call_page():
    guard = _require_login()
    if guard:
        return guard
    return send_from_directory(PROJECT_ROOT / "frontend", "index.html")


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
    correct = hmac.compare_digest(username, USERNAME) & hmac.compare_digest(password, PASSWORD)
    if not correct:
        with _failure_lock:
            _failures[key].append(now)
        return jsonify(error="帐号或密码不正确"), 401
    with _failure_lock:
        _failures.pop(key, None)
    remember = data.get("remember") is True
    session.clear()
    session.permanent = remember
    session["account"] = USERNAME
    session["credential_tag"] = _credential_tag()
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
def files(filename="index.html"):
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
