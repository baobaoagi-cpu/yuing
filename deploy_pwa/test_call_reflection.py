"""Post-call reflection: local gate, source/owner isolation, one paid call maximum."""
import io
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

os.environ.setdefault("POC_PREVIEW_USER", "test-user")
os.environ.setdefault("POC_PREVIEW_PASSWORD", "test-password")
os.environ.setdefault("POC_SESSION_SECRET", "test-secret-value-with-more-than-thirty-two-chars")
os.environ.setdefault("MINIMAX_API_KEY", "test-key-not-sent")

from deploy_pwa.server import app  # noqa: E402
from deploy_pwa.call_reflection import generate_reflection, substantial_call
from deploy_pwa.chat_api import ProviderError
from deploy_pwa.chat_store import ChatStore


class ReflectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path_patch = patch.dict(os.environ, {"POC_CHAT_DB_PATH": self.tmp.name + "/chat.db"})
        self.path_patch.start()
        self.client = app.test_client()
        self.base = "https://example.test"
        self.headers = {"Origin": self.base}
        self.call_id = "ailivex-yuling-reflection-12345"

    def tearDown(self):
        self.path_patch.stop()
        self.tmp.cleanup()

    def login(self, client=None, username="test-user", password="test-password"):
        c = client or self.client
        self.assertEqual(c.post("/api/login", base_url=self.base,
                                headers=self.headers,
                                json={"username": username, "password": password}).status_code, 200)

    def turn(self, index, role, text):
        result = self.client.post("/api/v2/call-transcript", base_url=self.base,
                                  headers=self.headers,
                                  json={"call_id": self.call_id,
                                        "speaker_identity": role,
                                        "segment_id": f"segment-{index}",
                                        "role": role, "text": text})
        self.assertEqual(result.status_code, 201)

    def long_call(self):
        for i, role, content in [
            (1, "user", "最近我要搬家，工作地点也改变了。我担心新环境会让自己每天都很焦虑。"),
            (2, "assistant", "听起来改变接连发生。你比较担心通勤，还是生活节奏被打乱？"),
            (3, "user", "主要是生活节奏。我希望这周先试着早点睡，把搬家安排分成几个小步骤。我也想保留周末和家人吃饭的时间，不想一直被工作占满。"),
            (4, "assistant", "这个决定很具体。我们可以先只看这周要处理的两件事，别急着一次想完。也许先把最紧急的搬家事项列出来，再留一段真正能休息的时间。"),
        ]:
            self.turn(i, role, content)

    def reflect(self, retry=False):
        return self.client.post(self.url(), base_url=self.base,
                                headers=self.headers, json={"retry": retry})

    def url(self):
        return "/api/v2/calls/" + self.call_id + "/reflection"

    def test_empty_short_and_one_sided_calls_never_call_model(self):
        self.login()
        self.assertEqual(self.reflect().status_code, 404)
        self.turn(1, "user", "你好")
        with patch("deploy_pwa.server.generate_reflection") as model:
            r = self.reflect()
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json["item"]["status"], "skipped")
        self.assertIsNone(r.json["item"]["text"])
        model.assert_not_called()
        self.assertEqual(self.reflect().json["created"], False)
        self.assertEqual(len(self.client.get("/api/v2/call-reflections", base_url=self.base)
                             .json["items"]), 1)

    def test_long_call_one_model_decision_source_links_and_idempotency(self):
        self.login()
        self.long_call()
        with patch("deploy_pwa.server.generate_reflection",
                   return_value=("我听见你说搬家和工作变化叠在一起。你没有要求自己一下子解决全部，而是决定先把这周安排拆开。我觉得这个选择清醒，也给自己留了呼吸的空间。", "MiniMax-M3")) as model:
            created = self.reflect()
            repeated = self.reflect()
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json["item"]["status"], "generated")
        self.assertEqual(len(created.json["item"]["source_event_ids"]), 4)
        self.assertEqual(created.json["item"]["prompt_version"], "call-heartnote-v1")
        self.assertEqual(repeated.status_code, 200)
        self.assertFalse(repeated.json["created"])
        model.assert_called_once()
        self.assertEqual(len(model.call_args.args[0]), 4)

    def test_model_skip_and_failure_are_distinct_and_retry_is_manual(self):
        self.login()
        self.long_call()
        with patch("deploy_pwa.server.generate_reflection", side_effect=ProviderError("offline")) as model:
            failed = self.reflect()
        self.assertEqual(failed.status_code, 502)
        self.assertEqual(failed.json["item"]["status"], "failed")
        self.assertIsNone(failed.json["item"]["text"])
        with patch("deploy_pwa.server.generate_reflection", return_value=(None, "MiniMax-M3")) as model:
            self.assertEqual(self.reflect().json["item"]["status"], "failed")
            retried = self.reflect(retry=True)
        self.assertEqual(retried.json["item"]["status"], "skipped")
        model.assert_called_once()

    def test_owner_and_origin_isolation(self):
        self.login()
        self.long_call()
        self.assertEqual(self.client.post(self.url(), base_url=self.base,
                                          json={}).status_code, 403)
        with patch("deploy_pwa.server.USERNAME", "friend"), patch(
                "deploy_pwa.server.PASSWORD", "friend-password"):
            other = app.test_client()
            self.login(other, "friend", "friend-password")
            self.assertEqual(other.get("/api/v2/call-reflections", base_url=self.base)
                             .json["items"], [])
            self.assertEqual(other.post(self.url(), base_url=self.base,
                                        headers=self.headers, json={}).status_code, 404)

    def test_concurrent_claim_allows_only_one_model_request(self):
        self.login()
        self.long_call()
        def claim(_):
            return ChatStore(self.tmp.name + "/chat.db", "test-user").claim_call_reflection(
                self.call_id, "call-heartnote-v1")[0]
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(claim, range(6)))
        self.assertEqual(results.count("claimed"), 1)
        self.assertEqual(results.count("existing"), 5)

    def test_provider_receives_redacted_url_and_strict_json(self):
        events = [{"role": role, "text": text, "id": str(i)} for i, (role, text) in enumerate([
            ("user", "看看 https://site.test/path?token=topsecret 这件事。"),
            ("assistant", "我可以看你实际提供的文字。"),
            ("user", "我想更清楚地规划下一步。"),
            ("assistant", "我们先确认你最关心的问题。"),
        ])]
        captured = {}
        class Response:
            def __enter__(self):
                return io.BytesIO(json.dumps({"content": [{"type": "text", "text":
                    '{"decision":"skip"}'}]}).encode())
            def __exit__(self, *args):
                return False
        def fake_urlopen(req, timeout):
            captured["body"] = req.data.decode()
            return Response()
        with patch("deploy_pwa.call_reflection.urlopen", side_effect=fake_urlopen):
            self.assertEqual(generate_reflection(events)[0], None)
        self.assertNotIn("topsecret", captured["body"])
        self.assertIn("https://site.test/path", captured["body"])
        self.assertFalse(substantial_call(events))


if __name__ == "__main__":
    unittest.main()
