"""Reproducible synthetic sqlite-vec storage probe; no production data or model.

The deterministic character vector is intentionally NOT a semantic embedding.
Its recall numbers assess this exact prototype only, never pgvector or a future
Chinese embedding model. Run with a Python environment containing sqlite-vec.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import struct
import tempfile
import time
from pathlib import Path

import sqlite_vec


DIM = 128
CORPUS = [
    (1, "繁體：端倪喜歡散步"),
    (1, "简体：端倪喜欢散步"),
    (1, "知識庫保存來源事件"),
    (1, "甲可能影響乙，但尚無因果證據"),
    (1, "語音通話需要短效簽章上下文"),
    (1, "附件圖片只屬於上傳帳號"),
    (2, "隔離帳號：端倪喜歡散步"),
    (2, "隔離帳號：甲影響乙"),
]
QUERIES = [
    ("端倪喜欢散步", {1, 2}),
    ("知識來源事件", {3}),
    ("因果證據", {4}),
    ("語音短效上下文", {5}),
    ("圖片附件", {6}),
]


def vector(text):
    values = [0.0] * DIM
    chars = [char for char in text if not char.isspace()]
    for token in chars + ["".join(pair) for pair in zip(chars, chars[1:])]:
        index = int.from_bytes(hashlib.sha256(token.encode()).digest()[:4], "big") % DIM
        values[index] += 1.0
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return struct.pack(f"{DIM}f", *(value / norm for value in values))


def run():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "candidate.sqlite3"
        db = sqlite3.connect(path)
        db.enable_load_extension(True)
        sqlite_vec.load(db)
        db.enable_load_extension(False)
        db.execute(f"CREATE VIRTUAL TABLE candidate USING vec0(owner_id INTEGER partition key, embedding float[{DIM}])")
        for rowid, (owner, text) in enumerate(CORPUS, 1):
            db.execute("INSERT INTO candidate(rowid,owner_id,embedding) VALUES(?,?,?)",
                       (rowid, owner, vector(text)))
        db.commit()
        times = []
        hits = 0
        for query, expected in QUERIES:
            started = time.perf_counter()
            rows = db.execute(
                "SELECT rowid FROM candidate WHERE embedding MATCH ? AND owner_id=? AND k=2",
                (vector(query), 1)).fetchall()
            times.append((time.perf_counter() - started) * 1000)
            hits += bool({row[0] for row in rows} & expected)
        wrong_owner = db.execute(
            "SELECT rowid FROM candidate WHERE embedding MATCH ? AND owner_id=? AND k=8",
            (vector("端倪喜欢散步"), 2)).fetchall()
        assert all(row[0] > 6 for row in wrong_owner)
        db.close()
        copied = Path(directory) / "restored.sqlite3"
        copied.write_bytes(path.read_bytes())
        restored = sqlite3.connect(copied)
        restored.enable_load_extension(True)
        sqlite_vec.load(restored)
        restored.enable_load_extension(False)
        restored_rows = restored.execute(
            "SELECT rowid FROM candidate WHERE embedding MATCH ? AND owner_id=? AND k=2",
            (vector("端倪喜欢散步"), 1)).fetchall()
        restored.close()
        return {"extension": "sqlite-vec", "version": sqlite_vec.__version__,
                "vectorizer": "deterministic_character_hash_not_semantic",
                "synthetic_queries": len(QUERIES), "top2_hit_rate": hits / len(QUERIES),
                "median_latency_ms": sorted(times)[len(times) // 2],
                "cross_account_rows": sum(row[0] <= 6 for row in wrong_owner),
                "restore_readback": bool(restored_rows), "corpus_size": len(CORPUS)}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, sort_keys=True))
