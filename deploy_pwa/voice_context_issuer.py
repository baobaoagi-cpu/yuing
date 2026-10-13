"""Server-only issuer for optional, room-bound voice account and memory context.

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
                        memories: list[dict], now: int | None = None,
                        account_seed: dict | None = None) -> str:
    """Sign the authenticated owner and bounded, explicit saved memories."""
    if not isinstance(secret, str) or len(secret.encode("utf-8")) < 32:
        raise ValueError("voice context signing key is missing or too short")
    if not isinstance(room, str) or not room.startswith("ailivex-yuling-"):
        raise ValueError("invalid voice room")
    if not isinstance(owner_id, str) or not owner_id:
        raise ValueError("authenticated owner is required")
    clean = []
    for item in memories:
        if (isinstance(item, dict) and item.get("source_kind") == "user_explicit" and
                isinstance(item.get("text"), str) and 1 <= len(item["text"]) <= 500):
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
    if owner_id == "MING" and isinstance(account_seed, dict):
        claims["account_seed"] = {
            field: {"value": item["value"], "source_kind": item["source_kind"],
                    "source_date": item["source_date"], "confidence": item["confidence"]}
            for field, item in account_seed.items()
            if field in {"display_name", "gender", "occupation", "expertise", "relationship", "collaboration", "account_purpose"}
            and isinstance(item, dict) and item.get("status") == "active"
            and isinstance(item.get("value"), str) and 1 <= len(item["value"]) <= 120
            and item.get("source_kind") in {"account_setup_user_provided", "authenticated_user_correction"}
        }
    # The receiving agent rejects envelopes longer than 11,000 characters.
    # Eight 500-character CJK memories can exceed that after base64 encoding.
    while True:
        raw = json.dumps(claims, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).digest()
        token = _b64url(raw) + "." + _b64url(signature)
        if len(token) <= 11_000 or not clean:
            break
        clean.pop()
    return json.dumps({"voice_context": token}, separators=(",", ":"))
