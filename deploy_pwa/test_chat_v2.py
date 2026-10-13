"""Focused contract checks for the authenticated POC chat API."""
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image


_tmp = tempfile.TemporaryDirectory()
os.environ["POC_PREVIEW_USER"] = "test-user"
os.environ["POC_PREVIEW_PASSWORD"] = "test-password"
os.environ["POC_SESSION_SECRET"] = "test-secret-value-with-more-than-thirty-two-chars"
os.environ["POC_CHAT_DB_PATH"] = os.path.join(_tmp.name, "chat.sqlite3")
os.environ["MINIMAX_API_KEY"] = "test-key-not-sent"
from deploy_pwa.server import app  # noqa: E402


class ChatV2Test(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.base = "https://example.test"
        self.headers = {"Origin": self.base}

    def _login(self):
        response = self.client.post("/api/login", base_url=self.base,
                                    headers=self.headers,
                                    json={"username": "test-user", "password": "test-password"})
        self.assertEqual(response.status_code, 200)

    def test_auth_csrf_and_persistent_history(self):
        self.assertEqual(self.client.get("/api/v2/history", base_url=self.base).status_code, 401)
        self._login()
        self.assertEqual(self.client.post("/api/v2/chat", base_url=self.base,
                                          json={"text": "你好"}).status_code, 403)
        with patch("deploy_pwa.server.reply_to", return_value="你好，我是陈端倪。"):
            response = self.client.post("/api/v2/chat", base_url=self.base,
                                        headers=self.headers, json={"text": "你好"})
        self.assertEqual(response.status_code, 200)
        sid = response.json["session_id"]
        events = self.client.get("/api/v2/history?session_id=" + sid,
                                 base_url=self.base).json["events"]
        self.assertEqual([x["role"] for x in events], ["user", "assistant"])
        self.assertEqual(events[0]["source_kind"], "user_explicit")
        self.assertEqual(events[1]["source_kind"], "model_generated")

    def test_image_is_validated_and_sent_to_adapter(self):
        self._login()
        bad = self.client.post("/api/v2/upload-image", base_url=self.base,
                               headers=self.headers,
                               data={"file": (io.BytesIO(b"not an image"), "bad.png")})
        self.assertEqual(bad.status_code, 400)
        buffer = io.BytesIO()
        Image.new("RGB", (4, 4), "blue").save(buffer, "PNG")
        buffer.seek(0)
        uploaded = self.client.post("/api/v2/upload-image", base_url=self.base,
                                    headers=self.headers,
                                    data={"file": (buffer, "test.png")})
        self.assertEqual(uploaded.status_code, 201)
        iid = uploaded.json["image_id"]
        self.assertEqual(self.client.get(uploaded.json["url"],
                                         base_url=self.base).mimetype, "image/png")
        with patch("deploy_pwa.server.reply_to", return_value="我看到一小块蓝色。") as model:
            response = self.client.post("/api/v2/chat", base_url=self.base,
                                        headers=self.headers,
                                        json={"text": "这是什么", "image_id": iid})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(model.call_args.kwargs["image"][0], "image/png")
        self.assertTrue(model.call_args.kwargs["image"][1].startswith(b"\x89PNG"))

    def test_uploaded_image_metadata_is_removed(self):
        self._login()
        buffer = io.BytesIO()
        exif = Image.Exif()
        exif[270] = "private-camera-note"
        Image.new("RGB", (4, 4), "green").save(buffer, "JPEG", exif=exif)
        buffer.seek(0)
        uploaded = self.client.post("/api/v2/upload-image", base_url=self.base,
                                    headers=self.headers,
                                    data={"file": (buffer, "private.jpg")})
        self.assertEqual(uploaded.status_code, 201)
        stored = self.client.get(uploaded.json["url"], base_url=self.base)
        with Image.open(io.BytesIO(stored.data)) as decoded:
            self.assertIsNone(decoded.getexif().get(270))

    def test_explicit_memory_only_and_delete(self):
        self._login()
        saved = self.client.post("/api/v2/memories", base_url=self.base,
                                 headers=self.headers, json={"text": "我喜欢安静的音乐"})
        self.assertEqual(saved.status_code, 201)
        memories = self.client.get("/api/v2/memories", base_url=self.base).json["memories"]
        self.assertTrue(any(x["source_kind"] == "user_explicit" for x in memories))
        deleted = self.client.delete("/api/v2/memories/" + saved.json["id"],
                                     base_url=self.base, headers=self.headers)
        self.assertEqual(deleted.status_code, 200)

    def test_final_call_transcript_joins_chat_history_once(self):
        payload = {"call_id": "ailivex-yuling-coach-1791920000",
                   "speaker_identity": "coach", "segment_id": "segment-1",
                   "role": "user", "text": "今天想听你说说话"}
        self.assertEqual(self.client.post("/api/v2/call-transcript", base_url=self.base,
                                          headers=self.headers, json=payload).status_code, 401)
        self._login()
        self.assertEqual(self.client.post("/api/v2/call-transcript", base_url=self.base,
                                          json=payload).status_code, 403)
        first = self.client.post("/api/v2/call-transcript", base_url=self.base,
                                 headers=self.headers, json=payload)
        self.assertEqual(first.status_code, 201)
        self.assertTrue(first.json["created"])
        sid = first.json["session_id"]
        repeated = self.client.post("/api/v2/call-transcript", base_url=self.base,
                                    headers=self.headers,
                                    json={**payload, "text": "中途修订文字"})
        self.assertEqual(repeated.status_code, 200)
        self.assertFalse(repeated.json["created"])
        self.assertEqual(repeated.json["session_id"], sid)
        agent = self.client.post("/api/v2/call-transcript", base_url=self.base,
                                 headers=self.headers,
                                 json={**payload, "speaker_identity": "agent-1",
                                       "segment_id": "segment-2", "role": "assistant",
                                       "text": "好，我在听。", "session_id": sid})
        self.assertEqual(agent.status_code, 201)
        events = self.client.get("/api/v2/history?session_id=" + sid,
                                 base_url=self.base).json["events"]
        self.assertEqual([(x["role"], x["text"]) for x in events],
                         [("user", payload["text"]), ("assistant", "好，我在听。")])
        self.assertTrue(all(x["source_kind"] == "livekit_browser_transcript"
                            for x in events))
        with patch("deploy_pwa.server.reply_to", return_value="我记得刚才的话。") as model:
            followup = self.client.post("/api/v2/chat", base_url=self.base,
                                        headers=self.headers,
                                        json={"session_id": sid, "text": "继续聊"})
        self.assertEqual(followup.status_code, 200)
        self.assertEqual([x["text"] for x in model.call_args.args[1]],
                         [payload["text"], "好，我在听。"])
        invalid = self.client.post("/api/v2/call-transcript", base_url=self.base,
                                   headers=self.headers,
                                   json={**payload, "segment_id": "bad-id", "role": "system"})
        self.assertEqual(invalid.status_code, 400)

    def test_browser_audio_event_contract(self):
        self._login()
        import storage
        with tempfile.TemporaryDirectory() as directory, patch.object(storage, "_CALL", directory):
            posted = self.client.post("/api/yuling/calls/test-call/event", base_url=self.base,
                                      headers=self.headers,
                                      json={"label": "cough", "confidence": 0.7,
                                            "meta": {"source": "browser-heuristic"}})
            self.assertEqual(posted.status_code, 200)
            with open(os.path.join(directory, "test-call.events.json"), encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(saved[0]["label"], "cough")
            self.assertEqual(saved[0]["source_kind"], "browser_candidate")


if __name__ == "__main__":
    unittest.main()
