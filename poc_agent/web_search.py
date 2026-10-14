"""Bounded, opt-in Alibaba Cloud Bailian WebSearch MCP for one voice call."""

import asyncio
import logging
import os
import re


logger = logging.getLogger("duanni.call.search")
MCP_URL = "https://dashscope.aliyuncs.com/api/v1/mcps/WebSearch/mcp"
MAX_QUERY_CHARS = 120
MAX_RESULT_CHARS = 4000
MAX_SEARCHES_PER_CALL = 3
PRIVATE_QUERY = re.compile(
    r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?<!\d)1[3-9]\d{9}(?!\d)|(?<!\d)09\d{8}(?!\d)",
    re.IGNORECASE,
)


def search_is_enabled() -> bool:
    """A key alone must not silently enable a potentially billable service."""
    return (
        os.environ.get("DUNNI_WEB_SEARCH_ENABLED", "") == "1"
        and bool(os.environ.get("DASHSCOPE_API_KEY", "").strip())
    )


class CallSearch:
    """One budget per LiveKit call. Counts attempts before invoking the provider."""

    def __init__(self, api_key: str, max_uses: int = MAX_SEARCHES_PER_CALL) -> None:
        if not api_key.strip():
            raise ValueError("search API key required")
        if not 1 <= max_uses <= MAX_SEARCHES_PER_CALL:
            raise ValueError("invalid per-call search cap")
        self._api_key = api_key.strip()
        self._remaining = max_uses
        self._lock = asyncio.Lock()

    async def search(self, query: str) -> str:
        query = (query or "").strip()
        if not query or len(query) > MAX_QUERY_CHARS:
            return "搜尋詞必須簡短、具體，最多 120 個字。"
        if PRIVATE_QUERY.search(query):
            return "搜尋詞可能含有私人聯絡資料；請改用公開關鍵字。"

        async with self._lock:
            if self._remaining == 0:
                return "這通電話的聯網搜尋次數已用完。"
            self._remaining -= 1

        try:
            async with asyncio.timeout(12):
                result = await self._call_mcp(query)
        except Exception as exc:
            # Provider exceptions may contain request headers, URLs or queries.
            logger.warning("call_stage web_search_failed error_type=%s", type(exc).__name__)
            return "聯網搜尋暫時失敗，請稍後再試。"

        if getattr(result, "isError", False):
            logger.warning("call_stage web_search_provider_error")
            return "聯網搜尋暫時無法取得結果。"
        text = "\n".join(
            block.text for block in getattr(result, "content", [])
            if isinstance(getattr(block, "text", None), str)
        ).strip()
        if not text:
            return "沒有找到可用的公開網頁結果。"
        logger.info("call_stage web_search_completed chars=%s", min(len(text), MAX_RESULT_CHARS))
        return text[:MAX_RESULT_CHARS]

    async def _call_mcp(self, query: str):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        headers = {"Authorization": f"Bearer {self._api_key}"}
        async with streamablehttp_client(
            MCP_URL, headers=headers, timeout=8, sse_read_timeout=8
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool("web_search", {"query": query})
