"""Persistent, fail-closed trial cap for the voice WebSearch MCP."""

import sqlite3
from contextlib import closing


TRIAL_SEARCH_CAP = 20


def reserve_trial_search(path, item):
    """Atomically spend one lifetime trial slot; retries keep their first result."""
    with closing(sqlite3.connect(path, timeout=5)) as db:
        with db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "CREATE TABLE IF NOT EXISTS web_search_reservations ("
                "request_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, "
                "call_id TEXT NOT NULL, created_at INTEGER NOT NULL)"
            )
            if db.execute(
                "SELECT 1 FROM web_search_reservations WHERE request_id=?",
                (item["request_id"],),
            ).fetchone():
                return True
            count = db.execute("SELECT COUNT(*) FROM web_search_reservations").fetchone()[0]
            if count >= TRIAL_SEARCH_CAP:
                return False
            db.execute(
                "INSERT INTO web_search_reservations VALUES (?,?,?,?)",
                (item["request_id"], item["owner_id"], item["call_id"], item["created_at"]),
            )
            return True
