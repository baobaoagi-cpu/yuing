"""firestore_loader.py — Agent 端的「角色 + 記憶」載入器（通用骨架版）

realtime_agent 透過這個模組拿到「它是誰」與「它記得什麼」。

本開源版把：
  - 角色/靈魂  → 接到 backend/character_provider（可插拔）
  - 記憶/持久化 → 做成安全的 no-op 佔位（不會讓 agent 崩，但也沒有真的長期記憶）

⚠️ 骨架說明：
   load_character / build_system_prompt / load_conversation / sanitize_spoken_text 是「真的」。
   其餘 load_*/recall_*/write_*/save_*/extract_* 都是「佔位」，回傳空或 None。
   要有長期記憶、會成長的角色 → 把這些接到你自己的資料庫 / RAG / 記憶系統。
"""
from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger("character-loader")

# 讓 agent 能 import 到 backend/character_provider
_BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

try:
    from character_provider import provider  # noqa: E402
except Exception as e:  # pragma: no cover
    logger.warning("character_provider 未載入，用內建 fallback：%s", e)
    provider = None


# ══════════ 有意義的實作（角色靈魂）══════════
def load_character(character_id: str = "default"):
    """回傳角色：system_prompt(靈魂) + voice_id(聲紋) + display_name。"""
    if provider:
        return provider.load_character(character_id)

    class _Fallback:
        system_prompt = "You are a warm, curious, helpful voice companion. Keep replies short and natural for spoken conversation."
        voice_id = os.environ.get("MINIMAX_DEFAULT_VOICE_ID", "")
        display_name = os.environ.get("CHARACTER_NAME", "AI")

    return _Fallback()


def load_conversation(conv_id: str = "", *args, **kwargs):
    if provider:
        return provider.load_conversation(conv_id)

    class _Empty:
        history: list = []
        facts: str = ""

    return _Empty()


def build_system_prompt(character=None, conversation=None, memories=None, *args, **kwargs) -> str:
    """把角色靈魂 + 記憶組裝成 system prompt。"""
    ch = character or load_character()
    parts = [getattr(ch, "system_prompt", "") or ""]
    facts = getattr(conversation, "facts", "") if conversation is not None else ""
    if facts:
        parts.append("\n# 你記得關於對方的事\n" + facts)
    if memories:
        parts.append("\n# 最近動態\n" + "\n".join(str(m) for m in memories))
    return "\n".join(p for p in parts if p)


def sanitize_spoken_text(text: str, *args, **kwargs) -> str:
    """給 TTS 之前的清理（拿掉不該唸出來的標記）。可依需求擴充。"""
    return (text or "").strip()


# ══════════ 記憶讀取（骨架版回空；接你自己的記憶系統）══════════
def load_memories(*args, **kwargs):
    return []


def load_relationship(*args, **kwargs):
    return {}


def recall_memory(*args, **kwargs):
    return []


def summarize_audio_events(*args, **kwargs):
    return ""


def extract_session_summary(*args, **kwargs):
    return ""


# ══════════ 記憶/持久化寫入（骨架版 no-op；TODO: 接你的資料庫/RAG）══════════
def save_conversation(*args, **kwargs):
    return None


def write_memory(*args, **kwargs):
    return None


def extract_and_save_memories(*args, **kwargs):
    return None


def update_last_session(*args, **kwargs):
    return None


def create_document_job(*args, **kwargs):
    return None


def write_causal(*args, **kwargs):
    return None


def record_turn(*args, **kwargs):
    return None


def save_inner(*args, **kwargs):
    return None


def save_call_audit(*args, **kwargs):
    return None


def record_latency_event(*args, **kwargs):
    return None
