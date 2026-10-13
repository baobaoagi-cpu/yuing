"""The growth registry records proposals without installing or running tools."""
import tempfile
import unittest
from pathlib import Path

from deploy_pwa.skill_growth import SkillGrowthError, SkillGrowthStore, explicit_skill_need


class SkillGrowthTest(unittest.TestCase):
    def test_explicit_skill_need_is_conservative(self):
        self.assertEqual(explicit_skill_need("我希望你能帮我查天气。"), "帮我查天气")
        self.assertEqual(explicit_skill_need("我想讓你學會整理我的檔案"), "整理我的檔案")
        self.assertEqual(explicit_skill_need("你可以学会读 PDF 吗？"), "读 PDF")
        for text in ("她想学会整理文件", "如果我希望你能整理文件", "我不希望你能整理文件",
                     "你可以学会读 PDF", "我喜欢你整理文件", "我希望你能" + "甲" * 130):
            self.assertIsNone(explicit_skill_need(text))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SkillGrowthStore(str(Path(self.tmp.name) / "skills.sqlite3"))

    def tearDown(self):
        self.tmp.cleanup()

    def propose(self, **changes):
        data = dict(owner_id="nicole", source_event_id="user-event-1",
                    user_need="想知道明天下雨吗", name="天气查询",
                    kind="skill", source_url="https://example.org/weather",
                    license_id="MIT", requested_scopes=[], cost="free")
        data.update(changes)
        return self.store.propose(**data)

    def test_manual_review_then_synthetic_practice_then_ready(self):
        item = self.propose()
        self.assertEqual(item["status"], "candidate")
        self.assertEqual(item["source_event_id"], "user-event-1")
        self.assertEqual(self.store.list("other"), [])
        with self.assertRaises(SkillGrowthError):
            self.store.review("other", item["id"], actor="reviewer",
                              note="license checked", approve=True,
                              license_verified=True, cost_verified=True)
        with self.assertRaises(SkillGrowthError):
            self.store.mark_ready("nicole", item["id"])
        reviewed = self.store.review("nicole", item["id"], actor="reviewer",
                                     note="MIT license, no scope expansion", approve=True,
                                     license_verified=True, cost_verified=True)
        self.assertEqual(reviewed["status"], "reviewed")
        practiced = self.store.record_synthetic_practice(
            "nicole", item["id"], case="synthetic rain question",
            evidence="mock forecast answer matched fixture", passed=True)
        self.assertEqual(practiced["status"], "practiced")
        ready = self.store.mark_ready("nicole", item["id"])
        self.assertEqual(ready["status"], "ready")
        self.assertIsNone(self.store.get("other", item["id"]))
        with self.assertRaises(SkillGrowthError):
            self.store.mark_ready("nicole", item["id"])

    def test_paid_or_permissioned_candidate_cannot_be_ready(self):
        for changes in ({"cost": "paid"}, {"cost": "unknown"},
                        {"requested_scopes": ["calendar.write"]}):
            item = self.propose(**changes)
            self.store.review("nicole", item["id"], actor="reviewer",
                              note="candidate only", approve=True,
                              license_verified=True, cost_verified=True)
            self.store.record_synthetic_practice(
                "nicole", item["id"], case="mock input", evidence="mock output", passed=True)
            with self.assertRaises(SkillGrowthError):
                self.store.mark_ready("nicole", item["id"])
            self.assertEqual(self.store.get("nicole", item["id"])["status"], "practiced")

    def test_rejection_and_invalid_source(self):
        with self.assertRaises(SkillGrowthError):
            self.propose(source_url="http://example.org/file")
        with self.assertRaises(SkillGrowthError):
            self.propose(source_url="https://name:pass@example.org/file")
        item = self.propose()
        with self.assertRaises(SkillGrowthError):
            self.store.review("nicole", item["id"], actor="reviewer",
                              note="not checked", approve=True)
        rejected = self.store.review("nicole", item["id"], actor="reviewer",
                                     note="license unverified", approve=False)
        self.assertEqual(rejected["status"], "rejected")
        with self.assertRaises(SkillGrowthError):
            self.store.record_synthetic_practice("nicole", item["id"],
                                                 case="test", evidence="none", passed=True)


if __name__ == "__main__":
    unittest.main()
