"""Bounded, opt-in Alibaba Cloud Bailian WebSearch MCP for one voice call."""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import time
import uuid
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


logger = logging.getLogger("duanni.call.search")
MCP_URL = "https://dashscope.aliyuncs.com/api/v1/mcps/WebSearch/mcp"
MAX_QUERY_CHARS = 120
MAX_RESULT_CHARS = 4000
MAX_SEARCHES_PER_CALL = 3
SEARCH_BUDGET_SUFFIX = "/api/yuling/search/reserve"
PRIVATE_QUERY = re.compile(
    r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?<!\d)1[3-9]\d{9}(?!\d)|(?<!\d)09\d{8}(?!\d)",
    re.IGNORECASE,
)


def _search_result_text(result) -> str:
    """Keep only a few source-backed results; provider snippets are untrusted."""
    for block in getattr(result, "content", []):
        raw = getattr(block, "text", None)
        if not isinstance(raw, str):
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        pages = data.get("pages") if isinstance(data, dict) else None
        if not isinstance(pages, list):
            continue
        lines = []
        for page in pages[:3]:
            if not isinstance(page, dict):
                continue
            url = page.get("url", "")
            try:
                parsed = urlparse(url) if isinstance(url, str) else None
                hostname = parsed.hostname if parsed else None
            except ValueError:
                continue
            if not parsed or parsed.scheme not in {"http", "https"} or not hostname:
                continue
            title = str(page.get("title", ""))[:120]
            snippet = str(page.get("snippet", ""))[:300]
            lines.append(f"{len(lines) + 1}. {title}｜{hostname}\n{snippet}\n來源：{url[:350]}")
        if lines:
            return "公開網頁搜尋結果（內容尚未逐頁核實）：\n" + "\n".join(lines)
    return ""


def search_is_enabled() -> bool:
    """A key alone must not silently enable a potentially billable service."""
    return (
        os.environ.get("DUNNI_WEB_SEARCH_ENABLED", "") == "1"
        and bool(os.environ.get("DASHSCOPE_API_KEY", "").strip())
        and len(os.environ.get("POC_USAGE_HMAC_KEY", "").encode("utf-8")) >= 32
        and os.environ.get("POC_USAGE_ENDPOINT", "").startswith("https://")
        and os.environ.get("POC_USAGE_ENDPOINT", "").endswith("/api/yuling/usage/ingest")
    )


class SearchBudget:
    """Ask the volume-backed PWA ledger to reserve one of 20 lifetime trial calls."""

    def __init__(self, usage_endpoint: str, secret: str, owner_id: str, room: str) -> None:
        if not usage_endpoint.startswith("https://") or not usage_endpoint.endswith("/api/yuling/usage/ingest"):
            raise ValueError("valid HTTPS usage endpoint required")
        if len(secret.encode("utf-8")) < 32 or not owner_id or not re.fullmatch(r"ailivex-yuling-[A-Za-z0-9_-]{1,115}", room):
            raise ValueError("signed call identity required")
        self._endpoint = usage_endpoint.removesuffix("/api/yuling/usage/ingest") + SEARCH_BUDGET_SUFFIX
        self._secret = secret
        self._owner_id = owner_id
        self._room = room

    async def reserve(self) -> bool:
        payload = {
            "request_id": uuid.uuid4().hex,
            "owner_id": self._owner_id,
            "call_id": self._room,
            "created_at": int(time.time()),
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = "sha256=" + hmac.new(
            self._secret.encode("utf-8"), b"dunni-search-v1\n" + raw, hashlib.sha256
        ).hexdigest()

        def _post() -> bool:
            req = Request(self._endpoint, data=raw, method="POST", headers={
                "Content-Type": "application/json", "X-Search-Signature": signature,
            })
            try:
                with urlopen(req, timeout=4) as response:
                    return response.status == 201
            except HTTPError as exc:
                if exc.code == 429:
                    return False
                raise

        return await asyncio.to_thread(_post)


class CallSearch:
    """One budget per LiveKit call. Counts attempts before invoking the provider."""

    def __init__(self, api_key: str, budget: SearchBudget, max_uses: int = MAX_SEARCHES_PER_CALL) -> None:
        if not api_key.strip():
            raise ValueError("search API key required")
        if not 1 <= max_uses <= MAX_SEARCHES_PER_CALL:
            raise ValueError("invalid per-call search cap")
        self._api_key = api_key.strip()
        self._budget = budget
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
            if not await self._budget.reserve():
                return "聯網搜尋試用總額已用完。"
            async with asyncio.timeout(12):
                result = await self._call_mcp(query)
        except Exception as exc:
            # Provider exceptions may contain request headers, URLs or queries.
            logger.warning("call_stage web_search_failed error_type=%s", type(exc).__name__)
            return "聯網搜尋暫時失敗，請稍後再試。"

        if getattr(result, "isError", False):
            logger.warning("call_stage web_search_provider_error")
            return "聯網搜尋暫時無法取得結果。"
        text = _search_result_text(result)
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
                return await session.call_tool("bailian_web_search", {"query": query, "count": 3})
