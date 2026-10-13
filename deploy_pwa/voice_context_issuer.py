"""Server-only issuer for the optional, room-bound voice memory context.

Never pass browser-supplied owner IDs, memory text, or room names to this module.
The receiving agent validates the envelope again before using any context.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def issue_voice_context(secret: str, room: str, owner_id: str,
                        memories: list[dict], now: int | None = None) -> str:
    """Return agent metadata containing only bounded, explicit saved memories."""
    if not isinstance(secret, str) or len(secret.encode("utf-8")) < 32:
        raise ValueError("voice context signing key is missing or too short")
    if not isinstance(room, str) or not room.startswith("ailivex-yuling-"):
        raise ValueError("invalid voice room")
    if not isinstance(owner_id, str) or not owner_id:
        raise ValueError("authenticated owner is required")
    clean = []
    for item in memories:
        if (isinstance(item, dict) and item.get("source_kind") == "user_explicit" and
                isinstance(item.get("text"), str) and 1 <= len(item["text"]) <= 400):
            clean.append({"text": item["text"], "source_kind": "user_explicit"})
        if len(clean) == 8:
            break
    issued = int(time.time()) if now is None else int(now)
    claims = {
        "version": 1,
        "room": room,
        "owner_id": owner_id,
        "issued_at": issued,
        "expires_at": issued + 90,
        "memories": clean,
    }
    # The receiving agent rejects envelopes longer than 11,000 characters.
    # Eight 400-character CJK memories can exceed that after base64 encoding.
    while True:
        raw = json.dumps(claims, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).digest()
        token = _b64url(raw) + "." + _b64url(signature)
        if len(token) <= 11_000 or not clean:
            break
        clean.pop()
    return json.dumps({"voice_context": token}, separators=(",", ":"))
