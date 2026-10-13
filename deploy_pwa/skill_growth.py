"""Reviewable skill candidates. This module never downloads or executes tools.

The caller must authenticate ``owner_id`` and verify ``source_event_id`` belongs
to that owner's explicit user message before calling ``propose``. A candidate
marked ready is still not installed, enabled, or available to Hermes.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4


STATUSES = frozenset({"candidate", "reviewed", "practiced", "ready", "rejected"})
KINDS = frozenset({"skill", "mcp", "api", "local_feature"})
COSTS = frozenset({"free", "unknown", "paid"})


class SkillGrowthError(ValueError):
    pass


_EXPLICIT_NEED = re.compile(
    r"^(?:我希望你能|我想让你学会|我想讓你學會|我想要你學會|我想要你学会)"
    r"(?P<need>[^。！？!?\n]{2,120})[。！？!?]?$"
)
_DIRECT_QUESTION = re.compile(
    r"^你可以(?:学会|學會)(?P<need>[^。！？!?\n]{2,120})(?:吗|嗎)[？?]?$"
)


def explicit_skill_need(text: str) -> str | None:
    """Recognize only direct user requests; do not infer a skill from chat.

    Caller must still verify the event is an authenticated user's own message.
    """
    if not isinstance(text, str) or len(text) > 200:
        return None
    value = text.strip()
    match = _EXPLICIT_NEED.fullmatch(value) or _DIRECT_QUESTION.fullmatch(value)
    if not match:
        return None
    need = match.group("need").strip(" ，,：: ")
    return need if len(need) >= 2 else None


def _required(value: str, label: str, max_length: int = 500) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > max_length:
        raise SkillGrowthError(f"{label} 不能为空且不得超过 {max_length} 字")
    return value.strip()


def _scopes(values: list[str]) -> list[str]:
    if not isinstance(values, list) or len(values) > 20:
        raise SkillGrowthError("权限范围格式错误")
    result = []
    for value in values:
        scope = _required(value, "权限范围", 100)
        if scope not in result:
            result.append(scope)
    return result


class SkillGrowthStore:
    """Tenant-scoped, append-evidence candidate registry in a separate SQLite DB."""

    def __init__(self, path: str):
        if not path or path == ":memory:" or not Path(path).is_absolute():
            raise SkillGrowthError("技能候选资料库必须使用绝对文件路径")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS skill_candidates (
                    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                    source_event_id TEXT NOT NULL, user_need TEXT NOT NULL,
                    name TEXT NOT NULL, kind TEXT NOT NULL,
                    source_url TEXT, license_id TEXT NOT NULL,
                    requested_scopes TEXT NOT NULL, cost TEXT NOT NULL,
                    status TEXT NOT NULL, review_actor TEXT, review_note TEXT,
                    license_verified INTEGER NOT NULL DEFAULT 0,
                    cost_verified INTEGER NOT NULL DEFAULT 0,
                    practice_case TEXT, practice_evidence TEXT,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS skill_candidates_owner_time
                    ON skill_candidates(owner_id, created_at DESC);
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
    def _row(row):
        if row is None:
            return None
        item = dict(row)
        item["requested_scopes"] = json.loads(item["requested_scopes"])
        return item

    def propose(self, *, owner_id: str, source_event_id: str, user_need: str,
                name: str, kind: str, source_url: str | None,
                license_id: str, requested_scopes: list[str], cost: str):
        owner_id = _required(owner_id, "用户", 64)
        source_event_id = _required(source_event_id, "用户消息来源", 100)
        user_need = _required(user_need, "需求", 1000)
        name = _required(name, "能力名称", 120)
        license_id = _required(license_id, "许可证", 120)
        if kind not in KINDS or cost not in COSTS:
            raise SkillGrowthError("候选类型或成本分类错误")
        if source_url is not None:
            source_url = _required(source_url, "来源网址", 1000)
            parsed = urlsplit(source_url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise SkillGrowthError("来源网址必须是无凭证的 HTTPS 地址")
        scopes = _scopes(requested_scopes)
        cid, now = str(uuid4()), time.time()
        with self._db() as db:
            db.execute("""INSERT INTO skill_candidates
                (id, owner_id, source_event_id, user_need, name, kind, source_url,
                 license_id, requested_scopes, cost, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'candidate', ?, ?)""",
                (cid, owner_id, source_event_id, user_need, name, kind,
                 source_url, license_id, json.dumps(scopes), cost, now, now))
        return self.get(owner_id, cid)

    def get(self, owner_id: str, candidate_id: str):
        with self._db() as db:
            row = db.execute("SELECT * FROM skill_candidates WHERE owner_id=? AND id=?",
                             (owner_id, candidate_id)).fetchone()
        return self._row(row)

    def list(self, owner_id: str, limit: int = 50):
        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise SkillGrowthError("列表上限错误")
        with self._db() as db:
            rows = db.execute("SELECT * FROM skill_candidates WHERE owner_id=? "
                              "ORDER BY created_at DESC LIMIT ?", (owner_id, limit)).fetchall()
        return [self._row(row) for row in rows]

    def review(self, owner_id: str, candidate_id: str, *, actor: str,
               note: str, approve: bool, license_verified: bool = False,
               cost_verified: bool = False):
        actor = _required(actor, "审核者", 120)
        note = _required(note, "审核说明", 1000)
        if not isinstance(approve, bool):
            raise SkillGrowthError("审核决定错误")
        if not isinstance(license_verified, bool) or not isinstance(cost_verified, bool):
            raise SkillGrowthError("核验结果错误")
        if approve and (not license_verified or not cost_verified):
            raise SkillGrowthError("通过审核前必须核实许可证与成本")
        with self._db() as db:
            cur = db.execute("""UPDATE skill_candidates SET status=?, review_actor=?,
                review_note=?, license_verified=?, cost_verified=?, updated_at=?
                WHERE owner_id=? AND id=? AND status='candidate'""",
                ("reviewed" if approve else "rejected", actor, note,
                 int(license_verified), int(cost_verified), time.time(),
                 owner_id, candidate_id))
            if cur.rowcount != 1:
                raise SkillGrowthError("候选不存在或审核状态不允许变更")
        return self.get(owner_id, candidate_id)

    def record_synthetic_practice(self, owner_id: str, candidate_id: str,
                                  *, case: str, evidence: str, passed: bool):
        case = _required(case, "合成测试案例", 500)
        evidence = _required(evidence, "测试证据", 1000)
        if not isinstance(passed, bool):
            raise SkillGrowthError("测试结果错误")
        with self._db() as db:
            cur = db.execute("""UPDATE skill_candidates SET status=?, practice_case=?,
                practice_evidence=?, updated_at=?
                WHERE owner_id=? AND id=? AND status='reviewed'""",
                ("practiced" if passed else "rejected", case, evidence,
                 time.time(), owner_id, candidate_id))
            if cur.rowcount != 1:
                raise SkillGrowthError("候选未通过人工审核")
        return self.get(owner_id, candidate_id)

    def mark_ready(self, owner_id: str, candidate_id: str):
        """Ready for *separate* integration review, never an activation step."""
        with self._db() as db:
            cur = db.execute("""UPDATE skill_candidates SET status='ready', updated_at=?
                WHERE owner_id=? AND id=? AND status='practiced' AND cost='free'
                AND requested_scopes='[]' AND license_verified=1 AND cost_verified=1""",
                (time.time(), owner_id, candidate_id))
            if cur.rowcount != 1:
                raise SkillGrowthError("仅零新增权限、免费且通过合成测试的候选可进入待接入状态")
        return self.get(owner_id, candidate_id)
