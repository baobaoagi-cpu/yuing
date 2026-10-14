"""Separate, account-scoped voice usage ledger for LiveKit Agent SDK 1.8.6.

Only server-verified room ownership may be passed to ``begin_call``. The
browser's identity, room metadata before signature verification, and spoken
names are not ownership evidence. This module deliberately stores no speech.

``session_usage_updated`` contains cumulative SDK measurements, not provider
invoices. Upsert the latest snapshot per model instead of adding snapshots.
Costs are estimates only when an operator supplies an explicit rate snapshot;
otherwise cost is unknown. A persistent volume is required in deployment.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


USAGE_TYPES = {"llm_usage", "stt_usage", "tts_usage", "interruption_usage", "eot_usage"}
MEASURES = {
    "input_tokens", "output_tokens", "input_cached_tokens", "input_cache_creation_tokens",
    "input_audio_tokens", "output_audio_tokens", "characters_count", "audio_duration",
    "session_duration", "total_requests",
}
RATE_MEASURES = MEASURES - {"input_audio_tokens", "output_audio_tokens"}


class UsageLedgerError(ValueError):
    pass


COMPONENTS = {"llm_usage": "llm", "stt_usage": "stt", "tts_usage": "tts",
              "interruption_usage": "interruption", "eot_usage": "turn_detector"}


@dataclass(frozen=True)
class Rate:
    """Explicit billing assumption, e.g. USD 0.5 per 1M input tokens."""

    currency: str
    measure: str
    units: Decimal
    amount: Decimal
    source: str
    effective_at: str

    def validated(self) -> "Rate":
        if (self.currency not in {"USD", "CNY"} or self.measure not in RATE_MEASURES
                or not self.source or not self.effective_at):
            raise UsageLedgerError("invalid rate provenance")
        try:
            units, amount = Decimal(self.units), Decimal(self.amount)
        except (InvalidOperation, TypeError) as exc:
            raise UsageLedgerError("invalid rate") from exc
        if not units.is_finite() or not amount.is_finite() or units <= 0 or amount < 0:
            raise UsageLedgerError("invalid rate amount")
        return self


def _snapshot(item) -> dict:
    """Accept SDK ModelUsage or a test dictionary; retain only metering fields."""
    raw = item.model_dump() if hasattr(item, "model_dump") else item
    if not isinstance(raw, dict):
        raise UsageLedgerError("invalid SDK usage")
    kind, provider, model = raw.get("type"), raw.get("provider"), raw.get("model")
    if kind not in USAGE_TYPES or not isinstance(provider, str) or not isinstance(model, str):
        raise UsageLedgerError("invalid model identity")
    if not provider or not model or len(provider) > 100 or len(model) > 120:
        raise UsageLedgerError("missing SDK model provenance")
    data = {"type": kind, "provider": provider, "model": model}
    for key in MEASURES:
        if key not in raw:
            continue
        value = raw[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise UsageLedgerError("invalid SDK measurement")
        if key in {"audio_duration", "session_duration"}:
            if value > 86_400 or value != value:
                raise UsageLedgerError("invalid SDK duration")
            data[key] = float(value)
        elif not isinstance(value, int) or value > 10**12:
            raise UsageLedgerError("invalid SDK count")
        else:
            data[key] = value
    return data


def signed_receipt(*, verified_owner_id: str, room: str, sdk_usage,
                   secret: str, created_at: int | None = None) -> tuple[bytes, str]:
    """Canonical signed receipt for PWA `/api/yuling/usage/ingest`.

    ``verified_owner_id`` must come from validated room-bound context. This
    helper does not verify identity; never feed browser JSON into it.
    """
    if not isinstance(secret, str) or len(secret.encode("utf-8")) < 32:
        raise UsageLedgerError("dedicated usage signing key required")
    if (not verified_owner_id or not isinstance(room, str)
            or not room.startswith("ailivex-yuling-")):
        raise UsageLedgerError("verified call identity required")
    item = _snapshot(sdk_usage)
    component = COMPONENTS[item["type"]]
    event_id = hashlib.sha256(
        json.dumps([room, component, item["provider"], item["model"]],
                   separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    metadata = {"source_status": "livekit_sdk_reported", "cost_status": "unpriced"}
    for key in ("input_cached_tokens", "input_cache_creation_tokens", "input_audio_tokens",
                "output_audio_tokens", "total_requests", "session_duration"):
        if key in item:
            metadata[key] = item[key]
    payload = {
        "event_id": event_id,
        "owner_id": verified_owner_id,
        "call_id": room,
        "created_at": int(time.time()) if created_at is None else int(created_at),
        "provider": item["provider"],
        "component": component,
        "model": item["model"],
        "input_tokens": item.get("input_tokens", 0),
        "output_tokens": item.get("output_tokens", 0),
        "audio_input_seconds": item.get("audio_duration", 0) if component == "stt" else 0,
        "audio_output_chars": item.get("characters_count", 0) if component == "tts" else 0,
        "estimated_usd_micros": None,
        "metadata": metadata,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    signature = "sha256=" + hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return raw, signature


def send_signed_receipts(*, endpoint: str, verified_owner_id: str, room: str,
                         model_usage: list, secret: str, timeout: float = 4) -> int:
    """Send final cumulative SDK usage; raise on any failed central receipt.

    Invoke from a worker thread, not a LiveKit event-loop callback. Callers
    should retain local/queued receipts for retry if this fails. The PWA
    endpoint must deduplicate deterministic ``event_id`` values.
    """
    if not endpoint.startswith("https://") and not endpoint.startswith("http://127.0.0.1:"):
        raise UsageLedgerError("usage endpoint must use HTTPS")
    if not isinstance(model_usage, list) or len(model_usage) > 40:
        raise UsageLedgerError("invalid SDK usage collection")
    sent = 0
    for item in model_usage:
        raw, signature = signed_receipt(verified_owner_id=verified_owner_id,
                                        room=room, sdk_usage=item, secret=secret)
        req = Request(endpoint, data=raw, method="POST", headers={
            "Content-Type": "application/json", "X-Usage-Signature": signature})
        try:
            with urlopen(req, timeout=timeout) as response:
                if response.status not in (200, 201, 202):
                    raise UsageLedgerError("central usage receipt rejected")
        except (HTTPError, URLError, TimeoutError) as exc:
            # Never include endpoint, request body, or server error text.
            raise UsageLedgerError("central usage receipt unavailable") from exc
        sent += 1
    return sent


class CallUsageLedger:
    def __init__(self, path: str):
        self.path = Path(path).expanduser()
        if not path or path == ":memory:" or not self.path.is_absolute():
            raise UsageLedgerError("persistent absolute database path required")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS calls (
                  room TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                  started_at REAL NOT NULL, closed_at REAL
                );
                CREATE INDEX IF NOT EXISTS calls_owner_time
                  ON calls(owner_id, started_at);
                CREATE TABLE IF NOT EXISTS model_usage (
                  room TEXT NOT NULL REFERENCES calls(room),
                  usage_type TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
                  sdk_snapshot_json TEXT NOT NULL, source_status TEXT NOT NULL,
                  rate_snapshot_json TEXT, estimated_cost TEXT,
                  cost_status TEXT NOT NULL, observed_at REAL NOT NULL,
                  PRIMARY KEY (room, usage_type, provider, model)
                );
            """)

    def _db(self):
        db = sqlite3.connect(str(self.path), timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def begin_call(self, *, verified_owner_id: str, room: str) -> None:
        """Call only after validating room-bound signed account context."""
        if (not isinstance(verified_owner_id, str) or not verified_owner_id.strip()
                or len(verified_owner_id) > 100 or not isinstance(room, str)
                or not room.startswith("ailivex-yuling-") or len(room) > 130):
            raise UsageLedgerError("verified account and room required")
        with self._db() as db:
            existing = db.execute("SELECT owner_id FROM calls WHERE room=?", (room,)).fetchone()
            if existing and existing["owner_id"] != verified_owner_id:
                raise UsageLedgerError("room already belongs to another account")
            db.execute("INSERT OR IGNORE INTO calls(room,owner_id,started_at) VALUES(?,?,?)",
                       (room, verified_owner_id, time.time()))
            db.commit()

    def record_sdk_snapshot(self, *, verified_owner_id: str, room: str,
                            model_usage: list, rates: dict[tuple[str, str, str], list[Rate]] | None = None) -> None:
        """Store cumulative SDK values and optional frozen operator pricing."""
        if not isinstance(model_usage, list) or len(model_usage) > 40:
            raise UsageLedgerError("invalid SDK usage collection")
        snapshots = [_snapshot(item) for item in model_usage]
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            call = db.execute("SELECT owner_id FROM calls WHERE room=?", (room,)).fetchone()
            if not call or call["owner_id"] != verified_owner_id:
                raise UsageLedgerError("call ownership mismatch")
            for item in snapshots:
                key = (item["type"], item["provider"], item["model"])
                selected_rates = (rates or {}).get(key, [])
                currency = None
                total = Decimal(0)
                frozen = []
                for rate in selected_rates:
                    rate.validated()
                    if currency is not None and currency != rate.currency:
                        raise UsageLedgerError("mixed currencies for model")
                    currency = rate.currency
                    frozen.append({"currency": rate.currency, "measure": rate.measure,
                                   "units": str(rate.units), "amount": str(rate.amount),
                                   "source": rate.source, "effective_at": rate.effective_at})
                    total += Decimal(str(item.get(rate.measure, 0))) * rate.amount / rate.units
                db.execute("""INSERT INTO model_usage
                    (room,usage_type,provider,model,sdk_snapshot_json,source_status,
                     rate_snapshot_json,estimated_cost,cost_status,observed_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(room,usage_type,provider,model) DO UPDATE SET
                      sdk_snapshot_json=excluded.sdk_snapshot_json,
                      source_status=excluded.source_status,
                      rate_snapshot_json=excluded.rate_snapshot_json,
                      estimated_cost=excluded.estimated_cost,
                      cost_status=excluded.cost_status,observed_at=excluded.observed_at""",
                    (room, *key, json.dumps(item, sort_keys=True), "livekit_sdk_reported",
                     json.dumps(frozen, sort_keys=True) if frozen else None,
                     str(total) if frozen else None,
                     "operator_rate_estimate" if frozen else "unpriced",
                     time.time()))
            db.commit()

    def close_call(self, *, verified_owner_id: str, room: str) -> None:
        with self._db() as db:
            db.execute("UPDATE calls SET closed_at=COALESCE(closed_at,?) "
                       "WHERE room=? AND owner_id=?", (time.time(), room, verified_owner_id))
            if db.total_changes != 1:
                raise UsageLedgerError("call ownership mismatch")
            db.commit()

    def account_calls(self, owner_id: str) -> list[dict]:
        """Caller must supply the already-authenticated owner, never URL/body owner."""
        if not owner_id:
            raise UsageLedgerError("authenticated owner required")
        with self._db() as db:
            rows = db.execute("""SELECT c.room,c.started_at,c.closed_at,
                u.sdk_snapshot_json,u.source_status,u.rate_snapshot_json,
                u.estimated_cost,u.cost_status
                FROM calls c LEFT JOIN model_usage u ON u.room=c.room
                WHERE c.owner_id=? ORDER BY c.started_at DESC,u.usage_type,u.provider,u.model""",
                (owner_id,)).fetchall()
        calls = {}
        for row in rows:
            room = row["room"]
            call = calls.setdefault(room, {"room": room, "started_at": row["started_at"],
                                           "closed_at": row["closed_at"], "models": []})
            if row["sdk_snapshot_json"]:
                call["models"].append({"sdk_usage": json.loads(row["sdk_snapshot_json"]),
                                       "source_status": row["source_status"],
                                       "rate_snapshot": json.loads(row["rate_snapshot_json"])
                                       if row["rate_snapshot_json"] else None,
                                       "estimated_cost": row["estimated_cost"],
                                       "cost_status": row["cost_status"]})
        return list(calls.values())

    def export_account_jsonl(self, owner_id: str) -> str:
        """Portable, owner-scoped export for a durable sink or later reconciliation.

        The room is the idempotent call ID. This exports metering only; no
        transcript, prompt, user name, or secrets are stored in this ledger.
        The host must authenticate ``owner_id`` before serving this result.
        """
        return "".join(json.dumps(call, ensure_ascii=False, sort_keys=True) + "\n"
                       for call in self.account_calls(owner_id))
