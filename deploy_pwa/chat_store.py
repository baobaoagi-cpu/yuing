"""Single-account POC conversation store. Requires a persistent volume path."""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


class StoreUnavailable(RuntimeError):
    pass


class ChatStore:
    def __init__(self, path: str):
        if not path or path == ":memory:":
            raise StoreUnavailable("请配置持久化对话存储")
        self.path = Path(path).expanduser()
        if not self.path.is_absolute():
            raise StoreUnavailable("对话存储必须使用绝对路径")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                    role TEXT NOT NULL CHECK(role IN ('user','assistant')),
                    text TEXT NOT NULL, image_id TEXT, link_url TEXT,
                    source_kind TEXT NOT NULL, created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_session_time ON events(session_id, created_at);
                CREATE TABLE IF NOT EXISTS call_transcripts (
                    call_id TEXT NOT NULL, speaker_identity TEXT NOT NULL,
                    segment_id TEXT NOT NULL, event_id TEXT NOT NULL REFERENCES events(id),
                    PRIMARY KEY (call_id, speaker_identity, segment_id)
                );
                CREATE TABLE IF NOT EXISTS images (
                    id TEXT PRIMARY KEY, mime_type TEXT NOT NULL, data BLOB NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY, text TEXT NOT NULL,
                    source_kind TEXT NOT NULL CHECK(source_kind IN ('user_explicit','inference_candidate')),
                    source_event_id TEXT, created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS knowledge_items (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL,
                    source_url TEXT, source_kind TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS relation_candidates (
                    id TEXT PRIMARY KEY, subject_id TEXT NOT NULL, predicate TEXT NOT NULL,
                    object_id TEXT NOT NULL, source_event_id TEXT,
                    status TEXT NOT NULL DEFAULT 'candidate', created_at REAL NOT NULL
                );
            """)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(str(self.path), timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=5000")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def create_session(self):
        sid = str(uuid4())
        now = time.time()
        with self._connect() as db:
            db.execute("INSERT INTO sessions VALUES(?,?,?)", (sid, now, now))
        return sid

    def has_session(self, sid):
        with self._connect() as db:
            return db.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone() is not None

    def add_event(self, sid, role, text, image_id=None, link_url=None, source_kind="conversation"):
        eid = str(uuid4())
        now = time.time()
        with self._connect() as db:
            db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?)",
                       (eid, sid, role, text, image_id, link_url, source_kind, now))
            db.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now, sid))
        return {"id": eid, "session_id": sid, "role": role, "text": text,
                "image_id": image_id, "url": link_url, "source_kind": source_kind,
                "created_at": now}

    def add_call_transcript(self, sid, call_id, speaker_identity, segment_id, role, text):
        """Store one final LiveKit text segment exactly once, with its browser provenance."""
        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT e.* FROM call_transcripts c JOIN events e ON e.id=c.event_id "
                "WHERE c.call_id=? AND c.speaker_identity=? AND c.segment_id=?",
                (call_id, speaker_identity, segment_id),
            ).fetchone()
            if existing:
                return {"id": existing["id"], "session_id": existing["session_id"],
                        "role": existing["role"], "text": existing["text"],
                        "source_kind": existing["source_kind"],
                        "created_at": existing["created_at"]}, False
            if sid is None:
                sid = str(uuid4())
                db.execute("INSERT INTO sessions VALUES(?,?,?)", (sid, now, now))
            elif not db.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone():
                raise StoreUnavailable("会话不存在")
            eid = str(uuid4())
            db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?)",
                       (eid, sid, role, text, None, None,
                        "livekit_browser_transcript", now))
            db.execute("INSERT INTO call_transcripts VALUES(?,?,?,?)",
                       (call_id, speaker_identity, segment_id, eid))
            db.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now, sid))
        return {"id": eid, "session_id": sid, "role": role, "text": text,
                "source_kind": "livekit_browser_transcript", "created_at": now}, True

    def history(self, sid, limit=100):
        with self._connect() as db:
            rows = db.execute("SELECT * FROM (SELECT rowid AS seq,* FROM events WHERE session_id=? "
                              "ORDER BY created_at DESC, rowid DESC LIMIT ?) "
                              "ORDER BY created_at, seq", (sid, limit)).fetchall()
        return [{"id": x["id"], "session_id": x["session_id"], "role": x["role"],
                 "text": x["text"], "image_id": x["image_id"], "url": x["link_url"],
                 "source_kind": x["source_kind"], "created_at": x["created_at"]}
                for x in rows]

    def recent_sessions(self, limit=20):
        with self._connect() as db:
            rows = db.execute("SELECT id,created_at,updated_at FROM sessions "
                              "ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(x) for x in rows]

    def put_image(self, data, mime_type):
        iid = str(uuid4())
        with self._connect() as db:
            db.execute("INSERT INTO images VALUES(?,?,?,?)", (iid, mime_type, data, time.time()))
        return iid

    def get_image(self, iid):
        with self._connect() as db:
            row = db.execute("SELECT mime_type,data FROM images WHERE id=?", (iid,)).fetchone()
        return (row["mime_type"], row["data"]) if row else None

    def add_memory(self, text, source_event_id=None):
        mid = str(uuid4())
        with self._connect() as db:
            db.execute("INSERT INTO memories VALUES(?,?,?,?,?)",
                       (mid, text, "user_explicit", source_event_id, time.time()))
        return mid

    def memories(self, limit=30):
        with self._connect() as db:
            rows = db.execute("SELECT id,text,source_kind,source_event_id,created_at FROM memories "
                              "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(x) for x in rows]

    def delete_memory(self, mid):
        with self._connect() as db:
            cur = db.execute("DELETE FROM memories WHERE id=?", (mid,))
            return cur.rowcount > 0
