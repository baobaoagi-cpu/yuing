"""Account-scoped profile candidates backed by the conversation SQLite volume.

Profile memory, relation graph, and personal knowledge live in three separate
SQLite files. This module is not a vector database and does not turn model
guesses into verified facts. Only an authenticated server path may construct
it with the account ID from the session, never browser JSON.
"""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


class ProfileSourceError(ValueError):
    """The claimed evidence is missing, not first-person, or belongs to another account."""


_SELF_REPORT = {
    "preference": ("我喜欢", "我喜歡", "我不喜欢", "我不喜歡", "我更喜欢", "我更喜歡"),
    "habit": ("我习惯", "我習慣", "我通常", "我的习惯", "我的習慣"),
    "trait": ("我是", "我很", "我觉得自己", "我覺得自己", "我的性格"),
    "need": ("我希望", "我需要", "我想要"),
}

# These are account-opening statements supplied by the user on 2026-10-14.
# They are not inferred from conversation and are not verified biographical facts.
_MING_SEED = {
    "display_name": "王跃明",
    "gender": "男性",
    "occupation": "资深工程师",
    "expertise": "熟悉 AI 与后端",
    "relationship": "威廷的好朋友",
    "collaboration": "协助准备陈端倪的中国区服务器",
    "account_purpose": "王跃明是陈端倪的独立陪伴使用者；由他主动打开对话",
}
_SEED_SOURCE = "account_setup_user_provided"
_SEED_DATE = "2026-10-14"  # Date precision only; no account-opening clock time was supplied.


