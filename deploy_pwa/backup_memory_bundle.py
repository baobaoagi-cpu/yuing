"""Offline, quiesced four-database snapshot for migration rehearsal.

The four SQLite files have no cross-file transaction. Stop application writes
before taking an operational snapshot; this utility cannot make live writes
atomically consistent across databases. It never modifies source files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4


FILES = {
    "chat.sqlite3": {"sessions", "events", "memories", "knowledge_items",
                     "relation_candidates"},
    "memory.sqlite3": {"profile_identity", "profile_consent", "profile_facts"},
    "knowledge.sqlite3": {"profile_knowledge"},
    "graph.sqlite3": {"profile_edges"},
}


def _hash_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _inspect(path, required):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
        if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise sqlite3.DatabaseError("snapshot integrity check failed")
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if not required.issubset(tables):
            raise sqlite3.DatabaseError("snapshot schema is incomplete")
        counted = required | ({"causal_claims"} if "causal_claims" in tables else set())
        return {name: db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                for name in sorted(counted)}


def snapshot_bundle(chat_path, companion_root, destination):
    chat = Path(chat_path).expanduser().resolve(strict=True)
    companion = Path(companion_root).expanduser().resolve(strict=True)
    target = Path(destination).expanduser().absolute()
    if not chat.is_file() or not companion.is_dir():
        raise ValueError("four source databases are required")
    if target.exists() or target.is_symlink() or not target.parent.is_dir():
        raise FileExistsError("destination must be a new directory")
    if target.is_relative_to(chat.parent) or target.is_relative_to(companion):
        raise ValueError("destination must be outside source directories")
    sources = {"chat.sqlite3": chat}
    sources.update({name: companion / name for name in FILES if name != "chat.sqlite3"})
    for name, source in sources.items():
        if not source.is_file() or source.is_symlink():
            raise ValueError("invalid source or destination for " + name)
    temporary = target.with_name("." + target.name + "." + uuid4().hex + ".tmp")
    temporary.mkdir(mode=0o700)
    try:
        manifest = {"format": 1, "consistency": "requires_quiesced_writes", "files": {}}
        for name, source in sources.items():
            copied = temporary / name
            fd = os.open(copied, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=5)) as src:
                with closing(sqlite3.connect(copied, timeout=5)) as dst:
                    src.backup(dst)
            manifest["files"][name] = {
                "sha256": _hash_file(copied), "bytes": copied.stat().st_size,
                "counts": _inspect(copied, FILES[name]),
            }
        marker = temporary / "manifest.json"
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(manifest, output, ensure_ascii=False, sort_keys=True)
        os.rename(temporary, target)
        return manifest
    finally:
        if temporary.exists():
            for child in temporary.iterdir():
                child.unlink()
            temporary.rmdir()


def verify_bundle(destination):
    target = Path(destination).expanduser().resolve(strict=True)
    with (target / "manifest.json").open(encoding="utf-8") as source:
        manifest = json.load(source)
    if manifest.get("format") != 1 or set(manifest.get("files", {})) != set(FILES):
        raise ValueError("invalid snapshot manifest")
    for name, required in FILES.items():
        path = target / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("snapshot file missing or redirected")
        record = manifest["files"][name]
        if (_hash_file(path) != record["sha256"] or
                path.stat().st_size != record["bytes"] or
                _inspect(path, required) != record["counts"]):
            raise ValueError("snapshot verification failed for " + name)
    return {"verified": True, "file_count": len(FILES),
            "sha256": {name: manifest["files"][name]["sha256"] for name in FILES}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("chat_db")
    parser.add_argument("companion_root")
    parser.add_argument("destination")
    args = parser.parse_args()
    snapshot_bundle(args.chat_db, args.companion_root, args.destination)
    print(json.dumps(verify_bundle(args.destination), ensure_ascii=False))


if __name__ == "__main__":
    main()
