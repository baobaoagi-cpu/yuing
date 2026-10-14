"""Four-file snapshot and isolated restoration using synthetic accounts only."""
import shutil
import tempfile
import unittest
from pathlib import Path

from deploy_pwa.backup_memory_bundle import snapshot_bundle, verify_bundle
from deploy_pwa.chat_store import ChatStore
from deploy_pwa.nico_profile_store import NicoProfileStore


class BackupMemoryBundleTest(unittest.TestCase):
    def test_snapshot_verifies_and_restores_without_cross_account_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chat = root / "live" / "chat.sqlite3"
            profile_root = root / "live" / "companion"
            owner = ChatStore(str(chat), "alice")
            sid = owner.create_session()
            event = owner.add_event(sid, "user", "我喜歡合成測試",
                                    source_kind="user_explicit")
            other = ChatStore(str(chat), "bob", legacy_owner_id="alice")
            other_sid = other.create_session()
            other.add_event(other_sid, "user", "另一帳號資料", source_kind="user_explicit")
            profile = NicoProfileStore(str(chat), "alice", str(profile_root))
            profile.set_consent(True)
            profile.add_fact("preference", "我喜歡合成測試", event["id"],
                             "我喜歡合成測試")
            bundle = root / "snapshot"
            manifest = snapshot_bundle(str(chat), str(profile_root), str(bundle))
            self.assertEqual(verify_bundle(str(bundle))["file_count"], 4)
            self.assertEqual(manifest["files"]["chat.sqlite3"]["counts"]["events"], 2)
            self.assertEqual((bundle / "chat.sqlite3").stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                snapshot_bundle(str(chat), str(profile_root), str(bundle))

            restored_root = root / "restored"
            restored_root.mkdir()
            shutil.copyfile(bundle / "chat.sqlite3", restored_root / "chat.sqlite3")
            restored_profile_root = restored_root / "companion"
            restored_profile_root.mkdir()
            for name in ("memory.sqlite3", "knowledge.sqlite3", "graph.sqlite3"):
                shutil.copyfile(bundle / name, restored_profile_root / name)
            restored_alice = ChatStore(str(restored_root / "chat.sqlite3"), "alice")
            restored_bob = ChatStore(str(restored_root / "chat.sqlite3"), "bob",
                                     legacy_owner_id="alice")
            self.assertEqual(restored_alice.history(sid)[0]["id"], event["id"])
            self.assertEqual(restored_bob.history(sid), [])
            restored_profile = NicoProfileStore(str(restored_root / "chat.sqlite3"),
                                                "alice", str(restored_profile_root))
            self.assertTrue(restored_profile.consent_status())
            self.assertEqual(restored_profile.facts()[0]["source_event_id"], event["id"])

            with (bundle / "knowledge.sqlite3").open("ab") as damaged:
                damaged.write(b"tampered")
            with self.assertRaises(ValueError):
                verify_bundle(str(bundle))


if __name__ == "__main__":
    unittest.main()
