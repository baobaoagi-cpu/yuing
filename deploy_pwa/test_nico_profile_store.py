"""Owner and provenance boundaries for Nicole's opt-in profile candidates."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deploy_pwa.chat_store import ChatStore
from deploy_pwa.nico_profile_store import NicoProfileStore, ProfileSourceError


class NicoProfileStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.chat_path = str(root / "chat.sqlite3")
        self.store_root = str(root / "profile")
        self.nicole_chat = ChatStore(self.chat_path, "nicole")
        self.other_chat = ChatStore(self.chat_path, "other", legacy_owner_id="nicole")
        self.nicole = NicoProfileStore(self.chat_path, "nicole", self.store_root)
        self.other = NicoProfileStore(self.chat_path, "other", self.store_root)
        self.nicole_session = self.nicole_chat.create_session()
        self.other_session = self.other_chat.create_session()

    def tearDown(self):
        self.temp.cleanup()

    def event(self, text, owner="nicole", role="user", source_kind="user_explicit"):
        store = self.nicole_chat if owner == "nicole" else self.other_chat
        sid = self.nicole_session if owner == "nicole" else self.other_session
        return store.add_event(sid, role, text, source_kind=source_kind)["id"]

    def test_three_files_name_seed_and_explicit_correction(self):
        self.assertEqual(self.nicole.identity()["display_name"], "Nicole")
        self.assertIsNone(self.other.identity())
        for basename in ("memory.sqlite3", "graph.sqlite3", "knowledge.sqlite3"):
            self.assertTrue((Path(self.store_root) / basename).exists())
        source = self.event("以后请叫我 Niki")
        self.assertEqual(self.nicole.correct_display_name("Niki", source)["display_name"], "Niki")
        self.assertEqual(NicoProfileStore(self.chat_path, "nicole", self.store_root).identity()["display_name"], "Niki")
        with self.assertRaises(ProfileSourceError):
            self.other.correct_display_name("Niki", source)

    def test_three_account_names_are_scoped_and_correction_stays_with_owner(self):
        ming = NicoProfileStore(self.chat_path, "MING", self.store_root)
        waitin = NicoProfileStore(self.chat_path, "WAITIN", self.store_root)
        self.assertEqual(ming.identity()["display_name"], "王跃明")
        self.assertEqual(waitin.identity()["display_name"], "威廷")
        self.assertEqual(self.nicole.identity()["display_name"], "Nicole")
        source = self.event("以后叫我 Niki")
        self.nicole.correct_display_name("Niki", source)
        self.assertEqual(self.nicole.identity()["display_name"], "Niki")
        self.assertEqual(ming.identity()["display_name"], "王跃明")
        self.assertEqual(waitin.identity()["display_name"], "威廷")

    def test_server_config_name_applies_without_cross_account_override(self):
        with patch.dict("os.environ", {"POC_ACCOUNT_DISPLAY_NAMES_JSON":
                               '{"WAITIN":"威廷测试称呼","new-user":"新用户"}'}):
            waitin = NicoProfileStore(self.chat_path, "WAITIN", self.store_root)
            newcomer = NicoProfileStore(self.chat_path, "new-user", self.store_root)
            self.assertEqual(waitin.identity()["display_name"], "威廷测试称呼")
            self.assertEqual(newcomer.identity()["display_name"], "新用户")
            self.assertEqual(self.nicole.identity()["display_name"], "Nicole")
            source = self.event("以后叫我 Niki")
            self.nicole.correct_display_name("Niki", source)
            self.assertEqual(self.nicole.identity()["display_name"], "Niki")
        self.assertEqual(waitin.identity()["display_name"], "威廷")

    def test_consent_default_off_source_bound_and_account_isolated(self):
        source = self.event("我喜欢散步")
        self.assertFalse(self.nicole.consent_status())
        with self.assertRaises(PermissionError):
            self.nicole.propose_from_user_event(source)
        self.nicole.set_consent(True)
        fact = self.nicole.propose_from_user_event(source)
        self.assertEqual(fact["status"], "candidate")
        self.assertEqual(fact["evidence_quote"], "我喜欢散步")
        self.assertEqual(len(self.nicole.facts()), 1)
        self.assertEqual(self.nicole.propose_from_user_event(source)["id"], fact["id"])
        self.assertEqual(self.other.facts(), [])
        self.assertIsNone(self.other.get_fact(fact["id"]))
        self.assertFalse(self.other.delete_fact(fact["id"]))
        self.other.set_consent(True)
        with self.assertRaises(ProfileSourceError):
            self.other.propose_from_user_event(source)
        with self.assertRaises(ProfileSourceError):
            self.nicole.add_fact("trait", "是老板", source, "我喜欢散步")
        assistant = self.event("我喜欢散步", role="assistant")
        with self.assertRaises(ProfileSourceError):
            self.nicole.propose_from_user_event(assistant)
        third_party = self.event("我朋友喜欢散步")
        self.assertIsNone(self.nicole.propose_from_user_event(third_party))
        self.nicole.set_consent(False)
        with self.assertRaises(PermissionError):
            self.nicole.add_fact("preference", "喜欢散步", source, "我喜欢散步")

    def test_graph_and_knowledge_are_candidates_and_deletable(self):
        self.nicole.set_consent(True)
        a_source = self.event("我喜欢散步")
        b_source = self.event("我习惯早睡")
        a = self.nicole.propose_from_user_event(a_source)
        b = self.nicole.propose_from_user_event(b_source)
        edge = self.nicole.add_edge(a["id"], "可能相关", b["id"], b_source, "我习惯早睡")
        self.assertEqual(edge["status"], "candidate")
        self.assertEqual(self.other.edges(), [])
        self.other.set_consent(True)
        with self.assertRaises(ProfileSourceError):
            self.other.add_edge(a["id"], "可能相关", b["id"], b_source, "我习惯早睡")
        knowledge = self.nicole.add_knowledge("偏好", "喜欢散步", a_source, "我喜欢散步")
        self.assertEqual(knowledge["status"], "candidate")
        self.assertEqual(self.other.knowledge(), [])
        self.assertFalse(self.other.delete_knowledge(knowledge["id"]))
        self.assertTrue(self.nicole.delete_knowledge(knowledge["id"]))
        self.assertTrue(self.nicole.delete_fact(a["id"]))
        self.assertEqual(self.nicole.edges(), [])
        self.assertIsNone(self.nicole.get_edge(edge["id"]))

    def test_correct_fact_requires_new_user_evidence(self):
        self.nicole.set_consent(True)
        source = self.event("我喜欢散步")
        fact = self.nicole.propose_from_user_event(source)
        correction = self.event("我不喜欢散步了")
        updated = self.nicole.correct_fact(fact["id"], "现在不喜欢散步", correction, "我不喜欢散步了")
        self.assertEqual(updated["source_event_id"], correction)
        self.assertEqual(updated["status"], "candidate")
        with self.assertRaises(ProfileSourceError):
            self.nicole.correct_fact(fact["id"], "改成喜欢开车", source, "我喜欢开车")


if __name__ == "__main__":
    unittest.main()
