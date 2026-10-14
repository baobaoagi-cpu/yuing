"""Two synthetic accounts exercise persisted correction, withdrawal and context use."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from deploy_pwa import server
from deploy_pwa.chat_store import ChatStore
from deploy_pwa.voice_context_issuer import issue_voice_context
from poc_agent.signed_context import signed_context_text


class MemoryConsentIterationTest(unittest.TestCase):
    def test_two_accounts_persist_and_withdraw_from_fresh_text_and_voice_context(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "chat.sqlite3")
            env = {"POC_CHAT_DB_PATH": path,
                   "POC_COMPANION_ROOT": str(Path(directory) / "profile"),
                   "POC_LEGACY_OWNER": "synthetic_a",
                   "MINIMAX_API_KEY": "synthetic-never-sent"}
            with (patch.object(server, "USERNAME", "synthetic_a"),
                  patch.object(server, "PASSWORD", "synthetic-a-password"),
                  patch.object(server, "SESSION_SECRET", "synthetic-session-key-at-least-32-bytes"),
                  patch.object(server, "EXTRA_ACCOUNTS", {"synthetic_b": generate_password_hash(
                      "synthetic-b-password", method="pbkdf2:sha256:1000000")}),
                  patch.dict(os.environ, env)):
                base = "https://example.test"
                headers = {"Origin": base}
                a, b = server.app.test_client(), server.app.test_client()
                self.assertEqual(a.post("/api/login", base_url=base, headers=headers,
                                        json={"username": "synthetic_a",
                                              "password": "synthetic-a-password"}).status_code, 200)
                self.assertEqual(b.post("/api/login", base_url=base, headers=headers,
                                        json={"username": "synthetic_b",
                                              "password": "synthetic-b-password"}).status_code, 200)

                saved = a.post("/api/v2/memories", base_url=base, headers=headers,
                               json={"text": "合成舊記憶"})
                self.assertEqual(saved.status_code, 201)
                mid, source = saved.json["id"], saved.json["source_event_id"]
                self.assertEqual(saved.json["version"], 1)
                owner = ChatStore(path, "synthetic_a", legacy_owner_id="synthetic_a")
                other = ChatStore(path, "synthetic_b", legacy_owner_id="synthetic_a")
                self.assertEqual(owner.memories()[0]["source_event_id"], source)
                self.assertEqual(other.memories(), [])
                self.assertEqual(b.patch("/api/v2/memories/" + mid, base_url=base,
                                         headers=headers, json={"text": "偷改"}).status_code, 404)
                corrected = a.patch("/api/v2/memories/" + mid, base_url=base,
                                    headers=headers, json={"text": "合成新記憶"})
                self.assertEqual(corrected.json["item"]["version"], 2)
                self.assertEqual([r["action"] for r in owner.item_revisions("memory", mid)],
                                 ["created", "corrected"])

                with patch.object(server, "reply_to", return_value="合成回答") as model:
                    chat = a.post("/api/v2/chat", base_url=base, headers=headers,
                                  json={"text": "合成知識舊版"})
                self.assertEqual(chat.status_code, 200)
                self.assertIn("合成新記憶", [m["text"] for m in model.call_args.args[2]])
                self.assertNotIn("合成舊記憶", [m["text"] for m in model.call_args.args[2]])
                event_id = chat.json["user_event"]["id"]
                knowledge = a.post("/api/v2/knowledge", base_url=base, headers=headers,
                                   json={"title": "合成筆記", "content": "合成知識舊版",
                                         "source_event_id": event_id})
                self.assertEqual(knowledge.status_code, 201)
                kid = knowledge.json["item"]["id"]
                self.assertEqual(b.patch("/api/v2/knowledge/" + kid, base_url=base,
                                         headers=headers,
                                         json={"title": "偷改", "content": "跨帳號",
                                               "source_event_id": event_id}).status_code, 404)
                new_source = owner.add_event(owner.create_session(), "user", "合成知識新版",
                                             source_kind="user_explicit")["id"]
                a.post("/api/v2/knowledge/consent", base_url=base, headers=headers,
                       json={"enabled": True})
                self.assertEqual(owner.search_knowledge("合成知識舊版")[0]["id"], kid)
                corrected = a.patch("/api/v2/knowledge/" + kid, base_url=base,
                                    headers=headers,
                                    json={"title": "合成筆記", "content": "合成知識新版",
                                          "source_event_id": new_source})
                self.assertEqual(corrected.json["item"]["version"], 2)
                self.assertTrue(all(item["content"] != "合成知識舊版"
                                    for item in owner.search_knowledge("合成知識舊版")))
                self.assertEqual(other.search_knowledge("合成知識新版"), [])
                self.assertEqual([r["action"] for r in owner.item_revisions("knowledge", kid)],
                                 ["created", "corrected"])

                # Reconstruct both stores against the same on-disk database.
                owner = ChatStore(path, "synthetic_a", legacy_owner_id="synthetic_a")
                other = ChatStore(path, "synthetic_b", legacy_owner_id="synthetic_a")
                self.assertEqual(owner.memories()[0]["text"], "合成新記憶")
                self.assertEqual(owner.knowledge_items()[0]["content"], "合成知識新版")
                self.assertEqual(other.memories(), [])
                self.assertEqual(other.knowledge_items(), [])

                key = "synthetic-voice-signing-key-at-least-32-bytes"
                room = "ailivex-yuling-synthetic-room"
                context = signed_context_text(issue_voice_context(
                    key, room, "synthetic_a", owner.memories(),
                    knowledge=owner.search_knowledge("合成知識新版")), key, room)
                self.assertIn("合成新記憶", context)
                self.assertIn("合成知識新版", context)
                self.assertNotIn("合成舊記憶", context)
                self.assertNotIn("合成知識舊版", context)

                a.post("/api/v2/knowledge/consent", base_url=base, headers=headers,
                       json={"enabled": False})
                with patch.object(server, "reply_to", return_value="合成回答") as model:
                    response = a.post("/api/v2/chat", base_url=base, headers=headers,
                                      json={"text": "合成知識新版"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(model.call_args.kwargs["knowledge_context"], ())

                self.assertEqual(a.delete("/api/v2/memories/" + mid, base_url=base,
                                          headers=headers).status_code, 200)
                self.assertEqual(a.delete("/api/v2/knowledge/" + kid, base_url=base,
                                          headers=headers).status_code, 200)
                a.post("/api/v2/knowledge/consent", base_url=base, headers=headers,
                       json={"enabled": False})
                owner = ChatStore(path, "synthetic_a", legacy_owner_id="synthetic_a")
                self.assertEqual(owner.memories(), [])
                self.assertEqual(owner.search_knowledge("合成知識新版"), [])
                self.assertFalse(owner.knowledge_consent_enabled())
                self.assertEqual([r["action"] for r in owner.item_revisions("memory", mid)],
                                 ["created", "corrected", "retracted"])
                self.assertEqual([r["action"] for r in owner.item_revisions("knowledge", kid)],
                                 ["created", "corrected", "retracted"])
                self.assertIsNone(signed_context_text(issue_voice_context(
                    key, room, "synthetic_a", owner.memories(), knowledge=[]), key, room))
                self.assertEqual(a.get("/api/v2/knowledge/search?q=合成知識新版",
                                       base_url=base).status_code, 403)


if __name__ == "__main__":
    unittest.main()
