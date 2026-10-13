"""Relation candidates remain source-linked, tentative, and account isolated."""
import tempfile
import unittest
from pathlib import Path

from deploy_pwa.chat_store import ChatStore, StoreUnavailable


class RelationCandidateTest(unittest.TestCase):
    def test_relation_requires_same_owner_items_and_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "chat.sqlite3")
            owner = ChatStore(path, "owner")
            sid = owner.create_session()
            event = owner.add_event(sid, "user", "甲与乙可能有关", source_kind="user_explicit")
            assistant = owner.add_event(sid, "assistant", "我推测有关")
            first = owner.add_knowledge_item("甲", "甲", event["id"])
            second = owner.add_knowledge_item("乙", "乙", event["id"])

            relation = owner.add_relation_candidate(first["id"], "  可能关联  ",
                                                    second["id"], event["id"])
            self.assertEqual(relation["status"], "candidate")
            self.assertEqual(relation["predicate"], "可能关联")
            self.assertEqual(owner.relation_candidates(), [relation])

            friend = ChatStore(path, "friend", legacy_owner_id="owner")
            friend_sid = friend.create_session()
            friend_event = friend.add_event(friend_sid, "user", "朋友的资料",
                                            source_kind="user_explicit")
            friend_item = friend.add_knowledge_item("丙", "丙", friend_event["id"])
            self.assertEqual(friend.relation_candidates(), [])
            for store, subject, other, source in (
                (friend, first["id"], friend_item["id"], friend_event["id"]),
                (owner, first["id"], friend_item["id"], event["id"]),
                (owner, first["id"], second["id"], friend_event["id"]),
                (owner, first["id"], second["id"], assistant["id"]),
            ):
                with self.assertRaises(StoreUnavailable):
                    store.add_relation_candidate(subject, "相关", other, source)
            with self.assertRaises(ValueError):
                owner.add_relation_candidate(first["id"], " ", second["id"], event["id"])
            self.assertFalse(friend.delete_relation_candidate(relation["id"]))
            self.assertEqual(owner.relation_candidates(), [relation])
            self.assertTrue(owner.delete_relation_candidate(relation["id"]))
            self.assertEqual(owner.relation_candidates(), [])

    def test_deleting_item_removes_its_relation_candidates_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "chat.sqlite3")
            owner = ChatStore(path, "owner")
            sid = owner.create_session()
            event = owner.add_event(sid, "user", "甲乙丙", source_kind="user_explicit")
            items = [owner.add_knowledge_item(text, text, event["id"])
                     for text in ("甲", "乙", "丙")]
            owner.add_relation_candidate(items[0]["id"], "关联", items[1]["id"], event["id"])
            untouched = owner.add_relation_candidate(items[1]["id"], "关联",
                                                     items[2]["id"], event["id"])
            self.assertTrue(owner.delete_knowledge_item(items[0]["id"]))
            self.assertEqual(owner.relation_candidates(), [untouched])


if __name__ == "__main__":
    unittest.main()
