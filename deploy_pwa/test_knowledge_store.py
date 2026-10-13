"""Knowledge entries preserve provenance and stay within their account."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from deploy_pwa.chat_store import ChatStore, StoreUnavailable


class KnowledgeStoreTest(unittest.TestCase):
    def test_source_owner_and_delete_are_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "chat.sqlite3")
            owner = ChatStore(path, "owner")
            session_id = owner.create_session()
            source = owner.add_event(session_id, "user", "我喜欢散步",
                                     source_kind="user_explicit")
            assistant = owner.add_event(session_id, "assistant", "你喜欢散步",
                                        source_kind="model_generated")
            item = owner.add_knowledge_item("喜好", "喜欢散步", source["id"])
            self.assertEqual(owner.knowledge_items(), [item])

            other = ChatStore(path, "friend", legacy_owner_id="owner")
            self.assertEqual(other.knowledge_items(), [])
            with self.assertRaises(StoreUnavailable):
                other.add_knowledge_item("偷看", "内容", source["id"])
            with self.assertRaises(StoreUnavailable):
                owner.add_knowledge_item("推测", "内容", assistant["id"])
            self.assertFalse(other.delete_knowledge_item(item["id"]))
            self.assertEqual(owner.knowledge_items(), [item])
            self.assertTrue(owner.delete_knowledge_item(item["id"]))
            self.assertEqual(owner.knowledge_items(), [])

    def test_explicit_item_requires_explicit_user_event(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "chat.sqlite3")
            store = ChatStore(path, "owner")
            sid = store.create_session()
            call = store.add_event(sid, "user", "可能喜欢散步",
                                   source_kind="livekit_browser_transcript")
            with self.assertRaises(StoreUnavailable):
                store.add_knowledge_item("喜好", "喜欢散步", call["id"])
            candidate = store.add_knowledge_item("候选", "可能喜欢散步", call["id"],
                                                 source_kind="webpage_candidate",
                                                 source_url="https://example.org/note")
            self.assertEqual(candidate["source_kind"], "webpage_candidate")
            self.assertEqual(candidate["source_event_id"], call["id"])
            with self.assertRaises(ValueError):
                store.add_knowledge_item("", "内容", call["id"])

    def test_legacy_knowledge_table_gets_source_column_and_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "old.sqlite3")
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE knowledge_items (id TEXT PRIMARY KEY, title TEXT NOT NULL, "
                           "content TEXT NOT NULL, source_url TEXT, source_kind TEXT NOT NULL, "
                           "created_at REAL NOT NULL)")
                db.execute("INSERT INTO knowledge_items VALUES "
                           "('legacy','old title','old content',NULL,'user_explicit',1)")
            other = ChatStore(path, "friend", legacy_owner_id="owner")
            self.assertEqual(other.knowledge_items(), [])
            owner = ChatStore(path, "owner", legacy_owner_id="owner")
            legacy = owner.knowledge_items()[0]
            self.assertEqual(legacy["id"], "legacy")
            self.assertIsNone(legacy["source_event_id"])


if __name__ == "__main__":
    unittest.main()
