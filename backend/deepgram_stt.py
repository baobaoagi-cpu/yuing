import json
import os
import urllib.parse
import urllib.request


def _deepgram_key() -> str:
    return os.environ.get("DEEPGRAM_API_KEY", "").strip()


def _deepgram_listen(audio_bytes: bytes, mime_type: str, params: dict,
                     timeout: int = 60) -> dict:
    if not audio_bytes:
        return {}
    key = _deepgram_key()
    if not key:
        return {}

    req = urllib.request.Request(
        "https://api.deepgram.com/v1/listen?" + urllib.parse.urlencode(params),
        data=audio_bytes,
        headers={
            "Authorization": f"Token {key}",
            "Content-Type": mime_type or "audio/webm",
            "User-Agent": "taiyi-yuling-stt/1.0 (+python/urllib)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return {}


def transcribe_prerecorded(audio_bytes: bytes, mime_type: str = "audio/webm",
                           language: str = "zh-TW",
                           model: str = "nova-3") -> dict:
    """Transcribe one prerecorded chunk with Deepgram and return transcript plus summary."""
    raw = _deepgram_listen(audio_bytes, mime_type, {
        "model": model,
        "smart_format": "true",
        "punctuate": "true",
        "paragraphs": "true",
        "utterances": "true",
        "summarize": "true",
        "language": language,
    }, timeout=60)
    if not raw:
        return {}

    results = raw.get("results") or {}
    channel = ((results.get("channels") or [{}])[0] or {})
    alternative = ((channel.get("alternatives") or [{}])[0] or {})
    summary = results.get("summary") or {}
    return {
        "transcript": (alternative.get("transcript") or "").strip(),
        "summary": (summary.get("short") or "").strip(),
        "utterances": results.get("utterances") or [],
        "request_id": ((raw.get("metadata") or {}).get("request_id") or "").strip(),
        "model": model,
    }


def transcribe_live_slice(audio_bytes: bytes, mime_type: str = "audio/webm",
                          language: str = "zh-TW",
                          model: str = "nova-3") -> dict:
    """Transcribe a short live slice quickly for near-realtime captions."""
    raw = _deepgram_listen(audio_bytes, mime_type, {
        "model": model,
        "smart_format": "true",
        "punctuate": "true",
        "paragraphs": "false",
        "utterances": "false",
        "summarize": "false",
        "language": language,
    }, timeout=20)
    if not raw:
        return {}

    results = raw.get("results") or {}
    channel = ((results.get("channels") or [{}])[0] or {})
    alternative = ((channel.get("alternatives") or [{}])[0] or {})
    return {
        "transcript": (alternative.get("transcript") or "").strip(),
        "summary": "",
        "utterances": [],
        "request_id": ((raw.get("metadata") or {}).get("request_id") or "").strip(),
        "model": model,
    }
