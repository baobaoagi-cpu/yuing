"""Single-account POC conversation store. Requires a persistent volume path."""
from __future__ import annotations

import sqlite3
import json
from hashlib import sha256
import time
import re
from urllib.parse import urlsplit
from deploy_pwa.public_page_reader import PageReadError, _validated_url
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class StoreUnavailable(RuntimeError):
    pass


class PageReadQuotaExceeded(RuntimeError):
    pass


OWNER_TABLES = ("sessions", "images", "documents", "memories", "knowledge_items", "relation_candidates")


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
                    source_kind TEXT NOT NULL, created_at REAL NOT NULL,
                    page_read_id TEXT
                );
                CREATE INDEX IF NOT EXISTS events_session_time ON events(session_id, created_at);
                CREATE TABLE IF NOT EXISTS call_transcripts (
                    call_id TEXT NOT NULL, speaker_identity TEXT NOT NULL,
                    segment_id TEXT NOT NULL, event_id TEXT NOT NULL REFERENCES events(id),
                    PRIMARY KEY (call_id, speaker_identity, segment_id)
                );
                CREATE TABLE IF NOT EXISTS call_reflections (
                    owner_id TEXT NOT NULL, call_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('processing','generated','skipped','failed')),
                    text TEXT, source_event_ids TEXT NOT NULL,
                    model TEXT, prompt_version TEXT NOT NULL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    PRIMARY KEY (owner_id, call_id)
                );
                CREATE INDEX IF NOT EXISTS call_reflections_owner_time
                    ON call_reflections(owner_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS images (
                    id TEXT PRIMARY KEY, mime_type TEXT NOT NULL, data BLOB NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, filename TEXT NOT NULL, kind TEXT NOT NULL,
                    mime_type TEXT NOT NULL, data BLOB NOT NULL, sha256 TEXT NOT NULL,
                    extracted_text TEXT NOT NULL, extract_status TEXT NOT NULL,
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
                CREATE TABLE IF NOT EXISTS voice_usage_events (
                    event_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                    call_id TEXT NOT NULL, component TEXT NOT NULL,
                    provider TEXT NOT NULL, model TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    audio_input_seconds REAL NOT NULL DEFAULT 0,
                    audio_output_chars INTEGER NOT NULL DEFAULT 0,
                    estimated_usd_micros INTEGER,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL, received_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS voice_usage_owner_time
                    ON voice_usage_events(owner_id, created_at DESC);
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
            db.execute("CREATE INDEX IF NOT EXISTS documents_owner ON documents(owner_id, id)")
            db.execute("CREATE INDEX IF NOT EXISTS memories_owner_time "
                       "ON memories(owner_id, created_at)")
            knowledge_columns = {row[1] for row in db.execute("PRAGMA table_info(knowledge_items)")}
            if "source_event_id" not in knowledge_columns:
                db.execute("ALTER TABLE knowledge_items ADD COLUMN source_event_id TEXT")
            db.execute("CREATE INDEX IF NOT EXISTS knowledge_owner_time "
                       "ON knowledge_items(owner_id, created_at)")
            db.execute("CREATE INDEX IF NOT EXISTS relation_owner_time "
                       "ON relation_candidates(owner_id, created_at)")
            event_columns = {row[1] for row in db.execute("PRAGMA table_info(events)")}
            if "page_read_id" not in event_columns:
                db.execute("ALTER TABLE events ADD COLUMN page_read_id TEXT")
            if "document_id" not in event_columns:
                db.execute("ALTER TABLE events ADD COLUMN document_id TEXT")
            db.execute("CREATE INDEX IF NOT EXISTS events_page_read ON events(page_read_id)")

    def record_voice_usage(self, event):
        """Idempotently store one signed, cumulative SDK usage snapshot.

        A later snapshot for the same event replaces the previous counters. It
        is never added to them, and an event ID can never cross owners/calls.
        """
        now = time.time()
        identity = (self.owner_id, event["call_id"], event["component"],
                    event["provider"], event["model"])
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT owner_id,call_id,component,provider,model,created_at "
                "FROM voice_usage_events WHERE event_id=?", (event["event_id"],)
            ).fetchone()
            if previous:
                if tuple(previous[key] for key in (
                        "owner_id", "call_id", "component", "provider", "model")) != identity:
                    raise StoreUnavailable("用量事件编号冲突")
                if event["created_at"] <= previous["created_at"]:
                    return False
                db.execute(
                    "UPDATE voice_usage_events SET input_tokens=?,output_tokens=?,"
                    "audio_input_seconds=?,audio_output_chars=?,estimated_usd_micros=?,"
                    "metadata=?,created_at=?,received_at=? WHERE event_id=?",
                    (event["input_tokens"], event["output_tokens"],
                     event["audio_input_seconds"], event["audio_output_chars"],
                     event["estimated_usd_micros"], json.dumps(event["metadata"],
                     ensure_ascii=False, sort_keys=True), event["created_at"], now,
                     event["event_id"]),
                )
                return True
            db.execute(
                "INSERT INTO voice_usage_events "
                "(event_id,owner_id,call_id,component,provider,model,input_tokens,"
                "output_tokens,audio_input_seconds,audio_output_chars,"
                "estimated_usd_micros,metadata,created_at,received_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (event["event_id"], self.owner_id, event["call_id"],
                 event["component"], event["provider"], event["model"],
                 event["input_tokens"], event["output_tokens"],
                 event["audio_input_seconds"], event["audio_output_chars"],
                 event["estimated_usd_micros"], json.dumps(event["metadata"],
                 ensure_ascii=False, sort_keys=True), event["created_at"], now),
            )
            return True

    def voice_usage_summary(self, limit=30):
        """Only this signed-in owner's model usage, not a provider invoice."""
        with self._connect() as db:
            totals = db.execute(
                "SELECT component,provider,model,COUNT(*) AS calls,"
                "SUM(input_tokens) AS input_tokens,SUM(output_tokens) AS output_tokens,"
                "SUM(audio_input_seconds) AS audio_input_seconds,"
                "SUM(audio_output_chars) AS audio_output_chars,"
                "SUM(estimated_usd_micros) AS estimated_usd_micros "
                "FROM voice_usage_events WHERE owner_id=? "
                "GROUP BY component,provider,model ORDER BY component,provider,model",
                (self.owner_id,),
            ).fetchall()
            recent = db.execute(
                "SELECT event_id,call_id,component,provider,model,input_tokens,"
                "output_tokens,audio_input_seconds,audio_output_chars,"
                "estimated_usd_micros,created_at FROM voice_usage_events "
                "WHERE owner_id=? ORDER BY created_at DESC LIMIT ?",
                (self.owner_id, max(1, min(int(limit), 100))),
            ).fetchall()
        return {"totals": [dict(row) for row in totals],
                "recent": [dict(row) for row in recent],
                "source": "livekit_sdk_reported", "currency_estimate_verified": False}

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

    def add_event(self, sid, role, text, image_id=None, link_url=None,
                  source_kind="conversation", page_read_id=None, document_id=None):
        eid = str(uuid4())
        now = time.time()
        with self._connect() as db:
            if not db.execute("SELECT 1 FROM sessions WHERE id=? AND owner_id=?",
                              (sid, self.owner_id)).fetchone():
                raise StoreUnavailable("会话不存在")
            if page_read_id and not self._usable_page_context(db, page_read_id, now, sid):
                raise StoreUnavailable("网页读取记录不存在或已过期")
            if document_id and not db.execute(
                    "SELECT 1 FROM documents WHERE id=? AND owner_id=?",
                    (document_id, self.owner_id)).fetchone():
                raise StoreUnavailable("文件不存在")
            db.execute("INSERT INTO events(id,session_id,role,text,image_id,link_url,"
                       "source_kind,created_at,page_read_id,document_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (eid, sid, role, text, image_id, link_url, source_kind, now,
                        page_read_id, document_id))
            db.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now, sid))
            citation = self._citation(self._usable_page_context(db, page_read_id, now, sid)) \
                if page_read_id else None
        return {"id": eid, "session_id": sid, "role": role, "text": text,
                "image_id": image_id, "document_id": document_id,
                "url": link_url, "source_kind": source_kind,
                "created_at": now, "source_citation": citation}

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
            db.execute("INSERT INTO events(id,session_id,role,text,image_id,link_url,"
                       "source_kind,created_at) VALUES(?,?,?,?,?,?,?,?)",
                       (eid, sid, role, text, None, None,
                        "livekit_browser_transcript", now))
            db.execute("INSERT INTO call_transcripts VALUES(?,?,?,?)",
                       (call_id, speaker_identity, segment_id, eid))
            db.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now, sid))
        return {"id": eid, "session_id": sid, "role": role, "text": text,
                "source_kind": "livekit_browser_transcript", "created_at": now}, True

    def recent_calls(self, limit=30):
        """Only calls with saved final browser transcripts owned by this account."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT c.call_id, MIN(e.created_at) AS started_at, "
                "MAX(e.created_at) AS last_at, COUNT(*) AS turns "
                "FROM call_transcripts c JOIN events e ON e.id=c.event_id "
                "JOIN sessions s ON s.id=e.session_id "
                "WHERE s.owner_id=? AND e.source_kind='livekit_browser_transcript' "
                "GROUP BY c.call_id ORDER BY last_at DESC LIMIT ?",
                (self.owner_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def recent_voice_context(self, limit=8):
        """Bounded prior turns from this owner only; conversation is not a saved fact."""
        limit = max(0, min(int(limit), 8))
        if not limit:
            return []
        with self._connect() as db:
            rows = db.execute(
                "SELECT e.role,e.text,e.source_kind,e.created_at FROM events e "
                "JOIN sessions s ON s.id=e.session_id "
                "WHERE s.owner_id=? AND e.source_kind IN "
                "('livekit_browser_transcript','user_explicit','model_generated') "
                "AND e.text<>'' ORDER BY e.created_at DESC,e.rowid DESC LIMIT ?",
                (self.owner_id, limit),
            ).fetchall()
        return [{"role": row["role"], "text": row["text"][:240],
                 "source_kind": row["source_kind"]} for row in reversed(rows)]

    def call_transcript(self, call_id, limit=500):
        """Return an owner's saved turns; a different owner's ID looks absent."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT e.role,e.text,e.created_at,e.rowid AS seq "
                "FROM call_transcripts c JOIN events e ON e.id=c.event_id "
                "JOIN sessions s ON s.id=e.session_id "
                "WHERE c.call_id=? AND s.owner_id=? "
                "AND e.source_kind='livekit_browser_transcript' "
                "ORDER BY e.created_at,e.rowid LIMIT ?",
                (call_id, self.owner_id, limit + 1),
            ).fetchall()
        if not rows:
            return None
        return {"call_id": call_id, "turns": [
            {"role": row["role"], "text": row["text"], "created_at": row["created_at"]}
            for row in rows[:limit]], "truncated": len(rows) > limit,
            "audio_saved": False}

    def call_reflection(self, call_id):
        with self._connect() as db:
            row = db.execute("SELECT call_id,status,text,source_event_ids,model,"
                             "prompt_version,created_at,updated_at FROM call_reflections "
                             "WHERE owner_id=? AND call_id=?",
                             (self.owner_id, call_id)).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["source_event_ids"] = json.loads(item["source_event_ids"])
        return item

    def recent_call_reflections(self, limit=30):
        with self._connect() as db:
            rows = db.execute("SELECT call_id,status,text,source_event_ids,model,"
                              "prompt_version,created_at,updated_at FROM call_reflections "
                              "WHERE owner_id=? ORDER BY updated_at DESC LIMIT ?",
                              (self.owner_id, limit)).fetchall()
        return [{**dict(row), "source_event_ids": json.loads(row["source_event_ids"])}
                for row in rows]

    def claim_call_reflection(self, call_id, prompt_version, retry=False):
        """Atomically claim a call and return only this owner's immutable source events."""
        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT e.id,e.role,e.text FROM call_transcripts c "
                "JOIN events e ON e.id=c.event_id JOIN sessions s ON s.id=e.session_id "
                "WHERE c.call_id=? AND s.owner_id=? "
                "AND e.source_kind='livekit_browser_transcript' "
                "ORDER BY e.created_at,e.rowid LIMIT 501", (call_id, self.owner_id)
            ).fetchall()
            if not rows:
                return None, None
            existing = db.execute(
                "SELECT status FROM call_reflections WHERE owner_id=? AND call_id=?",
                (self.owner_id, call_id)).fetchone()
            if existing:
                if existing["status"] != "failed" or not retry:
                    return "existing", None
                db.execute("UPDATE call_reflections SET status='processing',text=NULL,"
                           "source_event_ids=?,model=NULL,prompt_version=?,updated_at=? "
                           "WHERE owner_id=? AND call_id=?",
                           (json.dumps([r["id"] for r in rows]), prompt_version, now,
                            self.owner_id, call_id))
            else:
                db.execute("INSERT INTO call_reflections "
                           "(owner_id,call_id,status,text,source_event_ids,model,"
                           "prompt_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                           (self.owner_id, call_id, "processing", None,
                            json.dumps([r["id"] for r in rows]), None,
                            prompt_version, now, now))
        return "claimed", [dict(row) for row in rows]

    def finish_call_reflection(self, call_id, status, text=None, model=None):
        if status not in {"generated", "skipped", "failed"}:
            raise ValueError("invalid reflection status")
        with self._connect() as db:
            db.execute("UPDATE call_reflections SET status=?,text=?,model=?,updated_at=? "
                       "WHERE owner_id=? AND call_id=? AND status='processing'",
                       (status, text if status == "generated" else None, model,
                        time.time(), self.owner_id, call_id))

    def history(self, sid, limit=100):
        with self._connect() as db:
            rows = db.execute("SELECT * FROM (SELECT e.rowid AS seq,e.* FROM events e "
                              "JOIN sessions s ON s.id=e.session_id "
                              "WHERE e.session_id=? AND s.owner_id=? "
                              "ORDER BY e.created_at DESC, e.rowid DESC LIMIT ?) "
                              "ORDER BY created_at, seq", (sid, self.owner_id, limit)).fetchall()
            event_ids = [row["id"] for row in rows]
            reads = {}
            citations = {}
            documents = {}
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
                document_ids = [row["document_id"] for row in rows if row["document_id"]]
                if document_ids:
                    doc_placeholders = ",".join("?" for _ in document_ids)
                    found_docs = db.execute(
                        "SELECT id,filename,kind,sha256,extract_status "
                        "FROM documents WHERE owner_id=? AND id IN (" + doc_placeholders + ")",
                        (self.owner_id, *document_ids),
                    ).fetchall()
                    documents = {row["id"]: dict(row) for row in found_docs}
                cited_ids = [row["page_read_id"] for row in rows if row["page_read_id"]]
                if cited_ids:
                    cited_placeholders = ",".join("?" for _ in cited_ids)
                    cited_rows = db.execute(
                        "SELECT id,url,fetched_at,sha256,content_type,text "
                        "FROM page_reads WHERE owner_id=? AND id IN (" +
                        cited_placeholders + ") AND status='read'",
                        (self.owner_id, *cited_ids),
                    ).fetchall()
                    citations = {row["id"]: self._citation(row) for row in cited_rows}
        return [{"id": x["id"], "session_id": x["session_id"], "role": x["role"],
                 "text": x["text"], "image_id": x["image_id"],
                 "document": documents.get(x["document_id"]), "url": x["link_url"],
                 "source_kind": x["source_kind"], "created_at": x["created_at"],
                 "page_read": reads.get(x["id"]),
                 "source_citation": citations.get(x["page_read_id"])}
                for x in rows]

    @staticmethod
    def _citation(row):
        if not row:
            return None
        return {"page_read_id": row["id"], "url": row["url"],
                "fetched_at": row["fetched_at"], "sha256": row["sha256"],
                "content_type": row["content_type"],
                "excerpt_chars": min(len(row["text"] or ""), 4000)}

    def _usable_page_context(self, db, page_id, now, session_id=None):
        if not page_id:
            return None
        row = db.execute(
            "SELECT p.id,p.url,p.requested_at,p.status,p.fetched_at,p.sha256,"
            "p.content_type,p.text,e.link_url,e.session_id FROM page_reads p "
            "JOIN events e ON e.id=p.source_event_id "
            "JOIN sessions s ON s.id=e.session_id "
            "WHERE p.id=? AND p.owner_id=? AND s.owner_id=? "
            "AND e.role='user' AND e.source_kind='user_explicit' "
            "AND e.link_url IS NOT NULL",
            (page_id, self.owner_id, self.owner_id),
        ).fetchone()
        if not row or row["status"] != "read":
            return None
        if session_id is not None and row["session_id"] != session_id:
            return None
        if not (0 <= now - row["requested_at"] <= 24 * 60 * 60):
            return None
        if (not isinstance(row["url"], str) or
                not row["fetched_at"] or not isinstance(row["sha256"], str) or
                not re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) or
                row["content_type"] not in {"text/html", "text/plain"} or
                not isinstance(row["text"], str) or not row["text"].strip()):
            return None
        try:
            _, source_url = _validated_url(row["link_url"])
            _, fetched_url = _validated_url(row["url"])
            fetched_stamp = datetime.fromisoformat(row["fetched_at"])
            if fetched_stamp.tzinfo is None:
                return None
            fetched_time = fetched_stamp.timestamp()
        except (PageReadError, ValueError, TypeError):
            return None
        if source_url != fetched_url or not 0 <= now - fetched_time <= 24 * 60 * 60:
            return None
        return row

    def usable_page_context(self, page_id, session_id):
        """Return one fresh, server-fetched, owner-linked excerpt and citation."""
        with self._connect() as db:
            row = self._usable_page_context(db, page_id, time.time(), session_id)
        if not row:
            return None
        url = urlsplit(row["url"])
        model_url = url._replace(query="", fragment="").geturl()
        return {"text": row["text"][:4000],
                "model_url": model_url,
                "source_citation": self._citation(row)}

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

    def put_document(self, filename, kind, mime_type, data, extracted_text, extract_status):
        did = str(uuid4())
        digest = sha256(data).hexdigest()
        with self._connect() as db:
            db.execute(
                "INSERT INTO documents(id,filename,kind,mime_type,data,sha256,"
                "extracted_text,extract_status,created_at,owner_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (did, filename, kind, mime_type, data, digest, extracted_text,
                 extract_status, time.time(), self.owner_id),
            )
        return {"id": did, "filename": filename, "kind": kind,
                "sha256": digest, "extract_status": extract_status}

    def get_document(self, did, include_data=False):
        fields = "id,filename,kind,mime_type,sha256,extracted_text,extract_status"
        if include_data:
            fields += ",data"
        with self._connect() as db:
            row = db.execute("SELECT " + fields + " FROM documents "
                             "WHERE id=? AND owner_id=?", (did, self.owner_id)).fetchone()
        return dict(row) if row else None

    def add_memory(self, text, source_event_id=None):
        mid = str(uuid4())
        with self._connect() as db:
            if source_event_id is not None:
                source = db.execute(
                    "SELECT 1 FROM events e JOIN sessions s ON s.id=e.session_id "
                    "WHERE e.id=? AND s.owner_id=? AND e.role='user' "
                    "AND e.source_kind='user_explicit'",
                    (source_event_id, self.owner_id),
                ).fetchone()
                if source is None:
                    raise StoreUnavailable("来源事件不存在")
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

    def memory_index(self, query=None, limit=20, terms=None):
        """Small owner-scoped index; query or terms match literal text only.

        Call memory_details with selected IDs before using a memory as context.
        An empty query lists the newest items. No other owner's rows are read.
        """
        limit = max(1, min(int(limit), 50))
        query = (query or "").strip()[:120]
        params = [self.owner_id]
        where = "owner_id=?"
        search_terms = [query] if query else []
        if terms is not None:
            search_terms = [term.strip()[:40] for term in list(terms)[:12]
                            if isinstance(term, str) and term.strip()]
            if not search_terms:
                return []
        if search_terms:
            where += " AND (" + " OR ".join("text LIKE ? ESCAPE '\\'"
                                              for _ in search_terms) + ")"
            params.extend("%" + term.replace("\\", "\\\\").replace("%", "\\%")
                          .replace("_", "\\_") + "%" for term in search_terms)
        params.append(limit)
        with self._connect() as db:
            rows = db.execute(
                "SELECT id,substr(text,1,100) AS preview,source_kind,"
                "source_event_id,created_at FROM memories WHERE " + where +
                " ORDER BY created_at DESC,rowid DESC LIMIT ?", params,
            ).fetchall()
        return [dict(row) for row in rows]

    def memory_details(self, ids):
        """Fetch full text only for selected IDs from this owner's memory."""
        ids = list(dict.fromkeys(ids))[:50]
        if not ids:
            return []
        if any(not isinstance(mid, str) or not mid for mid in ids):
            raise ValueError("记忆编号不正确")
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as db:
            rows = db.execute(
                "SELECT m.id,m.text,m.source_kind,m.source_event_id,m.created_at,"
                "EXISTS(SELECT 1 FROM events e JOIN sessions s ON s.id=e.session_id "
                "WHERE e.id=m.source_event_id AND e.role='user' "
                "AND e.source_kind='user_explicit' "
                "AND s.owner_id=m.owner_id) AS source_linked "
                "FROM memories m "
                f"WHERE m.owner_id=? AND m.id IN ({placeholders})",
                [self.owner_id, *ids],
            ).fetchall()
        by_id = {row["id"]: dict(row) for row in rows}
        return [by_id[mid] for mid in ids if mid in by_id]

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

    def knowledge_index(self, query=None, limit=20):
        """Search titles/content literally, returning only a small owner index."""
        limit = max(1, min(int(limit), 50))
        query = (query or "").strip()[:120]
        params = [self.owner_id]
        where = "owner_id=?"
        if query:
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where += " AND (title LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\')"
            params.extend((f"%{escaped}%", f"%{escaped}%"))
        params.append(limit)
        with self._connect() as db:
            rows = db.execute(
                "SELECT id,title,substr(content,1,100) AS preview,source_kind,"
                "source_event_id,source_url,created_at FROM knowledge_items WHERE " +
                where + " ORDER BY created_at DESC,rowid DESC LIMIT ?", params,
            ).fetchall()
        return [dict(row) for row in rows]

    def knowledge_details(self, ids):
        """Fetch selected full knowledge items; never infer source verification."""
        ids = list(dict.fromkeys(ids))[:50]
        if not ids:
            return []
        if any(not isinstance(kid, str) or not kid for kid in ids):
            raise ValueError("知识编号不正确")
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as db:
            rows = db.execute(
                "SELECT id,title,content,source_url,source_kind,source_event_id,created_at "
                f"FROM knowledge_items WHERE owner_id=? AND id IN ({placeholders})",
                [self.owner_id, *ids],
            ).fetchall()
        by_id = {row["id"]: dict(row) for row in rows}
        return [by_id[kid] for kid in ids if kid in by_id]

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
