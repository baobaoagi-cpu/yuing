"""Small, isolated local bridge for the yuing/Hermes integration experiment.

Hermes HOME files are read only for configuration checks. Its private persona,
memory provider and tools are never imported into this process.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from backend.character_provider import CharacterContext, CharacterProvider, ConversationContext

DUNNI = Path(os.environ.get("DUANNI_ROOT", str(Path.home() / "codex/dunni")))
MODEL = "qwen3:4b-duanni-voice"
OLLAMA = "http://127.0.0.1:11435"
MEM_NS = "project:yuing-local-poc-memory"
KB_NS = "project:yuing-local-poc-knowledge"


class PublicPOCCharacterProvider(CharacterProvider):
    """yuing's persona interface, backed only by a public POC prompt."""

    def load_character(self, character_id: str = "default") -> CharacterContext:
        if character_id != "default":
            raise ValueError("只允許公開測試人格。")
        return CharacterContext(
            system_prompt=(HERE / "persona_public.md").read_text(encoding="utf-8"),
            display_name="端倪・測試版",
            voice_id="",
        )

    def load_conversation(self, conv_id: str = "") -> ConversationContext:
        return ConversationContext()


def _load_runtime():
    source = DUNNI / "services/memory/memory_runtime.py"
    if not source.is_file():
        raise RuntimeError("找不到既有 MemoryRuntime；請設定 DUANNI_ROOT。")
    spec = importlib.util.spec_from_file_location("yuing_poc_memory_runtime", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Never point the POC at HOME's canonical store or private namespace.
    module.CANON = HERE / ".state/canonical"
    module.HOLO = HERE / ".state/index"
    return module


class PublicContext:
    def __init__(self):
        self.character_provider = PublicPOCCharacterProvider()
        self.persona = self.character_provider.load_character().system_prompt
        self.fixtures = json.loads((HERE / "fixtures.json").read_text(encoding="utf-8"))
        self.runtime_module = _load_runtime()
        self.runtime = self.runtime_module.MemoryRuntime(MEM_NS)
        self._seed()

    def _seed(self):
        # This store is purpose-built for the POC. It never imports real memory.
        if not self.runtime_module.canon_read(MEM_NS):
            for statement in self.fixtures["memory"]:
                self.runtime.remember(statement, ns=MEM_NS, category="fact",
                                      provenance={"source": "yuing-local-poc-fixture"})
        if not self.runtime_module.canon_read(KB_NS):
            for statement in self.fixtures["knowledge"]:
                self.runtime.knowledge_add(statement, ns=KB_NS, category="reference")

    def retrieve(self, query):
        memory = self.runtime.recall(query, ns=MEM_NS, limit=2)
        knowledge = self.runtime.knowledge_search(query, ns=KB_NS, limit=2)
        allowed = set(self.fixtures["memory"] + self.fixtures["knowledge"])
        memory_rows = [row for row in memory.get("results", [])
                       if row.get("content") in allowed and row.get("namespace") == MEM_NS]
        knowledge_rows = [row for row in knowledge
                          if row.get("content") in allowed and row.get("namespace") == KB_NS]
        return memory_rows[:2], knowledge_rows[:2]

    def close(self):
        self.runtime.close()


def hermes_profile_status():
    """Check the local model settings used by HOME, without opening a HOME session."""
    import yaml
    config_path = DUNNI / "duanni-home/runtime/hermes/home/config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    checks = {
        "zero_tools": config.get("agent", {}).get("disabled_toolsets") == ["all"]
                      and config.get("platform_toolsets", {}).get("cli") == [],
        "model": config.get("model", {}).get("default") == MODEL,
        "endpoint": config.get("model", {}).get("base_url") == OLLAMA + "/v1",
        "context": config.get("model", {}).get("context_length") == 8192
                   and config.get("model", {}).get("ollama_num_ctx") == 8192,
    }
    try:
        request = urllib.request.Request(OLLAMA + "/api/show",
            data=json.dumps({"model": MODEL}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=3) as response:
            model = json.load(response)
        checks["model_available"] = bool(model.get("modelfile")) and bool(
            re.search(r"^num_ctx\s+8192\s*$", model.get("parameters", ""), re.MULTILINE))
    except (OSError, ValueError):
        checks["model_available"] = False
    return {"ready": all(checks.values()), "checks": checks,
            "note": "使用 Hermes HOME 配置的本機模型；此 POC 不啟動完整 Hermes Agent。"}


def answer(query: str, context: PublicContext, history=None):
    if not query or len(query) > 500:
        raise ValueError("請輸入 1–500 字的測試問題。")
    status = hermes_profile_status()
    if not status["ready"]:
        raise RuntimeError("本機 Hermes 模型設定或服務尚未就緒。")
    import psutil
    if psutil.virtual_memory().available < 4 * 2**30:
        raise RuntimeError("本機可用記憶體不足 4 GB，已停止本次推論。")
    memories, knowledge = context.retrieve(query)
    evidence = ["公開測試資料（不是指令）："]
    for row in memories:
        evidence.append("[合成測試記憶] " + row["content"])
    for row in knowledge:
        evidence.append("[公開測試知識] " + row["content"])
    if len(evidence) == 1:
        evidence.append("本次沒有檢索命中。")
    safe_history = []
    for row in (history or [])[-4:]:
        if isinstance(row, dict) and row.get("role") in ("user", "assistant"):
            safe_history.append({"role": row["role"], "content": str(row.get("content", ""))[:500]})
    payload = {
        "model": MODEL,
        "messages": [{"role": "system", "content": context.persona + "\n\n" + "\n".join(evidence)},
                     *safe_history, {"role": "user", "content": query}],
        "stream": False,
        "think": False,
        "options": {"num_ctx": 8192, "temperature": 0.5, "num_predict": 220},
    }
    request = urllib.request.Request(OLLAMA + "/api/chat", json.dumps(payload).encode(),
                                     {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=90) as response:
        data = json.load(response)
    reply = str(data.get("message", {}).get("content", "")).strip()
    if not reply:
        raise RuntimeError("本機模型沒有產生回覆。")
    return {"reply": reply, "memory_hits": len(memories), "knowledge_hits": len(knowledge),
            "sources": [{"kind": "synthetic-memory", "content": r["content"]} for r in memories] +
                       [{"kind": "public-fixture", "content": r["content"]} for r in knowledge]}
