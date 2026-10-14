"""Reproducible synthetic sqlite-vec storage probe; no production data or model.

The deterministic character vector is intentionally NOT a semantic embedding.
Its recall numbers assess this exact prototype only, never pgvector or a future
Chinese embedding model. Run with a Python environment containing sqlite-vec.
"""
from __future__ import annotations

import hashlib
import argparse
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
    (1, "記憶分成短期會話與使用者明示的長期保存"),
    (1, "使用者可撤回知識筆記與關係候選"),
    (1, "因果主張必須記錄反例與時間順序"),
    (1, "語音代理不可直接取得資料庫密碼"),
    (1, "圖片檔案有雜湊與擁有者欄位"),
    (1, "夜間跑步是另一個人的運動習慣"),
    (1, "檢索索引可從原始資料重建"),
    (1, "文件文字預覽不代表讀過掃描頁"),
    (2, "隔離帳號：端倪喜歡散步"),
    (2, "隔離帳號：甲影響乙"),
]
QUERIES = [
    ("端倪喜欢散步", {1, 2}),
    ("知識來源事件", {3}),
    ("因果證據", {4}),
    ("語音短效上下文", {5}),
    ("圖片附件", {6}),
    ("哪些記憶是本人主動保存的？", {7}),
    ("怎麼撤銷關係筆記？", {8}),
    ("因果論證要看反證嗎？", {9}),
    ("語音服務能直接登入資料庫嗎？", {10}),
    ("掃描頁真的已經讀到了嗎？", {14}),
]


def vector(text):
    values = [0.0] * DIM
    chars = [char for char in text if not char.isspace()]
    for token in chars + ["".join(pair) for pair in zip(chars, chars[1:])]:
        index = int.from_bytes(hashlib.sha256(token.encode()).digest()[:4], "big") % DIM
        values[index] += 1.0
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return struct.pack(f"{DIM}f", *(value / norm for value in values))


def run(semantic=False, cache_dir=None):
    dimension = DIM
    embed = vector
    model_name = "deterministic_character_hash_not_semantic"
    if semantic:
        from fastembed import TextEmbedding
        model = TextEmbedding(model_name="BAAI/bge-small-zh-v1.5", cache_dir=cache_dir)
        model_name = "BAAI/bge-small-zh-v1.5"
        dimension = 512
        def embed(text):
            return next(model.embed([text])).astype("float32").tobytes()
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "candidate.sqlite3"
        db = sqlite3.connect(path)
        db.enable_load_extension(True)
        sqlite_vec.load(db)
        db.enable_load_extension(False)
        db.execute(f"CREATE VIRTUAL TABLE candidate USING vec0(owner_id INTEGER partition key, embedding float[{dimension}])")
        for rowid, (owner, text) in enumerate(CORPUS, 1):
            db.execute("INSERT INTO candidate(rowid,owner_id,embedding) VALUES(?,?,?)",
                       (rowid, owner, embed(text)))
        db.commit()
        times = []
        hits = 0
        top1_hits = 0
        for query, expected in QUERIES:
            started = time.perf_counter()
            rows = db.execute(
                "SELECT rowid FROM candidate WHERE embedding MATCH ? AND owner_id=? AND k=2",
                (embed(query), 1)).fetchall()
            times.append((time.perf_counter() - started) * 1000)
            hits += bool({row[0] for row in rows} & expected)
            top1_hits += bool(rows and rows[0][0] in expected)
        wrong_owner = db.execute(
            "SELECT rowid FROM candidate WHERE embedding MATCH ? AND owner_id=? AND k=8",
            (embed("端倪喜欢散步"), 2)).fetchall()
        assert all(CORPUS[row[0] - 1][0] == 2 for row in wrong_owner)
        db.close()
        copied = Path(directory) / "restored.sqlite3"
        copied.write_bytes(path.read_bytes())
        restored = sqlite3.connect(copied)
        restored.enable_load_extension(True)
        sqlite_vec.load(restored)
        restored.enable_load_extension(False)
        restored_rows = restored.execute(
            "SELECT rowid FROM candidate WHERE embedding MATCH ? AND owner_id=? AND k=2",
            (embed("端倪喜欢散步"), 1)).fetchall()
        restored.close()
        return {"extension": "sqlite-vec", "version": sqlite_vec.__version__,
                "vectorizer": model_name,
                "synthetic_queries": len(QUERIES), "top2_hit_rate": hits / len(QUERIES),
                "top1_hit_rate": top1_hits / len(QUERIES),
                "median_latency_ms": sorted(times)[len(times) // 2],
                "cross_account_rows": sum(CORPUS[row[0] - 1][0] != 2 for row in wrong_owner),
                "restore_readback": bool(restored_rows), "corpus_size": len(CORPUS)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--semantic", action="store_true")
    parser.add_argument("--cache-dir")
    args = parser.parse_args()
    print(json.dumps(run(args.semantic, args.cache_dir), ensure_ascii=False, sort_keys=True))
