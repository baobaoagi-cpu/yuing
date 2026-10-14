"""Storage checks for owner scoping, legacy attribution, and offline snapshots."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from deploy_pwa.backup_chat_db import snapshot
from deploy_pwa.chat_store import ChatStore, StoreUnavailable


class ChatStoreIsolationTest(unittest.TestCase):
    def test_owner_cannot_read_or_mutate_another_owners_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "chat.sqlite3")
            nicole = ChatStore(path, "nicole")
            sid = nicole.create_session()
            nicole.add_event(sid, "user", "private hello")
            image_id = nicole.put_image(b"private-pixels", "image/png")
            memory_id = nicole.add_memory("private preference")
            nicole.add_call_transcript(sid, "ailivex-yuling-owner-test", "nicole",
                                       "segment-one", "user", "private audio")

            friend = ChatStore(path, "friend", legacy_owner_id="nicole")
            self.assertFalse(friend.has_session(sid))
            self.assertEqual(friend.history(sid), [])
            self.assertEqual(friend.recent_sessions(), [])
            self.assertIsNone(friend.get_image(image_id))
            self.assertEqual(friend.memories(), [])
            self.assertFalse(friend.delete_memory(memory_id))
            with self.assertRaises(StoreUnavailable):
                friend.add_event(sid, "user", "intrusion")
            with self.assertRaises(StoreUnavailable):
                friend.add_call_transcript(sid, "ailivex-yuling-owner-test", "friend",
                                           "segment-two", "user", "intrusion")
            with self.assertRaises(StoreUnavailable):
                friend.add_call_transcript(None, "ailivex-yuling-owner-test", "nicole",
                                           "segment-one", "user", "duplicate")
            self.assertEqual(len(nicole.history(sid)), 2)

    def test_existing_single_account_data_is_attributed_to_legacy_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "old.sqlite3")
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, created_at REAL NOT NULL, "
                           "updated_at REAL NOT NULL)")
                db.execute("INSERT INTO sessions VALUES ('old-session',1,1)")
                db.execute("CREATE TABLE images (id TEXT PRIMARY KEY, mime_type TEXT NOT NULL, "
                           "data BLOB NOT NULL, created_at REAL NOT NULL)")
                db.execute("INSERT INTO images VALUES ('old-image','image/png',X'616263',1)")
                db.execute("CREATE TABLE memories (id TEXT PRIMARY KEY, text TEXT NOT NULL, "
                           "source_kind TEXT NOT NULL, source_event_id TEXT, created_at REAL NOT NULL)")
                db.execute("INSERT INTO memories VALUES "
                           "('old-memory','old private memory','user_explicit',NULL,1)")
            friend = ChatStore(path, "friend", legacy_owner_id="nicole")
            self.assertFalse(friend.has_session("old-session"))
            self.assertIsNone(friend.get_image("old-image"))
            self.assertEqual(friend.memories(), [])
            nicole = ChatStore(path, "nicole", legacy_owner_id="nicole")
            self.assertTrue(nicole.has_session("old-session"))
            self.assertEqual(nicole.get_image("old-image"), ("image/png", b"abc"))
            self.assertEqual(nicole.memories()[0]["text"], "old private memory")
            with sqlite3.connect(path) as db:
                self.assertIsNotNone(db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='causal_claims'"
                ).fetchone())
            self.assertEqual(nicole.causal_claims(), [])

    def test_snapshot_is_consistent_private_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            source = str(Path(directory) / "source.sqlite3")
            backup = str(Path(directory) / "snapshot.sqlite3")
            owner = ChatStore(source, "nicole")
            sid = owner.create_session()
            owner.add_event(sid, "user", "preserved")
            source_event = owner.add_event(sid, "user", "https://example.com/",
                                           link_url="https://example.com/",
                                           source_kind="user_explicit")
            page_id, _ = owner.reserve_page_read(source_event["id"], "https://example.com/")
            owner.put_image(b"pixels", "image/png")
            owner.put_document("备忘.txt", "文本", "text/plain", b"note", "note", "text_preview")
            result = snapshot(source, backup)
            self.assertEqual(result["integrity_check"], "ok")
            self.assertEqual(result["counts"]["events"], 2)
            self.assertEqual(result["counts"]["images"], 1)
            self.assertEqual(result["counts"]["documents"], 1)
            self.assertEqual(result["counts"]["page_reads"], 1)
            self.assertEqual(os.stat(backup).st_mode & 0o777, 0o600)
            restored = ChatStore(backup, "nicole")
            self.assertEqual(restored.history(sid)[0]["text"], "preserved")
            self.assertEqual(restored.page_read(page_id)["source_event_id"], source_event["id"])
            with self.assertRaises(FileExistsError):
                snapshot(source, backup)
            self.assertEqual(restored.history(sid)[0]["text"], "preserved")


if __name__ == "__main__":
    unittest.main()
