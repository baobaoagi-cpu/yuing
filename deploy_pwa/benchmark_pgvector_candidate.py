"""Synthetic pgvector trial in an explicitly isolated, disposable PostgreSQL DB.

Install only in the trial worker: ``pip install 'psycopg[binary]' fastembed``.
Run with DATABASE_URL set privately and --ack-isolated-synthetic-db. This script
creates an extension and a random schema, then removes its schema on success.
It does not print the connection string or access application tables.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import time
import uuid

from benchmark_vector_candidate import CORPUS, QUERIES, DIM, vector


def _embeddings(semantic: bool, cache_dir: str | None):
    if semantic:
        from fastembed import TextEmbedding

        model = TextEmbedding(model_name="BAAI/bge-small-zh-v1.5", cache_dir=cache_dir)
        return 512, "BAAI/bge-small-zh-v1.5", lambda text: next(model.embed([text])).tolist()

    import struct

    return DIM, "deterministic_character_hash_not_semantic", lambda text: list(struct.unpack(f"{DIM}f", vector(text)))


def run(*, acknowledged: bool, semantic: bool = False, cache_dir: str | None = None):
    if not acknowledged:
        raise ValueError("Requires --ack-isolated-synthetic-db; never run against production")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL must point to an isolated, disposable trial database")

    import psycopg
    from psycopg import sql

    dimension, model_name, embed = _embeddings(semantic, cache_dir)
    schema = "synthetic_pgvector_" + uuid.uuid4().hex
    table = sql.Identifier(schema, "candidate")
    restore = sql.Identifier(schema, "restore_probe")
    schema_id = sql.Identifier(schema)
    with psycopg.connect(database_url, autocommit=True) as db:
        # A fresh private trial DB only. Do not grant this role to the voice agent.
        db.execute("CREATE EXTENSION IF NOT EXISTS vector")
        version = db.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()[0]
        db.execute(sql.SQL("CREATE SCHEMA {}").format(schema_id))
        try:
            db.execute(sql.SQL("CREATE TABLE {} (id integer PRIMARY KEY, owner_id integer NOT NULL, "
                               "source_event_id text NOT NULL, status text NOT NULL, "
                               "content text NOT NULL, embedding vector({}))").format(table, sql.Literal(dimension)))
            db.execute(sql.SQL("CREATE INDEX ON {} (owner_id, status)").format(table))
            db.execute(sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(table))
            db.execute(sql.SQL("ALTER TABLE {} FORCE ROW LEVEL SECURITY").format(table))
            db.execute(sql.SQL("CREATE POLICY owner_read ON {} FOR SELECT USING "
                               "(owner_id = nullif(current_setting('app.owner_id', true), '')::integer)").format(table))
            # Insert under owner context. The trial role owns the table; FORCE RLS
            # applies to its SELECT, while INSERT has no policy and is denied.
            # Temporarily disable RLS solely for synthetic seeding before checks.
            db.execute(sql.SQL("ALTER TABLE {} DISABLE ROW LEVEL SECURITY").format(table))
            for row_id, (owner, content) in enumerate(CORPUS, 1):
                db.execute(sql.SQL("INSERT INTO {} (id,owner_id,source_event_id,status,content,embedding) "
                                   "VALUES (%s,%s,%s,%s,%s,%s::vector)").format(table),
                           (row_id, owner, f"synthetic-event-{row_id}", "user_note_unverified",
                            content, json.dumps(embed(content))))
            db.execute(sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(table))

            def retrieve(owner: int | None, query: str, limit: int = 2):
                with db.transaction():
                    if owner is not None:
                        db.execute("SELECT set_config('app.owner_id', %s, true)", (str(owner),))
                    return db.execute(sql.SQL("SELECT id,owner_id FROM {} "
                        "WHERE owner_id=%s AND status='user_note_unverified' "
                        "ORDER BY embedding <=> %s::vector LIMIT %s").format(table),
                        (owner if owner is not None else 1, json.dumps(embed(query)), limit)).fetchall()

            latencies = []
            top1 = top2 = 0
            for query, expected in QUERIES:
                started = time.perf_counter()
                rows = retrieve(1, query)
                latencies.append((time.perf_counter() - started) * 1000)
                top1 += bool(rows and rows[0][0] in expected)
                top2 += bool({row[0] for row in rows} & expected)
            owner2 = retrieve(2, "端倪喜欢散步", 8)
            assert owner2 and all(row[1] == 2 for row in owner2)
            assert retrieve(None, "端倪喜欢散步") == []

            # Simulates an index/table rebuild from synthetic authoritative rows.
            # This is not a PostgreSQL volume backup or cross-deployment restore.
            db.execute(sql.SQL("CREATE TABLE {} (LIKE {} INCLUDING ALL)").format(restore, table))
            for owner in (1, 2):
                with db.transaction():
                    db.execute("SELECT set_config('app.owner_id', %s, true)", (str(owner),))
                    db.execute(sql.SQL("INSERT INTO {} SELECT * FROM {} WHERE owner_id=%s").format(restore, table), (owner,))
            restored_count = db.execute(sql.SQL("SELECT count(*) FROM {}").format(restore)).fetchone()[0]
            assert restored_count == len(CORPUS)
            return {"extension": "pgvector", "version": version, "vectorizer": model_name,
                    "synthetic_queries": len(QUERIES), "top1_hit_rate": top1 / len(QUERIES),
                    "top2_hit_rate": top2 / len(QUERIES),
                    "median_latency_ms": statistics.median(latencies),
                    "cross_account_rows": sum(row[1] != 2 for row in owner2),
                    "rls_denies_missing_owner": True, "rebuild_readback_rows": restored_count,
                    "corpus_size": len(CORPUS)}
        finally:
            db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(schema_id))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ack-isolated-synthetic-db", action="store_true")
    parser.add_argument("--semantic", action="store_true")
    parser.add_argument("--cache-dir")
    args = parser.parse_args()
    print(json.dumps(run(acknowledged=args.ack_isolated_synthetic_db,
                         semantic=args.semantic, cache_dir=args.cache_dir),
                     ensure_ascii=False, sort_keys=True))
