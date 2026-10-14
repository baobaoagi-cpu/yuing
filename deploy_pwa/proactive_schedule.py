"""Opt-in, owner-scoped daily proactive-message scheduler.

This module does not start a worker or send a notification on import. The host
must call ``run_due`` from one durable scheduler and supply owner-scoped topic
and delivery adapters. A delivery attempt is never retried automatically when
its outcome is uncertain; the adapter must honor the supplied idempotency key.
"""
from __future__ import annotations

import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_ZONE = "Asia/Shanghai"


@dataclass(frozen=True)
class Topic:
    """A recent, owner-verified conversation topic, never an inferred location."""

    owner_id: str
    source_event_id: str
    subject: str


@dataclass(frozen=True)
class Delivery:
    owner_id: str
    local_day: str
    idempotency_key: str
    source_event_id: str
    in_app_text: str
    # Do not expose a potentially private topic on a lock screen.
    push_title: str = "陈端倪"
    push_text: str = "想起我们聊过的事。想继续聊时，我在。"


class ProactiveSchedule:
    """SQLite state machine with one delivery *attempt* per owner/local day."""

    def __init__(self, path: str, *, randbelow: Callable[[int], int] = secrets.randbelow):
        if not path or path == ":memory:":
            raise ValueError("A persistent absolute SQLite path is required")
        self.path = Path(path).expanduser()
        if not self.path.is_absolute():
            raise ValueError("A persistent absolute SQLite path is required")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.randbelow = randbelow
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS proactive_preferences (
                    owner_id TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
                    consent_at REAL,
                    timezone_name TEXT NOT NULL,
                    awake_start_hour INTEGER NOT NULL,
                    awake_end_hour INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS proactive_days (
                    owner_id TEXT NOT NULL,
                    local_day TEXT NOT NULL,
                    due_at REAL NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                        ('scheduled','sending','sent','skipped','cancelled','failed_unknown')),
                    source_event_id TEXT,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(owner_id, local_day)
                );
                CREATE INDEX IF NOT EXISTS proactive_due
                    ON proactive_days(status, due_at);
            """)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(str(self.path), timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _instant(now: datetime | None) -> datetime:
        value = now or datetime.now(timezone.utc)
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        return value

    def set_consent(self, owner_id: str, enabled: bool, *,
                    timezone_name: str = DEFAULT_ZONE,
                    awake_start_hour: int = 9, awake_end_hour: int = 21,
                    now: datetime | None = None) -> None:
        """Call only after authenticated, explicit user action; never infer consent."""
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise ValueError("authenticated owner_id is required")
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        try:
            zone = ZoneInfo(timezone_name)
        except (TypeError, ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("invalid timezone") from exc
        if (type(awake_start_hour) is not int or type(awake_end_hour) is not int or
                not 0 <= awake_start_hour < awake_end_hour <= 24):
            raise ValueError("invalid awake window")
        stamp = self._instant(now).timestamp()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""
                INSERT INTO proactive_preferences
                    (owner_id,enabled,consent_at,timezone_name,awake_start_hour,awake_end_hour)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(owner_id) DO UPDATE SET
                    enabled=excluded.enabled, consent_at=excluded.consent_at,
                    timezone_name=excluded.timezone_name,
                    awake_start_hour=excluded.awake_start_hour,
                    awake_end_hour=excluded.awake_end_hour
            """, (owner_id, int(enabled), stamp if enabled else None,
                  zone.key, awake_start_hour, awake_end_hour))
            if not enabled:
                db.execute("""
                    UPDATE proactive_days SET status='cancelled', updated_at=?
                    WHERE owner_id=? AND status='scheduled'
                """, (stamp, owner_id))
            db.commit()

    def _preference(self, db, owner_id: str):
        return db.execute("SELECT * FROM proactive_preferences WHERE owner_id=?",
                          (owner_id,)).fetchone()

    def settings(self, owner_id: str) -> dict | None:
        """Safe user-facing preference readback, scoped to authenticated owner."""
        with self._db() as db:
            row = self._preference(db, owner_id)
            return ({"enabled": bool(row["enabled"]),
                     "timezone_name": row["timezone_name"],
                     "awake_start_hour": row["awake_start_hour"],
                     "awake_end_hour": row["awake_end_hour"]} if row else None)

    def schedule_day(self, owner_id: str, *, now: datetime | None = None):
        """Create one cryptographically randomized slot inside the consented window."""
        instant = self._instant(now)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            pref = self._preference(db, owner_id)
            if not pref or not pref["enabled"] or pref["consent_at"] is None:
                db.commit()
                return None
            local = instant.astimezone(ZoneInfo(pref["timezone_name"]))
            day = local.date().isoformat()
            existing = db.execute("SELECT * FROM proactive_days WHERE owner_id=? AND local_day=?",
                                  (owner_id, day)).fetchone()
            if existing:
                db.commit()
                return dict(existing)
            start = datetime.combine(local.date(), time(pref["awake_start_hour"]),
                                     tzinfo=local.tzinfo)
            end = start + timedelta(hours=pref["awake_end_hour"] - pref["awake_start_hour"])
            # Do not backdate a fresh slot or send twice inside a rolling 24 h.
            earliest = max(start, local)
            prior = db.execute("""
                SELECT MAX(updated_at) AS attempted_at FROM proactive_days
                WHERE owner_id=? AND status IN ('sending','sent','failed_unknown')
            """, (owner_id,)).fetchone()["attempted_at"]
            if prior is not None:
                earliest = max(earliest,
                               datetime.fromtimestamp(prior + 24 * 3600,
                                                      ZoneInfo(pref["timezone_name"])))
            seconds = int((end - earliest).total_seconds())
            if seconds <= 0:
                db.commit()
                return None
            offset = self.randbelow(seconds)
            if type(offset) is not int or not 0 <= offset < seconds:
                raise ValueError("randbelow returned invalid offset")
            due_at = (earliest + timedelta(seconds=offset)).timestamp()
            db.execute("""
                INSERT INTO proactive_days(owner_id,local_day,due_at,status,updated_at)
                VALUES(?, ?, ?, 'scheduled', ?)
            """, (owner_id, day, due_at, instant.timestamp()))
            db.commit()
            return {"owner_id": owner_id, "local_day": day, "due_at": due_at,
                    "status": "scheduled"}

    def run_due(self, owner_id: str, topic_provider: Callable[[str], Topic | None],
                deliver: Callable[[Delivery], None], *, now: datetime | None = None) -> str:
        """Return status; never invoke an adapter without a due, consented slot.

        ``topic_provider`` MUST query recent owner-owned events and approve a
        non-sensitive, source-backed subject. It must not infer whereabouts.
        ``deliver`` MUST atomically/idempotently persist the in-app message and
        send a user-visible push only for an opted-in PushSubscription. It must
        use ``idempotency_key`` for its own duplicate suppression.
        """
        instant = self._instant(now)
        slot = self.schedule_day(owner_id, now=instant)
        if not slot:
            return "disabled_or_outside_window"
        if slot["status"] != "scheduled":
            return slot["status"]
        if instant.timestamp() < slot["due_at"]:
            return "scheduled"
        with self._db() as db:
            pref = self._preference(db, owner_id)
            local = instant.astimezone(ZoneInfo(pref["timezone_name"])) if pref else None
            if (not local or local.date().isoformat() != slot["local_day"] or
                    not pref["awake_start_hour"] <= local.hour < pref["awake_end_hour"]):
                db.execute("UPDATE proactive_days SET status='skipped', updated_at=? "
                           "WHERE owner_id=? AND local_day=? AND status='scheduled'",
                           (instant.timestamp(), owner_id, slot["local_day"]))
                db.commit()
                return "skipped"
        # The host adapter owns the stronger provenance check against ChatStore.
        topic = topic_provider(owner_id)
        valid = (isinstance(topic, Topic) and topic.owner_id == owner_id and
                 isinstance(topic.source_event_id, str) and bool(topic.source_event_id.strip()) and
                 isinstance(topic.subject, str) and 1 <= len(topic.subject.strip()) <= 80 and
                 "\n" not in topic.subject and "\r" not in topic.subject)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            pref = self._preference(db, owner_id)
            row = db.execute("SELECT * FROM proactive_days WHERE owner_id=? AND local_day=?",
                             (owner_id, slot["local_day"])).fetchone()
            if not pref or not pref["enabled"] or not row or row["status"] != "scheduled":
                db.commit()
                return "cancelled_or_claimed"
            if not valid:
                db.execute("UPDATE proactive_days SET status='skipped', updated_at=? "
                           "WHERE owner_id=? AND local_day=?",
                           (instant.timestamp(), owner_id, slot["local_day"]))
                db.commit()
                return "skipped"
            db.execute("""
                UPDATE proactive_days SET status='sending', source_event_id=?, updated_at=?
                WHERE owner_id=? AND local_day=?
            """, (topic.source_event_id, instant.timestamp(), owner_id, slot["local_day"]))
            db.commit()
        # A lock-screen push intentionally omits the topic. The in-app text can
        # refer to the verified subject, but never claims live location/activity.
        delivery = Delivery(
            owner_id=owner_id, local_day=slot["local_day"],
            idempotency_key=f"proactive:{owner_id}:{slot['local_day']}",
            source_event_id=topic.source_event_id,
            in_app_text=f"上次我们聊到「{topic.subject.strip()}」。你后来有什么新想法吗？",
        )
        try:
            # Recheck opt-out as close to the external side effect as possible.
            with self._db() as db:
                pref = self._preference(db, owner_id)
                if not pref or not pref["enabled"]:
                    db.execute("UPDATE proactive_days SET status='cancelled', updated_at=? "
                               "WHERE owner_id=? AND local_day=? AND status='sending'",
                               (instant.timestamp(), owner_id, slot["local_day"]))
                    db.commit()
                    return "cancelled"
            deliver(delivery)
        except Exception:
            # Network outcomes can be uncertain: never resend automatically.
            with self._db() as db:
                db.execute("UPDATE proactive_days SET status='failed_unknown', updated_at=? "
                           "WHERE owner_id=? AND local_day=? AND status='sending'",
                           (instant.timestamp(), owner_id, slot["local_day"]))
                db.commit()
            return "failed_unknown"
        with self._db() as db:
            db.execute("UPDATE proactive_days SET status='sent', updated_at=? "
                       "WHERE owner_id=? AND local_day=? AND status='sending'",
                       (instant.timestamp(), owner_id, slot["local_day"]))
            db.commit()
        return "sent"
