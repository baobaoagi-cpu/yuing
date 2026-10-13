"""Loopback-only local voice/text experiment. No cloud, memory writes or tools."""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading

from bridge import HERE, PublicContext, answer, hermes_profile_status

WEB = HERE / "web"
ALLOWED = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js",
           "/style.css": "style.css"}
MAX_JSON = 16_384
MAX_AUDIO = 2_000_000
_whisper = None
_whisper_lock = threading.Lock()
_answer_lock = threading.Lock()
_context = None


def transcribe_wav(data: bytes) -> str:
    global _whisper
    import psutil
    if psutil.virtual_memory().available < 4 * 2**30:
        raise RuntimeError("本機可用記憶體不足 4 GB，已停止本次辨識。")
    if not data.startswith(b"RIFF") or data[8:12] != b"WAVE":
        raise ValueError("請提供 WAV 音訊。")
    # Reuse only the model already cached by the existing local voice runtime.
    model_dir = Path.home() / ".cache/huggingface/hub/models--Systran--faster-whisper-small/snapshots/536b0662742c02347bc0e980a01041f333bce120"
    if not all((model_dir / part).is_file() for part in ("model.bin", "config.json")):
        raise RuntimeError("本機 Whisper small 尚未快取；本 POC 不自動下載。")
    with _whisper_lock:
        if _whisper is None:
            from faster_whisper import WhisperModel
            _whisper = WhisperModel(str(model_dir), device="cpu", compute_type="int8")
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as file:
            file.write(data)
            file.flush()
            segments, _ = _whisper.transcribe(file.name, language="zh", beam_size=3,
                vad_filter=True, condition_on_previous_text=False,
                no_speech_threshold=0.6, log_prob_threshold=-0.8)
            result = "".join(segment.text for segment in segments).strip()
    return result[:500]


class Handler(BaseHTTPRequestHandler):
    def _local_host(self):
        port = self.server.server_port
        return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _same_origin(self):
        port = self.server.server_port
        return self.headers.get("Origin", "") in (f"http://127.0.0.1:{port}",
                                                   f"http://localhost:{port}")

    def _json(self, status, data):
        raw = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if not self._local_host():
            return self._json(403, {"error": "forbidden host"})
        if self.path == "/api/status":
            try:
                return self._json(200, hermes_profile_status())
            except Exception as exc:
                return self._json(503, {"ready": False, "error": str(exc)})
        filename = ALLOWED.get(self.path)
        if filename is None:
            return self._json(404, {"error": "not found"})
        path = WEB / filename
        data = path.read_bytes()
        mime = "text/html; charset=utf-8" if filename.endswith(".html") else (
            "text/css; charset=utf-8" if filename.endswith(".css") else "application/javascript; charset=utf-8")
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if not self._local_host() or not self._same_origin():
            return self._json(403, {"error": "forbidden origin"})
        expected_type = {"/api/chat": "application/json", "/api/transcribe": "audio/wav"}.get(self.path)
        if expected_type and self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != expected_type:
            return self._json(415, {"error": "unsupported content type"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if self.path == "/api/chat":
                if not 0 < length <= MAX_JSON:
                    raise ValueError("文字請求大小不正確。")
                body = json.loads(self.rfile.read(length))
                with _answer_lock:
                    return self._json(200, answer(str(body.get("text", "")), _context,
                                                  body.get("history", [])))
            if self.path == "/api/transcribe":
                if not 44 <= length <= MAX_AUDIO:
                    raise ValueError("音訊大小不正確。")
                return self._json(200, {"text": transcribe_wav(self.rfile.read(length))})
            return self._json(404, {"error": "not found"})
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return self._json(400, {"error": str(exc)})
        except Exception as exc:
            return self._json(503, {"error": f"本機服務暫時無法處理：{type(exc).__name__}"})


def main():
    global _context
    _context = PublicContext()
    port = int(os.environ.get("YUING_LOCAL_PORT", "9135"))
    print(f"yuing 本機語音 POC：http://127.0.0.1:{port}/", flush=True)
    try:
        ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    finally:
        _context.close()


if __name__ == "__main__":
    main()
