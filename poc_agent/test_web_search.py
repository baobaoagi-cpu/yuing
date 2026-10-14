import asyncio
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from poc_agent.web_search import CallSearch, search_is_enabled


class AllowBudget:
    async def reserve(self):
        return True


class DenyBudget:
    async def reserve(self):
        return False


class SearchConfigurationTests(unittest.TestCase):
    def test_key_does_not_enable_billable_tool_on_its_own(self):
        base = {"DASHSCOPE_API_KEY": "test-key", "POC_USAGE_HMAC_KEY": "x" * 32,
                "POC_USAGE_ENDPOINT": "https://example.test/api/yuling/usage/ingest"}
        with patch.dict(os.environ, {**base, "DUNNI_WEB_SEARCH_ENABLED": "0"}):
            self.assertFalse(search_is_enabled())
        with patch.dict(os.environ, {**base, "DASHSCOPE_API_KEY": "", "DUNNI_WEB_SEARCH_ENABLED": "1"}):
            self.assertFalse(search_is_enabled())
        with patch.dict(os.environ, {**base, "POC_USAGE_HMAC_KEY": "", "DUNNI_WEB_SEARCH_ENABLED": "1"}):
            self.assertFalse(search_is_enabled())
        with patch.dict(os.environ, {**base, "DUNNI_WEB_SEARCH_ENABLED": "1"}):
            self.assertTrue(search_is_enabled())


class CallSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_requests_cannot_exceed_per_call_cap(self):
        search = CallSearch("test-key", AllowBudget())
        attempts = []

        async def fake_call(query):
            attempts.append(query)
            await asyncio.sleep(0)
            return SimpleNamespace(isError=False, content=[SimpleNamespace(text="来源：示例网站")])

        search._call_mcp = fake_call
        results = await asyncio.gather(*(search.search(f"查询{i}") for i in range(6)))
        self.assertEqual(len(attempts), 3)
        self.assertEqual(sum("次數已用完" in item for item in results), 3)

    async def test_rejects_invalid_query_without_spending_attempt(self):
        search = CallSearch("test-key", AllowBudget(), max_uses=1)
        search._call_mcp = lambda query: None
        self.assertIn("最多 120", await search.search("字" * 121))
        self.assertEqual(search._remaining, 1)

    async def test_private_contact_query_is_not_sent(self):
        search = CallSearch("test-key", AllowBudget(), max_uses=1)
        response = await search.search("查詢 private@example.com")
        self.assertIn("私人聯絡資料", response)
        self.assertEqual(search._remaining, 1)

    async def test_provider_failure_does_not_reveal_exception_details(self):
        search = CallSearch("test-key", AllowBudget(), max_uses=1)

        async def fail(query):
            raise RuntimeError("secret-key and private query")

        search._call_mcp = fail
        response = await search.search("天气")
        self.assertIn("暫時失敗", response)
        self.assertNotIn("secret-key", response)
        self.assertEqual(search._remaining, 0)

    async def test_result_is_bounded(self):
        search = CallSearch("test-key", AllowBudget(), max_uses=1)

        async def fake_call(query):
            pages = [{"title": "上海市人民政府", "url": "https://www.shanghai.gov.cn/",
                      "snippet": "x" * 6000}]
            return SimpleNamespace(isError=False, content=[SimpleNamespace(text=json.dumps({"pages": pages}))])

        search._call_mcp = fake_call
        rendered = await search.search("天气")
        self.assertLess(len(rendered), 1000)
        self.assertIn("www.shanghai.gov.cn", rendered)
        self.assertIn("內容尚未逐頁核實", rendered)

    async def test_global_cap_prevents_provider_call(self):
        search = CallSearch("test-key", DenyBudget(), max_uses=1)
        called = []

        async def fake_call(query):
            called.append(query)

        search._call_mcp = fake_call
        self.assertIn("總額已用完", await search.search("天气"))
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
