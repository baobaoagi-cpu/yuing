"""MiniMax text adapter for the single-account POC; never logs credentials."""
from __future__ import annotations

import json
import os
import base64
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
PERSONA = (ROOT / "persona" / "chen_dunni_constitution_v1.md").read_text(encoding="utf-8")
PRINCIPLES = (ROOT / "persona" / "pset_principles_v0.1.md").read_text(encoding="utf-8")
SYSTEM = (
    "你是陈端倪。以下两层人格宪法决定你的价值、独立判断与说话方式。"
    "自然、温暖、简洁地用简体中文回应。不要自称语灵；语灵只是前端名称。"
    "不要假装拥有人类意识或真实感官。"
    "目前线上文字版只有可核对的对话记录、用户主动保存的记忆及当前上传的图片，"
    "没有接入 Hermes、向量检索、图数据库或实时网络搜索。"
    "用户贴的网址只是文本，除非系统提供已核对的网页内容，否则不要声称已阅读。"
    "仅在当前消息确实提供图片内容时，才依据画面回答；不能推断看不见的细节。"
    "用户明确保存的记忆可参考，但记忆可能过时；不确定时先确认。"
    "不要把自己的推测说成用户明示的事实。\n\n"
    "【核心人格】\n" + PERSONA + "\n\n【人格独立与演化原则】\n" + PRINCIPLES
)


class ProviderError(RuntimeError):
    pass


def reply_to(text, history, memories=(), image=None, link_url=None):
    key = os.environ.get("MINIMAX_API_KEY", "").strip()
    if not key:
        raise ProviderError("对话模型尚未配置")
    model = os.environ.get("MINIMAX_LLM_MODEL", "MiniMax-M3")
    if image and model != "MiniMax-M3":
        raise ProviderError("当前模型尚未验证图片理解")
    messages = []
    for item in history[-16:]:
        if item["role"] not in {"user", "assistant"}:
            continue
        content = item["text"] or ""
        if item.get("image_id"):
            content += "\n[先前上传过图片；本轮未重新提供像素，不要声称仍看得见。]"
        if item.get("url"):
            content += "\n[先前分享的网址，未抓取网页内容：" + item["url"] + "]"
        if content:
            messages.append({"role": item["role"], "content": content[:4000]})
    if image:
        text = text or "请看看这张图片，并说明你能确认的内容。"
    if link_url:
        text += "\n[用户分享的网址，未抓取网页内容：" + link_url + "]"
    if image:
        mime, raw = image
        content = [{"type": "text", "text": text},
                   {"type": "image", "source": {
                       "type": "base64", "media_type": mime,
                       "data": base64.b64encode(raw).decode("ascii"), "detail": "low"}}]
    else:
        content = text
    messages.append({"role": "user", "content": content})
    remembered = "\n".join("- " + x["text"][:400] for x in list(memories)[:12])
    system = SYSTEM + ("\n\n【用户主动保存的记忆；可能过时】\n" + remembered if remembered else "")
    body = json.dumps({"model": model,
                       "max_tokens": 400, "system": system,
                       "messages": messages}, ensure_ascii=False).encode("utf-8")
    req = Request("https://api.minimax.io/anthropic/v1/messages", data=body,
                  headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                           "Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(req, timeout=35) as response:
            payload = json.load(response)
    except HTTPError as exc:
        raise ProviderError("对话模型暂时无法回应") from exc
    except (URLError, TimeoutError, ValueError) as exc:
        raise ProviderError("对话模型暂时无法连接") from exc
    chunks = payload.get("content", [])
    answer = "".join(x.get("text", "") for x in chunks if x.get("type") == "text").strip()
    if not answer:
        raise ProviderError("对话模型没有返回文字")
    return answer[:4000]
