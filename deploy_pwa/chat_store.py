"""Single-account POC conversation store. Requires a persistent volume path."""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class StoreUnavailable(RuntimeError):
    pass


class PageReadQuotaExceeded(RuntimeError):
    pass


OWNER_TABLES = ("sessions", "images", "memories", "knowledge_items", "relation_candidates")


class ChatStore:
    def __init__(self, path: str, owner_id: str, legacy_owner_id: str | None = None):
        if not path or path == ":memory:":
            raise StoreUnavailable("请配置持久化对话存储")
        if not owner_id or not legacy_owner_id and legacy_owner_id is not None:
            raise StoreUnavailable("用户归属未配置")
        self.owner_id = owner_id
        self.legacy_owner_id = legacy_owner_id or owner_id
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
                    source_url TEXT, source_kind TEXT NOT NULL, source_event_id TEXT,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS relation_candidates (
                    id TEXT PRIMARY KEY, subject_id TEXT NOT NULL, predicate TEXT NOT NULL,
                    object_id TEXT NOT NULL, source_event_id TEXT,
                    status TEXT NOT NULL DEFAULT 'candidate', created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS page_reads (
                    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                    source_event_id TEXT NOT NULL, url TEXT NOT NULL,
                    requested_at REAL NOT NULL, utc_day TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending','read','failed')),
                    fetched_at TEXT, sha256 TEXT, content_type TEXT, text TEXT
                );
                CREATE INDEX IF NOT EXISTS page_reads_owner_day
                    ON page_reads(owner_id, utc_day);
                CREATE INDEX IF NOT EXISTS page_reads_owner_event
                    ON page_reads(owner_id, source_event_id, requested_at);
            """)
        # Existing single-account databases are assigned to the configured
        # legacy owner once. New records always carry their owner explicitly.
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for table in OWNER_TABLES:
                columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
                if "owner_id" not in columns:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN owner_id TEXT NOT NULL DEFAULT ''")
                db.execute(f"UPDATE {table} SET owner_id=? WHERE owner_id=''",
                           (self.legacy_owner_id,))
            db.execute("CREATE INDEX IF NOT EXISTS sessions_owner_time "
                       "ON sessions(owner_id, updated_at)")
            db.execute("CREATE INDEX IF NOT EXISTS images_owner ON images(owner_id, id)")
            db.execute("CREATE INDEX IF NOT EXISTS memories_owner_time "
                       "ON memories(owner_id, created_at)")
            knowledge_columns = {row[1] for row in db.execute("PRAGMA table_info(knowledge_items)")}
            if "source_event_id" not in knowledge_columns:
                db.execute("ALTER TABLE knowledge_items ADD COLUMN source_event_id TEXT")
            db.execute("CREATE INDEX IF NOT EXISTS knowledge_owner_time "
                       "ON knowledge_items(owner_id, created_at)")
            db.execute("CREATE INDEX IF NOT EXISTS relation_owner_time "
                       "ON relation_candidates(owner_id, created_at)")

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
            db.execute("INSERT INTO sessions(id,created_at,updated_at,owner_id) "
                       "VALUES(?,?,?,?)", (sid, now, now, self.owner_id))
        return sid

    def has_session(self, sid):
        with self._connect() as db:
            return db.execute("SELECT 1 FROM sessions WHERE id=? AND owner_id=?",
                              (sid, self.owner_id)).fetchone() is not None

    def add_event(self, sid, role, text, image_id=None, link_url=None, source_kind="conversation"):
        eid = str(uuid4())
        now = time.time()
        with self._connect() as db:
            if not db.execute("SELECT 1 FROM sessions WHERE id=? AND owner_id=?",
                              (sid, self.owner_id)).fetchone():
                raise StoreUnavailable("会话不存在")
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
                "SELECT e.*, s.owner_id FROM call_transcripts c "
                "JOIN events e ON e.id=c.event_id "
                "JOIN sessions s ON s.id=e.session_id "
                "WHERE c.call_id=? AND c.speaker_identity=? AND c.segment_id=?",
                (call_id, speaker_identity, segment_id),
            ).fetchone()
            if existing:
                if existing["owner_id"] != self.owner_id:
                    raise StoreUnavailable("通话编号已使用")
                return {"id": existing["id"], "session_id": existing["session_id"],
                        "role": existing["role"], "text": existing["text"],
                        "source_kind": existing["source_kind"],
                        "created_at": existing["created_at"]}, False
            if sid is None:
                sid = str(uuid4())
                db.execute("INSERT INTO sessions(id,created_at,updated_at,owner_id) "
                           "VALUES(?,?,?,?)", (sid, now, now, self.owner_id))
            elif not db.execute("SELECT 1 FROM sessions WHERE id=? AND owner_id=?",
                                (sid, self.owner_id)).fetchone():
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
            rows = db.execute("SELECT * FROM (SELECT e.rowid AS seq,e.* FROM events e "
                              "JOIN sessions s ON s.id=e.session_id "
                              "WHERE e.session_id=? AND s.owner_id=? "
                              "ORDER BY e.created_at DESC, e.rowid DESC LIMIT ?) "
                              "ORDER BY created_at, seq", (sid, self.owner_id, limit)).fetchall()
            event_ids = [row["id"] for row in rows]
            reads = {}
            if event_ids:
                placeholders = ",".join("?" for _ in event_ids)
                found = db.execute(
                    "SELECT id,source_event_id,url,status,fetched_at,sha256,content_type "
                    "FROM page_reads WHERE owner_id=? AND source_event_id IN (" +
                    placeholders + ") ORDER BY requested_at DESC,rowid DESC",
                    (self.owner_id, *event_ids),
                ).fetchall()
                for item in found:
                    reads.setdefault(item["source_event_id"], {
                        "id": item["id"], "status": item["status"], "url": item["url"],
                        "fetched_at": item["fetched_at"], "sha256": item["sha256"],
                        "content_type": item["content_type"],
                    })
        return [{"id": x["id"], "session_id": x["session_id"], "role": x["role"],
                 "text": x["text"], "image_id": x["image_id"], "url": x["link_url"],
                 "source_kind": x["source_kind"], "created_at": x["created_at"],
                 "page_read": reads.get(x["id"])}
                for x in rows]

    def page_read_source(self, source_event_id):
        """Only a user-posted link card from this account can initiate a read."""
        with self._connect() as db:
            row = db.execute(
                "SELECT e.link_url FROM events e JOIN sessions s ON s.id=e.session_id "
                "WHERE e.id=? AND s.owner_id=? AND e.role='user' "
                "AND e.source_kind='user_explicit'",
                (source_event_id, self.owner_id),
            ).fetchone()
        return row["link_url"] if row and row["link_url"] else None

    def reserve_page_read(self, source_event_id, canonical_url, daily_limit=6):
        """Atomically consume one UTC-day attempt before making a network request."""
        now = time.time()
        utc_day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
        page_id = str(uuid4())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            source = db.execute(
                "SELECT e.link_url FROM events e JOIN sessions s ON s.id=e.session_id "
                "WHERE e.id=? AND s.owner_id=? AND e.role='user' "
                "AND e.source_kind='user_explicit'",
                (source_event_id, self.owner_id),
            ).fetchone()
            if source is None or not source["link_url"]:
                raise StoreUnavailable("来源消息不存在")
            count = db.execute(
                "SELECT count(*) FROM page_reads WHERE owner_id=? AND utc_day=?",
                (self.owner_id, utc_day),
            ).fetchone()[0]
            if count >= daily_limit:
                raise PageReadQuotaExceeded("今日网页读取次数已用完")
            db.execute(
                "INSERT INTO page_reads(id,owner_id,source_event_id,url,requested_at,utc_day,status) "
                "VALUES(?,?,?,?,?,?,?)",
                (page_id, self.owner_id, source_event_id, canonical_url,
                 now, utc_day, "pending"),
            )
        return page_id, daily_limit - count - 1

    def finish_page_read(self, page_id, result=None):
        """Keep fetched content separate from chat history and model input."""
        fields = ("read", result["url"], result["fetched_at"], result["sha256"],
                  result["content_type"], result["text"][:8000]) if result else (
                      "failed", None, None, None, None, None)
        with self._connect() as db:
            cur = db.execute(
                "UPDATE page_reads SET status=?, url=COALESCE(?,url), fetched_at=?, "
                "sha256=?, content_type=?, text=? WHERE id=? AND owner_id=? "
                "AND status='pending'",
                (*fields, page_id, self.owner_id),
            )
        return cur.rowcount == 1

    def page_read(self, page_id):
        with self._connect() as db:
            row = db.execute(
                "SELECT id,source_event_id,url,requested_at,status,fetched_at,sha256,"
                "content_type,text FROM page_reads WHERE id=? AND owner_id=?",
                (page_id, self.owner_id),
            ).fetchone()
        return dict(row) if row else None

    def recent_sessions(self, limit=20):
        with self._connect() as db:
            rows = db.execute("SELECT id,created_at,updated_at FROM sessions "
                              "WHERE owner_id=? ORDER BY updated_at DESC LIMIT ?",
                              (self.owner_id, limit)).fetchall()
        return [dict(x) for x in rows]

    def put_image(self, data, mime_type):
        iid = str(uuid4())
        with self._connect() as db:
            db.execute("INSERT INTO images(id,mime_type,data,created_at,owner_id) "
                       "VALUES(?,?,?,?,?)", (iid, mime_type, data, time.time(), self.owner_id))
        return iid

    def get_image(self, iid):
        with self._connect() as db:
            row = db.execute("SELECT mime_type,data FROM images WHERE id=? AND owner_id=?",
                             (iid, self.owner_id)).fetchone()
        return (row["mime_type"], row["data"]) if row else None

    def add_memory(self, text, source_event_id=None):
        mid = str(uuid4())
        with self._connect() as db:
            db.execute("INSERT INTO memories(id,text,source_kind,source_event_id,created_at,owner_id) "
                       "VALUES(?,?,?,?,?,?)",
                       (mid, text, "user_explicit", source_event_id, time.time(), self.owner_id))
        return mid

    def memories(self, limit=30):
        with self._connect() as db:
            rows = db.execute("SELECT id,text,source_kind,source_event_id,created_at FROM memories "
                              "WHERE owner_id=? ORDER BY created_at DESC LIMIT ?",
                              (self.owner_id, limit)).fetchall()
        return [dict(x) for x in rows]

    def delete_memory(self, mid):
        with self._connect() as db:
            cur = db.execute("DELETE FROM memories WHERE id=? AND owner_id=?",
                             (mid, self.owner_id))
            return cur.rowcount > 0

    def add_knowledge_item(self, title, content, source_event_id,
                           source_kind="user_explicit", source_url=None):
        """Save a source-linked item; this is no claim that a URL was fetched."""
        if (not isinstance(title, str) or not 1 <= len(title.strip()) <= 200 or
                not isinstance(content, str) or not 1 <= len(content.strip()) <= 8000 or
                not isinstance(source_event_id, str) or not source_event_id or
                source_kind not in {"user_explicit", "document_extract", "webpage_candidate"} or
                source_url is not None and
                (not isinstance(source_url, str) or len(source_url) > 2048)):
            raise ValueError("知识条目或来源不正确")
        kid = str(uuid4())
        now = time.time()
        with self._connect() as db:
            source = db.execute(
                "SELECT e.role,e.source_kind FROM events e "
                "JOIN sessions s ON s.id=e.session_id "
                "WHERE e.id=? AND s.owner_id=?",
                (source_event_id, self.owner_id),
            ).fetchone()
            if source is None or source["role"] != "user":
                raise StoreUnavailable("来源事件不存在")
            if source_kind == "user_explicit" and source["source_kind"] != "user_explicit":
                raise StoreUnavailable("来源事件不是用户明示输入")
            db.execute(
                "INSERT INTO knowledge_items"
                "(id,title,content,source_url,source_kind,source_event_id,created_at,owner_id) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (kid, title.strip(), content.strip(), source_url, source_kind,
                 source_event_id, now, self.owner_id),
            )
        return {"id": kid, "title": title.strip(), "content": content.strip(),
                "source_event_id": source_event_id, "source_kind": source_kind,
                "source_url": source_url, "created_at": now}

    def knowledge_items(self, limit=50):
        limit = max(1, min(int(limit), 100))
        with self._connect() as db:
            rows = db.execute(
                "SELECT id,title,content,source_url,source_kind,source_event_id,created_at "
                "FROM knowledge_items WHERE owner_id=? "
                "ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (self.owner_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_knowledge_item(self, item_id):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM relation_candidates WHERE owner_id=? "
                       "AND (subject_id=? OR object_id=?)",
                       (self.owner_id, item_id, item_id))
            cur = db.execute("DELETE FROM knowledge_items WHERE id=? AND owner_id=?",
                             (item_id, self.owner_id))
            return cur.rowcount > 0

    def add_relation_candidate(self, subject_id, predicate, object_id, source_event_id):
        """Record a possible relation, never a verified fact or causal finding."""
        if (not all(isinstance(value, str) and value for value in
                    (subject_id, object_id, source_event_id)) or
                not isinstance(predicate, str) or
                not 1 <= len(predicate.strip()) <= 120):
            raise ValueError("关系候选或来源不正确")
        rid = str(uuid4())
        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            items = db.execute(
                "SELECT id FROM knowledge_items WHERE owner_id=? AND id IN (?,?)",
                (self.owner_id, subject_id, object_id),
            ).fetchall()
            if {row["id"] for row in items} != {subject_id, object_id}:
                raise StoreUnavailable("关系条目不存在")
            source = db.execute(
                "SELECT e.role FROM events e JOIN sessions s ON s.id=e.session_id "
                "WHERE e.id=? AND s.owner_id=?",
                (source_event_id, self.owner_id),
            ).fetchone()
            if source is None or source["role"] != "user":
                raise StoreUnavailable("来源事件不存在")
            db.execute(
                "INSERT INTO relation_candidates"
                "(id,subject_id,predicate,object_id,source_event_id,status,created_at,owner_id) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (rid, subject_id, predicate.strip(), object_id, source_event_id,
                 "candidate", now, self.owner_id),
            )
        return {"id": rid, "subject_id": subject_id, "predicate": predicate.strip(),
                "object_id": object_id, "source_event_id": source_event_id,
                "status": "candidate", "created_at": now}

    def relation_candidates(self, limit=50):
        limit = max(1, min(int(limit), 100))
        with self._connect() as db:
            rows = db.execute(
                "SELECT r.id,r.subject_id,r.predicate,r.object_id,r.source_event_id,"
                "r.status,r.created_at FROM relation_candidates r "
                "JOIN knowledge_items subject ON subject.id=r.subject_id "
                "AND subject.owner_id=r.owner_id "
                "JOIN knowledge_items object ON object.id=r.object_id "
                "AND object.owner_id=r.owner_id "
                "JOIN events e ON e.id=r.source_event_id "
                "JOIN sessions s ON s.id=e.session_id AND s.owner_id=r.owner_id "
                "WHERE r.owner_id=? AND r.status='candidate' "
                "ORDER BY r.created_at DESC,r.rowid DESC LIMIT ?",
                (self.owner_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_relation_candidate(self, relation_id):
        with self._connect() as db:
            cur = db.execute("DELETE FROM relation_candidates WHERE id=? AND owner_id=?",
                             (relation_id, self.owner_id))
            return cur.rowcount > 0