class NicoProfileStore:
    """Narrow profile API; owner_id must come from the authenticated session.

    The ChatStore path is read only for source ownership checks. The three
    concepts remain distinct files: profile_facts (memory), profile_edges
    (candidate graph), and profile_knowledge. No embedding index or semantic
    similarity is implied.
    """

    def __init__(self, chat_db_path: str, owner_id: str, storage_root: str):
        if not chat_db_path or chat_db_path == ":memory:" or not Path(chat_db_path).expanduser().is_absolute():
            raise ValueError("需配置绝对路径的持久化资料库")
        if not storage_root or not Path(storage_root).expanduser().is_absolute():
            raise ValueError("需配置绝对路径的独立资料库目录")
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise ValueError("用户归属未配置")
        self.chat_db_path = Path(chat_db_path).expanduser()
        root = Path(storage_root).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        self.memory_path = root / "memory.sqlite3"
        self.graph_path = root / "graph.sqlite3"
        self.knowledge_path = root / "knowledge.sqlite3"
        self.owner_id = owner_id
        # ChatStore owns the base schema. Fail closed if it is not initialized.
        if not self.chat_db_path.exists():
            raise ValueError("请先初始化对话资料库")
        with self._connect(self.chat_db_path) as db:
            required = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
                "('sessions','events')")}
            if required != {"sessions", "events"}:
                raise ValueError("请先初始化对话资料库")
        with self._connect(self.memory_path) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS profile_identity (
                    owner_id TEXT PRIMARY KEY, display_name TEXT NOT NULL,
                    source_kind TEXT NOT NULL, source_event_id TEXT,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS profile_consent (
                    owner_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0
                    CHECK(enabled IN (0,1)), updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS profile_facts (
                    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                    category TEXT NOT NULL CHECK(category IN ('preference','habit','trait','need')),
                    text TEXT NOT NULL, source_event_id TEXT NOT NULL,
                    evidence_quote TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'candidate'
                    CHECK(status='candidate'), created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS profile_facts_owner_time
                    ON profile_facts(owner_id, updated_at DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS profile_facts_source_unique
                    ON profile_facts(owner_id,category,source_event_id,evidence_quote);
                CREATE TABLE IF NOT EXISTS account_seed_revisions (
                    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, field TEXT NOT NULL,
                    value TEXT, source_kind TEXT NOT NULL, source_date TEXT NOT NULL,
                    recorded_at REAL NOT NULL, confidence TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active','retracted'))
                );
                CREATE INDEX IF NOT EXISTS account_seed_owner_field_time
                    ON account_seed_revisions(owner_id,field,recorded_at DESC);
            """)
            if owner_id == "nicole":
                db.execute("INSERT OR IGNORE INTO profile_identity "
                           "(owner_id,display_name,source_kind,source_event_id,updated_at) "
                           "VALUES(?,?,?,?,?)",
                           (owner_id, "Nicole", "account_seed", None, time.time()))
            if owner_id == "MING":
                for field, value in _MING_SEED.items():
                    exists = db.execute(
                        "SELECT 1 FROM account_seed_revisions WHERE owner_id=? AND field=? LIMIT 1",
                        (owner_id, field)).fetchone()
                    if not exists:
                        db.execute("INSERT INTO account_seed_revisions VALUES(?,?,?,?,?,?,?,?,?)",
                                   (str(uuid4()), owner_id, field, value, _SEED_SOURCE,
                                    _SEED_DATE, time.time(), "user_provided_unverified", "active"))
        with self._connect(self.graph_path) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS profile_edges (
                    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                    subject_fact_id TEXT NOT NULL, predicate TEXT NOT NULL,
                    object_fact_id TEXT NOT NULL, source_event_id TEXT NOT NULL,
                    evidence_quote TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'candidate'
                    CHECK(status='candidate'), created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS profile_edges_owner_time
                    ON profile_edges(owner_id, created_at DESC);
            """)
        with self._connect(self.knowledge_path) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS profile_knowledge (
                    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                    title TEXT NOT NULL, content TEXT NOT NULL,
                    source_event_id TEXT NOT NULL, evidence_quote TEXT NOT NULL,
                    source_kind TEXT NOT NULL DEFAULT 'user_explicit',
                    status TEXT NOT NULL DEFAULT 'candidate' CHECK(status='candidate'),
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS profile_knowledge_owner_time
                    ON profile_knowledge(owner_id, created_at DESC);
            """)

    @contextmanager
    def _connect(self, path=None):
        db = sqlite3.connect(str(path or self.memory_path), timeout=5)
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

    def _user_source(self, db, event_id: str, quote: str):
        if not isinstance(event_id, str) or not event_id or not isinstance(quote, str):
            raise ProfileSourceError("来源无效")
        with self._connect(self.chat_db_path) as source_db:
            row = source_db.execute(
                "SELECT e.text,e.source_kind FROM events e "
                "JOIN sessions s ON s.id=e.session_id "
                "WHERE e.id=? AND e.role='user' AND s.owner_id=?",
                (event_id, self.owner_id)).fetchone()
        if (row is None or row["source_kind"] not in
                {"user_explicit", "livekit_browser_transcript"} or
                not quote.strip() or quote.strip() not in row["text"]):
            raise ProfileSourceError("来源不是本用户的直接表达")
        return row

    def identity(self):
        if self.owner_id == "MING":
            name = self.seed_facts().get("display_name")
            if name and name["status"] == "active":
                return {"display_name": name["value"], "source_kind": name["source_kind"],
                        "source_event_id": None, "updated_at": name["recorded_at"]}
        with self._connect() as db:
            row = db.execute("SELECT display_name,source_kind,source_event_id,updated_at "
                             "FROM profile_identity WHERE owner_id=?",
                             (self.owner_id,)).fetchone()
        return dict(row) if row else None

    def seed_facts(self):
        """Latest account-opening values, scoped to this authenticated owner."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT field,value,source_kind,source_date,recorded_at,confidence,status "
                "FROM account_seed_revisions WHERE owner_id=? "
                "ORDER BY recorded_at DESC,rowid DESC", (self.owner_id,)).fetchall()
        result = {}
        for row in rows:
            result.setdefault(row["field"], dict(row))
        return result

    def correct_seed_fact(self, field: str, value: str | None):
        """Record an authenticated user's correction or retraction without erasing provenance."""
        if field not in _MING_SEED or field not in self.seed_facts():
            raise ValueError("种子字段不存在")
        if value is not None and (not isinstance(value, str) or not 1 <= len(value.strip()) <= 120
                                  or any(ord(ch) < 32 for ch in value)):
            raise ValueError("更正内容不正确")
        clean = value.strip() if isinstance(value, str) else None
        with self._connect() as db:
            db.execute("INSERT INTO account_seed_revisions VALUES(?,?,?,?,?,?,?,?,?)",
                       (str(uuid4()), self.owner_id, field, clean,
                        "authenticated_user_correction", time.strftime("%Y-%m-%d", time.gmtime()),
                        time.time(), "user_corrected_unverified",
                        "active" if clean is not None else "retracted"))
        return self.seed_facts()[field]

    def consent_status(self) -> bool:
        with self._connect() as db:
            row = db.execute("SELECT enabled FROM profile_consent WHERE owner_id=?",
                             (self.owner_id,)).fetchone()
        return bool(row["enabled"]) if row else False

    def consent_decided(self) -> bool:
        """Distinguish an explicit decline from a user who has not answered."""
        with self._connect() as db:
            row = db.execute("SELECT 1 FROM profile_consent WHERE owner_id=?",
                             (self.owner_id,)).fetchone()
        return row is not None

    def set_consent(self, enabled: bool) -> bool:
        """Call only from an authenticated, user-initiated settings action."""
        if type(enabled) is not bool:
            raise ValueError("同意状态必须为布尔值")
        with self._connect() as db:
            db.execute("INSERT INTO profile_consent(owner_id,enabled,updated_at) "
                       "VALUES(?,?,?) ON CONFLICT(owner_id) DO UPDATE SET "
                       "enabled=excluded.enabled,updated_at=excluded.updated_at",
                       (self.owner_id, int(enabled), time.time()))
        return self.consent_status()

    def _require_consent(self):
        if not self.consent_status():
            raise PermissionError("尚未同意建立个人资料候选")

    def correct_display_name(self, name: str, source_event_id: str):
        """Only a first-person user correction may override the account seed."""
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
            raise ValueError("称呼长度不正确")
        name = name.strip()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._user_source(db, source_event_id, name)
            if not any(signal in row["text"] for signal in
                       ("叫我", "我叫", "称呼我", "稱呼我", "我的名字是")):
                raise ProfileSourceError("称呼更正需要用户明确表达")
            now = time.time()
            db.execute("INSERT INTO profile_identity "
                       "(owner_id,display_name,source_kind,source_event_id,updated_at) "
                       "VALUES(?,?,?,?,?) ON CONFLICT(owner_id) DO UPDATE SET "
                       "display_name=excluded.display_name,source_kind=excluded.source_kind,"
                       "source_event_id=excluded.source_event_id,updated_at=excluded.updated_at",
                       (self.owner_id, name, "user_correction", source_event_id, now))
        return self.identity()

    def add_fact(self, category: str, text: str, source_event_id: str,
                 evidence_quote: str):
        """Record a first-person statement as a *candidate*, not a verified trait."""
        self._require_consent()
        if category not in _SELF_REPORT or not isinstance(text, str) or not 1 <= len(text.strip()) <= 200:
            raise ValueError("记忆分类或内容不正确")
        if not isinstance(evidence_quote, str) or not 1 <= len(evidence_quote.strip()) <= 300:
            raise ValueError("来源摘录不正确")
        quote = evidence_quote.strip()
        if not quote.startswith(_SELF_REPORT[category]):
            raise ProfileSourceError("只接受第一人称自述作为候选")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._user_source(db, source_event_id, quote)
            existing = db.execute("SELECT id FROM profile_facts WHERE owner_id=? "
                                  "AND category=? AND source_event_id=? AND evidence_quote=?",
                                  (self.owner_id, category, source_event_id, quote)).fetchone()
            if existing:
                return self.get_fact(existing["id"])
            fid, now = str(uuid4()), time.time()
            db.execute("INSERT INTO profile_facts "
                       "(id,owner_id,category,text,source_event_id,evidence_quote,status,created_at,updated_at) "
                       "VALUES(?,?,?,?,?,?,?,?,?)",
                       (fid, self.owner_id, category, text.strip(), source_event_id,
                        quote, "candidate", now, now))
        return self.get_fact(fid)

    def propose_from_user_event(self, event_id: str):
        """Conservative first-person candidate extractor; no LLM or quoted web text.

        Only a short message beginning with an explicit self-report is eligible.
        Richer inferences belong in a separate reviewed flow, never here.
        """
        self._require_consent()
        with self._connect(self.chat_db_path) as db:
            row = db.execute("SELECT e.text,e.source_kind,e.role FROM events e "
                             "JOIN sessions s ON s.id=e.session_id "
                             "WHERE e.id=? AND s.owner_id=?", (event_id, self.owner_id)).fetchone()
        if row is None or row["role"] != "user" or row["source_kind"] not in {
                "user_explicit", "livekit_browser_transcript"}:
            raise ProfileSourceError("来源不是本用户的直接表达")
        statement = row["text"].strip()
        if not 1 <= len(statement) <= 200 or "\n" in statement or "http" in statement.lower():
            return None
        for category, prefixes in _SELF_REPORT.items():
            if statement.startswith(prefixes):
                return self.add_fact(category, statement, event_id, statement)
        return None

    def get_fact(self, fact_id: str):
        with self._connect() as db:
            row = db.execute("SELECT id,category,text,source_event_id,evidence_quote,status,"
                             "created_at,updated_at FROM profile_facts "
                             "WHERE id=? AND owner_id=?", (fact_id, self.owner_id)).fetchone()
        return dict(row) if row else None

    def facts(self, limit=50):
        limit = max(1, min(int(limit), 100))
        with self._connect() as db:
            rows = db.execute("SELECT id,category,text,source_event_id,evidence_quote,status,"
                              "created_at,updated_at FROM profile_facts WHERE owner_id=? "
                              "ORDER BY updated_at DESC,rowid DESC LIMIT ?",
                              (self.owner_id, limit)).fetchall()
        return [dict(row) for row in rows]

    def correct_fact(self, fact_id: str, text: str, source_event_id: str,
                     evidence_quote: str):
        """Replace a candidate using new user evidence; remove edges tied to old meaning."""
        self._require_consent()
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 200:
            raise ValueError("记忆内容不正确")
        if not isinstance(evidence_quote, str) or not 1 <= len(evidence_quote.strip()) <= 300:
            raise ValueError("来源摘录不正确")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT category FROM profile_facts WHERE id=? AND owner_id=?",
                             (fact_id, self.owner_id)).fetchone()
            if old is None:
                return None
            quote = evidence_quote.strip()
            if not quote.startswith(_SELF_REPORT[old["category"]]):
                raise ProfileSourceError("只接受第一人称自述作为更正")
            self._user_source(db, source_event_id, quote)
            db.execute("UPDATE profile_facts SET text=?,source_event_id=?,"
                       "evidence_quote=?,updated_at=? WHERE id=? AND owner_id=?",
                       (text.strip(), source_event_id, quote, time.time(), fact_id,
                        self.owner_id))
        self._delete_fact_edges(fact_id)
        return self.get_fact(fact_id)

    def _delete_fact_edges(self, fact_id: str):
        with self._connect(self.graph_path) as db:
            db.execute("DELETE FROM profile_edges WHERE owner_id=? "
                       "AND (subject_fact_id=? OR object_fact_id=?)",
                       (self.owner_id, fact_id, fact_id))

    def delete_fact(self, fact_id: str):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cur = db.execute("DELETE FROM profile_facts WHERE id=? AND owner_id=?",
                             (fact_id, self.owner_id))
        self._delete_fact_edges(fact_id)
        return cur.rowcount > 0

    def add_edge(self, subject_fact_id: str, predicate: str, object_fact_id: str,
                 source_event_id: str, evidence_quote: str):
        """Store a sourced possible association; never assert causality."""
        self._require_consent()
        if (not isinstance(predicate, str) or not 1 <= len(predicate.strip()) <= 80 or
                not isinstance(evidence_quote, str) or not 1 <= len(evidence_quote.strip()) <= 300 or
                subject_fact_id == object_fact_id):
            raise ValueError("关系候选不正确")
        if not self.get_fact(subject_fact_id) or not self.get_fact(object_fact_id):
            raise ProfileSourceError("关系端点不属于当前用户")
        with self._connect(self.graph_path) as db:
            db.execute("BEGIN IMMEDIATE")
            self._user_source(db, source_event_id, evidence_quote.strip())
            eid, now = str(uuid4()), time.time()
            db.execute("INSERT INTO profile_edges "
                       "(id,owner_id,subject_fact_id,predicate,object_fact_id,"
                       "source_event_id,evidence_quote,status,created_at) "
                       "VALUES(?,?,?,?,?,?,?,?,?)",
                       (eid, self.owner_id, subject_fact_id, predicate.strip(),
                        object_fact_id, source_event_id, evidence_quote.strip(),
                        "candidate", now))
        return self.get_edge(eid)

    def get_edge(self, edge_id: str):
        with self._connect(self.graph_path) as db:
            row = db.execute("SELECT id,subject_fact_id,predicate,object_fact_id,"
                             "source_event_id,evidence_quote,status,created_at FROM profile_edges "
                             "WHERE id=? AND owner_id=?", (edge_id, self.owner_id)).fetchone()
        if row and self.get_fact(row["subject_fact_id"]) and self.get_fact(row["object_fact_id"]):
            return dict(row)
        return None

    def edges(self, limit=50):
        limit = max(1, min(int(limit), 100))
        with self._connect(self.graph_path) as db:
            rows = db.execute("SELECT e.id,e.subject_fact_id,e.predicate,e.object_fact_id,"
                              "e.source_event_id,e.evidence_quote,e.status,e.created_at "
                              "FROM profile_edges e "
                              "WHERE e.owner_id=? ORDER BY e.created_at DESC,e.rowid DESC LIMIT ?",
                              (self.owner_id, limit)).fetchall()
        return [dict(row) for row in rows if self.get_fact(row["subject_fact_id"])
                and self.get_fact(row["object_fact_id"])]

    def delete_edge(self, edge_id: str):
        with self._connect(self.graph_path) as db:
            cur = db.execute("DELETE FROM profile_edges WHERE id=? AND owner_id=?",
                             (edge_id, self.owner_id))
            return cur.rowcount > 0

    def add_knowledge(self, title: str, content: str, source_event_id: str,
                      evidence_quote: str):
        """Distinct sourced personal knowledge candidate, not a vector index."""
        self._require_consent()
        if (not isinstance(title, str) or not 1 <= len(title.strip()) <= 120 or
                not isinstance(content, str) or not 1 <= len(content.strip()) <= 2000 or
                not isinstance(evidence_quote, str) or
                not 1 <= len(evidence_quote.strip()) <= 300):
            raise ValueError("知识候选内容不正确")
        with self._connect(self.knowledge_path) as db:
            db.execute("BEGIN IMMEDIATE")
            self._user_source(db, source_event_id, evidence_quote.strip())
            kid, now = str(uuid4()), time.time()
            db.execute("INSERT INTO profile_knowledge "
                       "(id,owner_id,title,content,source_event_id,evidence_quote,"
                       "source_kind,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                       (kid, self.owner_id, title.strip(), content.strip(),
                        source_event_id, evidence_quote.strip(), "user_explicit",
                        "candidate", now))
        return {"id": kid, "title": title.strip(), "content": content.strip(),
                "source_event_id": source_event_id,
                "evidence_quote": evidence_quote.strip(), "status": "candidate",
                "created_at": now}

    def knowledge(self, limit=50):
        limit = max(1, min(int(limit), 100))
        with self._connect(self.knowledge_path) as db:
            rows = db.execute("SELECT id,title,content,source_event_id,evidence_quote,"
                              "source_kind,status,created_at FROM profile_knowledge "
                              "WHERE owner_id=? ORDER BY created_at DESC,rowid DESC LIMIT ?",
                              (self.owner_id, limit)).fetchall()
        return [dict(row) for row in rows]

    def delete_knowledge(self, item_id: str):
        with self._connect(self.knowledge_path) as db:
            cur = db.execute("DELETE FROM profile_knowledge WHERE id=? AND owner_id=?",
                             (item_id, self.owner_id))
            return cur.rowcount > 0
