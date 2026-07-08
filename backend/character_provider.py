"""character_provider.py — 可插拔的「角色靈魂 + 記憶」介面

語音 Agent（模組 A）與會議模組（模組 B）都透過這個介面拿到：
  1. 「它是誰」——system prompt（人格/靈魂）+ voice_id（聲紋）
  2. 「它記得什麼」——對話歷史 + 關於使用者的長期記憶

本套件【故意不含任何特定人格】。你透過實作 CharacterProvider，
把你自己的角色/記憶系統接進來。

內附 FileCharacterProvider 作為最小可跑範例（從 config/character.md 讀）。
若要「有長期記憶、會成長」的角色，把 load_conversation 接到你自己的
資料庫 / RAG / 記憶系統即可。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import List, Dict


@dataclass
class CharacterContext:
    """AI 的身份。"""
    system_prompt: str                # 角色的靈魂 / 人格（system prompt）
    voice_id: str = ""                # TTS 聲紋 ID（例：MiniMax voice_id）
    display_name: str = "AI"          # 對外顯示名稱


@dataclass
class ConversationContext:
    """AI 對這位使用者的記憶。"""
    history: List[Dict] = field(default_factory=list)  # [{"role","content"}, ...]
    facts: str = ""                   # 關於使用者的長期記憶（自由文字，會注入 prompt）


class CharacterProvider:
    """實作這兩個方法，就能給你的語音 AI 一個靈魂與記憶。"""

    def load_character(self, character_id: str = "default") -> CharacterContext:
        raise NotImplementedError

    def load_conversation(self, conv_id: str = "") -> ConversationContext:
        raise NotImplementedError

    # 通話 / 會議結束後把對話寫回你的記憶系統（可選；預設不做）
    def save_conversation(self, conv_id: str, turns: List[Dict]) -> None:
        return None


# ──────────────────────────────────────────────────────────────
#  最小範例實作：從 config/character.md + 環境變數讀一個靜態角色，
#  對話記憶存本地 JSON 檔。足以讓整套跑起來；正式使用請換成你自己的。
# ──────────────────────────────────────────────────────────────
class FileCharacterProvider(CharacterProvider):
    def __init__(self, config_dir: str = "config", data_dir: str = "data"):
        self.config_dir = config_dir
        self.data_dir = data_dir

    def load_character(self, character_id: str = "default") -> CharacterContext:
        path = os.path.join(self.config_dir, "character.md")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                prompt = f.read().strip()
        else:
            prompt = "You are a warm, curious, helpful voice companion. Keep replies short and natural for spoken conversation."
        return CharacterContext(
            system_prompt=prompt,
            voice_id=os.environ.get("MINIMAX_DEFAULT_VOICE_ID", ""),
            display_name=os.environ.get("CHARACTER_NAME", "AI"),
        )

    def _conv_path(self, conv_id: str) -> str:
        safe = "".join(c for c in conv_id if c.isalnum() or c in "-_") or "default"
        return os.path.join(self.data_dir, "conversations", f"{safe}.json")

    def load_conversation(self, conv_id: str = "") -> ConversationContext:
        path = self._conv_path(conv_id)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            return ConversationContext(history=d.get("history", []),
                                       facts=d.get("facts", ""))
        return ConversationContext()

    def save_conversation(self, conv_id: str, turns: List[Dict]) -> None:
        path = self._conv_path(conv_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        ctx = self.load_conversation(conv_id)
        ctx.history.extend(turns)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"history": ctx.history, "facts": ctx.facts}, f,
                      ensure_ascii=False, indent=2)


# 預設用範例實作。換成你自己的：
#   from my_memory import MyCharacterProvider
#   provider = MyCharacterProvider()
provider: CharacterProvider = FileCharacterProvider()
