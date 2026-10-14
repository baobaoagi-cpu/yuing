"""Two-stage owner-scoped memory reads keep full details out of discovery."""
import tempfile
import unittest
from pathlib import Path

from deploy_pwa.chat_store import ChatStore, StoreUnavailable


class ProgressiveMemoryTest(unittest.TestCase):
    def test_index_then_details_never_cross_accounts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "chat.sqlite3")
            nicole = ChatStore(path, "nicole")
            n_session = nicole.create_session()
            n_event = nicole.add_event(n_session, "user", "喜欢散步",
                                       source_kind="user_explicit")
            n_memory = nicole.add_memory("Nicole 喜欢散步，周末在公园。", n_event["id"])
            n_knowledge = nicole.add_knowledge_item(
                "散步喜好", "Nicole 周末在公园散步。", n_event["id"])

            waitin = ChatStore(path, "waitin", legacy_owner_id="nicole")
            w_session = waitin.create_session()
            w_event = waitin.add_event(w_session, "user", "喜欢咖啡",
                                       source_kind="user_explicit")
            w_memory = waitin.add_memory("威廷喜欢咖啡，早上喝。", w_event["id"])
            w_knowledge = waitin.add_knowledge_item(
                "咖啡喜好", "威廷早上喝咖啡。", w_event["id"])

            self.assertEqual([m["id"] for m in waitin.memory_index("咖啡")], [w_memory])
            self.assertEqual(waitin.memory_index("散步"), [])
            self.assertEqual([m["id"] for m in nicole.memory_index("散步")], [n_memory])
            self.assertNotIn("text", waitin.memory_index()[0])
            self.assertEqual(waitin.memory_details([n_memory, w_memory])[0]["id"], w_memory)
            self.assertEqual(nicole.memory_details([w_memory]), [])
            self.assertEqual(waitin.knowledge_index("散步"), [])
            self.assertEqual([k["id"] for k in waitin.knowledge_index("咖啡")],
                             [w_knowledge["id"]])
            self.assertNotIn("content", waitin.knowledge_index()[0])
            self.assertEqual(waitin.knowledge_details([n_knowledge["id"]]), [])
            self.assertEqual(nicole.knowledge_details([w_knowledge["id"]]), [])
            self.assertEqual(waitin.knowledge_details([w_knowledge["id"]])[0]["content"],
                             "威廷早上喝咖啡。")
            with self.assertRaises(StoreUnavailable):
                waitin.add_memory("别人的资料", n_event["id"])
            transcript = waitin.add_event(w_session, "user", "可能听错的语音",
                                          source_kind="livekit_browser_transcript")
            with self.assertRaises(StoreUnavailable):
                waitin.add_memory("未经确认的语音", transcript["id"])

    def test_query_wildcards_are_literal_and_details_keep_requested_order(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ChatStore(str(Path(directory) / "chat.sqlite3"), "owner")
            first = store.add_memory("5% 专题")
            second = store.add_memory("50 专题")
            third = store.add_memory("带_下划线")
            self.assertEqual([x["id"] for x in store.memory_index("5%")], [first])
            self.assertEqual([x["id"] for x in store.memory_index("_")], [third])
            self.assertEqual([x["id"] for x in store.memory_details([first, second, first])],
                             [first, second])
            self.assertEqual(store.memory_details([]), [])
            with self.assertRaises(ValueError):
                store.memory_details([None])


if __name__ == "__main__":
    unittest.main()
