"""Focused contract checks for the authenticated POC chat API."""
import io
import json
import os
import tempfile
import unittest
import uuid
import zipfile
from unittest.mock import patch

from PIL import Image
from openpyxl import Workbook
from pypdf import PdfWriter


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

    def test_install_manifest_and_icons_are_public_but_app_is_protected(self):
        manifest = self.client.get("/manifest.webmanifest", base_url=self.base)
        self.assertEqual(manifest.status_code, 200)
        self.assertEqual(manifest.mimetype, "application/manifest+json")
        info = json.loads(manifest.data)
        manifest.close()
        self.assertEqual(info["start_url"], "/chat")
        for icon in info["icons"]:
            response = self.client.get(icon["src"], base_url=self.base)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, icon["type"])
            response.close()
        self.assertEqual(self.client.get("/chat", base_url=self.base).status_code, 303)
        self.assertEqual(self.client.get("/api/v2/history", base_url=self.base).status_code, 401)

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

    def test_status_does_not_claim_persistence_from_a_path_alone(self):
        self._login()
        with patch.dict(os.environ, {"POC_CHAT_DB_PATH": os.path.join(_tmp.name, "chat.sqlite3"),
                                     "RAILWAY_SERVICE_ID": "test-service",
                                     "RAILWAY_VOLUME_MOUNT_PATH": ""}):
            status = self.client.get("/api/v2/status", base_url=self.base)
        self.assertEqual(status.status_code, 200)
        self.assertFalse(status.json["persistent_history"])
        self.assertEqual(status.json["memory_persistence"], "ephemeral")

        with patch.dict(os.environ, {"RAILWAY_SERVICE_ID": "",
                                     "RAILWAY_VOLUME_MOUNT_PATH": ""}):
            status = self.client.get("/api/v2/status", base_url=self.base)
        self.assertFalse(status.json["persistent_history"])
        self.assertEqual(status.json["memory_persistence"], "path_configured_unverified")

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

    def test_document_upload_chat_history_and_account_isolation(self):
        self._login()
        uploaded = self.client.post("/api/v2/upload-document", base_url=self.base,
                                    headers=self.headers,
                                    data={"file": (io.BytesIO("上海文件内容".encode()), "备忘.txt")})
        self.assertEqual(uploaded.status_code, 201)
        did = uploaded.json["document_id"]
        self.assertEqual(uploaded.json["document"]["extract_status"], "text_preview")
        with patch("deploy_pwa.server.reply_to", return_value="我读到了文字预览。") as model:
            response = self.client.post("/api/v2/chat", base_url=self.base,
                                        headers=self.headers,
                                        json={"text": "这份文件写什么？", "document_id": did})
        self.assertEqual(response.status_code, 200)
        self.assertIn("上海文件内容", model.call_args.kwargs["document_context"]["extracted_text"])
        history = self.client.get("/api/v2/history?session_id=" + response.json["session_id"],
                                  base_url=self.base).json["events"]
        self.assertEqual(history[0]["document"]["filename"], "备忘.txt")
        stored = self.client.get("/api/v2/document/" + did, base_url=self.base)
        self.assertEqual(stored.status_code, 200)
        self.assertEqual(stored.data, "上海文件内容".encode())
        with patch("deploy_pwa.server.USERNAME", "friend"), patch(
                "deploy_pwa.server.PASSWORD", "friend-password"):
            other = app.test_client()
            self.assertEqual(other.post("/api/login", base_url=self.base,
                                        headers=self.headers,
                                        json={"username": "friend", "password": "friend-password"}).status_code, 200)
            self.assertEqual(other.get("/api/v2/document/" + did, base_url=self.base).status_code, 404)
            self.assertEqual(other.post("/api/v2/chat", base_url=self.base,
                                        headers=self.headers,
                                        json={"text": "读别人的文件", "document_id": did}).status_code, 404)

    def test_pdf_upload_survives_stale_session_retry(self):
        self._login()
        pdf = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.write(pdf)
        pdf.seek(0)
        uploaded = self.client.post("/api/v2/upload-document", base_url=self.base,
                                    headers=self.headers,
                                    data={"file": (pdf, "sample.pdf")})
        self.assertEqual(uploaded.status_code, 201)
        did = uploaded.json["document_id"]
        stale = self.client.post("/api/v2/chat", base_url=self.base,
                                 headers=self.headers,
                                 json={"text": "看看这份 PDF", "document_id": did,
                                       "session_id": str(uuid.uuid4())})
        self.assertEqual(stale.status_code, 404)
        self.assertEqual(stale.json["error"], "会话不存在")
        with patch("deploy_pwa.server.reply_to", return_value="这份 PDF 没有可抽取文字。"):
            recovered = self.client.post("/api/v2/chat", base_url=self.base,
                                         headers=self.headers,
                                         json={"text": "看看这份 PDF", "document_id": did})
        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(recovered.json["user_event"]["document_id"], did)

    def test_document_rejects_unsupported_invalid_and_oversize(self):
        self._login()
        for filename, content, status in [
            ("old.doc", b"legacy", 400),
            ("script.exe", b"MZbad", 415),
            ("fake.pdf", b"not a pdf", 400),
            ("huge.txt", b"x" * 5_000_001, 413),
        ]:
            response = self.client.post("/api/v2/upload-document", base_url=self.base,
                                        headers=self.headers,
                                        data={"file": (io.BytesIO(content), filename)})
            self.assertEqual(response.status_code, status, filename)

    def test_legacy_office_and_ofd_are_download_only(self):
        self._login()
        ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"placeholder"
        ofd = io.BytesIO()
        with zipfile.ZipFile(ofd, "w") as archive:
            archive.writestr("OFD.xml", "<ofd/>")
        for filename, content in [
            ("旧稿.doc", ole), ("表格.xls", ole), ("演示.ppt", ole),
            ("金山.wps", ole), ("金山.et", ole), ("金山.dps", ole),
            ("发票.ofd", ofd.getvalue()),
        ]:
            uploaded = self.client.post("/api/v2/upload-document", base_url=self.base,
                                        headers=self.headers,
                                        data={"file": (io.BytesIO(content), filename)})
            self.assertEqual(uploaded.status_code, 201, (filename, uploaded.json))
            self.assertEqual(uploaded.json["document"]["extract_status"], "unsupported_extract")
            self.assertIn("暂不支持内容解析", uploaded.json["note"])
            did = uploaded.json["document_id"]
            with patch("deploy_pwa.server.reply_to", return_value="目前只能保存，无法读取内容。") as model:
                exchange = self.client.post("/api/v2/chat", base_url=self.base,
                                            headers=self.headers,
                                            json={"text": "这份文件是什么？", "document_id": did})
            self.assertEqual(exchange.status_code, 200)
            self.assertEqual(model.call_args.kwargs["document_context"]["extracted_text"], "")
            self.assertEqual(self.client.get("/api/v2/document/" + did,
                                             base_url=self.base).data, content)
            with patch("deploy_pwa.server.USERNAME", "friend"), patch(
                    "deploy_pwa.server.PASSWORD", "friend-password"):
                other = app.test_client()
                self.assertEqual(other.post("/api/login", base_url=self.base,
                                            headers=self.headers,
                                            json={"username": "friend", "password": "friend-password"}).status_code, 200)
                self.assertEqual(other.get("/api/v2/document/" + did,
                                           base_url=self.base).status_code, 404)

    def test_docx_xlsx_and_gb18030_text_extraction(self):
        self._login()
        docx = io.BytesIO()
        with zipfile.ZipFile(docx, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("word/document.xml",
                             '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                             '<w:body><w:p><w:r><w:t>上海 Word 内容</w:t></w:r></w:p></w:body></w:document>')
        workbook = Workbook()
        workbook.active.title = "数据"
        workbook.active.append(["城市", "上海"])
        xlsx = io.BytesIO()
        workbook.save(xlsx)
        for filename, content, expected in [
            ("说明.docx", docx.getvalue(), "上海 Word 内容"),
            ("数据.xlsx", xlsx.getvalue(), "上海"),
            ("备注.csv", "地址,上海".encode("gb18030"), "上海"),
        ]:
            response = self.client.post("/api/v2/upload-document", base_url=self.base,
                                        headers=self.headers,
                                        data={"file": (io.BytesIO(content), filename)})
            self.assertEqual(response.status_code, 201, (filename, response.json))
            from deploy_pwa.chat_store import ChatStore
            store = ChatStore(os.environ["POC_CHAT_DB_PATH"], "test-user")
            extracted = store.get_document(response.json["document_id"])["extracted_text"]
            self.assertIn(expected, extracted)

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

    def test_knowledge_requires_original_user_message_and_can_be_removed(self):
        self.assertEqual(self.client.get("/api/v2/knowledge", base_url=self.base).status_code, 401)
        self._login()
        with patch("deploy_pwa.server.reply_to", return_value="我记下来源了。"):
            exchange = self.client.post("/api/v2/chat", base_url=self.base,
                                        headers=self.headers,
                                        json={"text": "我住在上海"})
        self.assertEqual(exchange.status_code, 200)
        source = exchange.json["user_event"]["id"]
        candidate = {"title": "居住地", "content": "使用者说自己住在上海",
                     "source_event_id": source}
        self.assertEqual(self.client.post("/api/v2/knowledge", base_url=self.base,
                                          json=candidate).status_code, 403)
        self.assertEqual(self.client.post("/api/v2/knowledge", base_url=self.base,
                                          headers=self.headers,
                                          json={**candidate, "source_event_id":
                                                exchange.json["assistant_event"]["id"]}).status_code, 404)
        saved = self.client.post("/api/v2/knowledge", base_url=self.base,
                                 headers=self.headers, json=candidate)
        self.assertEqual(saved.status_code, 201)
        self.assertEqual(saved.json["item"]["source_event_id"], source)
        listing = self.client.get("/api/v2/knowledge", base_url=self.base)
        self.assertTrue(any(item["id"] == saved.json["item"]["id"]
                            for item in listing.json["items"]))
        self.assertEqual(self.client.delete("/api/v2/knowledge/" + saved.json["item"]["id"],
                                            base_url=self.base, headers=self.headers).status_code, 200)

    def test_relation_is_only_a_sourced_candidate(self):
        self._login()
        with patch("deploy_pwa.server.reply_to", return_value="我会把关系先标成候选。"):
            exchange = self.client.post("/api/v2/chat", base_url=self.base,
                                        headers=self.headers,
                                        json={"text": "测试：甲可能影响乙"})
        self.assertEqual(exchange.status_code, 200)
        source = exchange.json["user_event"]["id"]
        ids = []
        for title in ("甲", "乙"):
            saved = self.client.post("/api/v2/knowledge", base_url=self.base,
                                     headers=self.headers,
                                     json={"title": title, "content": "测试条目" + title,
                                           "source_event_id": source})
            self.assertEqual(saved.status_code, 201)
            ids.append(saved.json["item"]["id"])
        payload = {"subject_id": ids[0], "predicate": "可能影响",
                   "object_id": ids[1], "source_event_id": source}
        self.assertEqual(self.client.post("/api/v2/relations", base_url=self.base,
                                          json=payload).status_code, 403)
        saved = self.client.post("/api/v2/relations", base_url=self.base,
                                 headers=self.headers, json=payload)
        self.assertEqual(saved.status_code, 201)
        self.assertEqual(saved.json["item"]["status"], "candidate")
        listed = self.client.get("/api/v2/relations", base_url=self.base).json
        self.assertEqual(listed["status"], "candidate_only")
        self.assertIn(saved.json["item"]["id"], [item["id"] for item in listed["items"]])
        self.assertEqual(self.client.delete("/api/v2/relations/" + saved.json["item"]["id"],
                                            base_url=self.base, headers=self.headers).status_code, 200)

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
        calls = self.client.get("/api/v2/calls", base_url=self.base)
        self.assertEqual(calls.status_code, 200)
        self.assertFalse(calls.json["audio_saved"])
        listed = next(x for x in calls.json["calls"] if x["call_id"] == payload["call_id"])
        self.assertEqual(listed["turns"], 2)
        detail = self.client.get("/api/v2/calls/" + payload["call_id"], base_url=self.base)
        self.assertEqual(detail.status_code, 200)
        self.assertFalse(detail.json["audio_saved"])
        self.assertEqual([(x["role"], x["text"]) for x in detail.json["turns"]],
                         [("user", payload["text"]), ("assistant", "好，我在听。")])
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

    def test_saved_call_list_and_detail_are_account_scoped(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
                os.environ, {"POC_CHAT_DB_PATH": os.path.join(directory, "calls.sqlite3")}
        ):
            self.assertEqual(self.client.get("/api/v2/calls", base_url=self.base).status_code, 401)
            self._login()
            call_id = "ailivex-yuling-owner-1791920000"
            posted = self.client.post("/api/v2/call-transcript", base_url=self.base,
                                      headers=self.headers,
                                      json={"call_id": call_id, "speaker_identity": "owner",
                                            "segment_id": "segment-1", "role": "user",
                                            "text": "只属于这个账号"})
            self.assertEqual(posted.status_code, 201)
            with patch("deploy_pwa.server.USERNAME", "friend"), patch(
                    "deploy_pwa.server.PASSWORD", "friend-password"):
                other = app.test_client()
                self.assertEqual(other.post("/api/login", base_url=self.base,
                                            headers=self.headers,
                                            json={"username": "friend", "password": "friend-password"}).status_code, 200)
                self.assertEqual(other.get("/api/v2/calls", base_url=self.base).json["calls"], [])
                self.assertEqual(other.get("/api/v2/calls/" + call_id,
                                           base_url=self.base).status_code, 404)
            self.assertEqual(self.client.get("/api/v2/calls/" + call_id,
                                             base_url=self.base).json["turns"][0]["text"],
                             "只属于这个账号")

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

    def test_separate_account_cannot_read_existing_account_data(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
                os.environ, {"POC_CHAT_DB_PATH": os.path.join(directory, "chat.sqlite3")}
        ):
            self._login()
            with patch("deploy_pwa.server.reply_to", return_value="你好。"):
                posted = self.client.post("/api/v2/chat", base_url=self.base,
                                          headers=self.headers, json={"text": "私人的文字"})
            self.assertEqual(posted.status_code, 200)
            sid = posted.json["session_id"]
            saved = self.client.post("/api/v2/memories", base_url=self.base,
                                     headers=self.headers,
                                     json={"text": "私人的记忆"})
            self.assertEqual(saved.status_code, 201)
            buffer = io.BytesIO()
            Image.new("RGB", (4, 4), "blue").save(buffer, "PNG")
            buffer.seek(0)
            uploaded = self.client.post("/api/v2/upload-image", base_url=self.base,
                                        headers=self.headers,
                                        data={"file": (buffer, "private.png")})
            self.assertEqual(uploaded.status_code, 201)
            with patch("deploy_pwa.server.USERNAME", "friend"), patch(
                    "deploy_pwa.server.PASSWORD", "friend-password"):
                other = app.test_client()
                signed_in = other.post("/api/login", base_url=self.base,
                                       headers=self.headers,
                                       json={"username": "friend", "password": "friend-password"})
                self.assertEqual(signed_in.status_code, 200)
                history = other.get("/api/v2/history?session_id=" + sid,
                                    base_url=self.base)
                self.assertEqual(history.status_code, 404)
                self.assertEqual(other.get("/api/v2/history", base_url=self.base)
                                 .json["sessions"], [])
                self.assertEqual(other.get("/api/v2/memories", base_url=self.base)
                                 .json["memories"], [])
                self.assertEqual(other.get(uploaded.json["url"], base_url=self.base)
                                 .status_code, 404)


if __name__ == "__main__":
    unittest.main()
