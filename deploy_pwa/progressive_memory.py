"""Bounded, literal topic lookup for the text chat's one-to-one memory."""
from __future__ import annotations

import re


_CJK = re.compile(r"[\u3400-\u9fff]+")
_ASCII = re.compile(r"[A-Za-z0-9]{3,32}")
_COMMON = {
    "你好", "我是", "是谁", "什么", "怎么", "如何", "今天", "现在",
    "你的", "我的", "我们", "你们", "知道", "记得", "可以", "一下",
    "这个", "那个", "时候", "是否", "请问", "还有", "有没有", "真的",
}


def topic_terms(text: str) -> list[str]:
    """Extract a few literal terms; an empty/generic turn retrieves nothing.

    This deliberately makes no claim of semantic similarity. Later, a scoped
    search index can replace it without changing the two-stage store API.
    """
    if not isinstance(text, str) or not text.strip():
        return []
    words = []
    for block in _CJK.findall(text[:400]):
        words.extend(block[i:i + 2] for i in range(len(block) - 1))
    words.extend(word.casefold() for word in _ASCII.findall(text[:400]))
    selected = []
    seen = set()
    for word in reversed(words):
        if word in _COMMON or word in seen:
            continue
        selected.append(word)
        seen.add(word)
        if len(selected) == 12:
            break
    return selected


def memories_for_turn(store, text: str, *, max_details=3) -> list[dict]:
    """Find owner-scoped previews, then load only selected full records."""
    terms = topic_terms(text)
    if not terms:
        return []
    count = max(1, min(int(max_details), 3))
    candidates = store.memory_index(terms=terms, limit=8)
    return store.memory_details([item["id"] for item in candidates[:count]])
