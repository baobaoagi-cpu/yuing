"""Create a consistent, private SQLite snapshot for an operator-controlled migration.

This command only reads the source. It does not reach Railway, send data, or
change the application's configured database path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from uuid import uuid4


TABLES = (
    "sessions", "events", "call_transcripts", "images", "documents", "memories",
    "knowledge_items", "relation_candidates", "page_reads",
)


def snapshot(source_path: str, destination_path: str) -> dict:
    source = Path(source_path).expanduser().resolve(strict=True)
    destination = Path(destination_path).expanduser().absolute()
    if not source.is_file() or not destination.parent.is_dir():
        raise ValueError("source file and destination directory must exist")
    if destination.exists() or destination.is_symlink() or source == destination:
        raise FileExistsError("destination must be a new file")

    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=5) as src:
            with sqlite3.connect(str(temporary), timeout=5) as dst:
                src.backup(dst)
                result = dst.execute("PRAGMA integrity_check").fetchone()
                if result != ("ok",):
                    raise sqlite3.DatabaseError("backup integrity check failed")
                names = {row[0] for row in dst.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                if not set(TABLES).issubset(names):
                    raise sqlite3.DatabaseError("backup schema is incomplete")
                counts = {name: dst.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                          for name in TABLES}
        digest_state = hashlib.sha256()
        with temporary.open("rb") as snapshot_file:
            for block in iter(lambda: snapshot_file.read(1024 * 1024), b""):
                digest_state.update(block)
        digest = digest_state.hexdigest()
        # A hard link publishes the completed snapshot without replacing an
        # existing destination, including one created during the backup.
        os.link(temporary, destination)
        return {"backup": str(destination), "sha256": digest,
                "bytes": destination.stat().st_size, "counts": counts,
                "integrity_check": "ok"}
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="existing SQLite chat database")
    parser.add_argument("destination", help="new, private snapshot file")
    args = parser.parse_args()
    print(json.dumps(snapshot(args.source, args.destination), ensure_ascii=False))


if __name__ == "__main__":
    main()
