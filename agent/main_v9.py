"""
ailivex-realtime-agent-v9 — 啟動入口

v8 + LLM floor-gate：發言權判斷（叫我/交棒/彼此聊）多人情境改 Haiku，regex 快路徑+fallback。

與 v1-v8 完全隔離：不同 agent_name（ailivex-realtime-v9）+ 獨立 Cloud Run 服務。
同一 image，啟動命令 override 成：python -m agent.main_v9 start

用法：
  python -m agent.main_v9 dev    # 本地開發
  python -m agent.main_v9 start  # 正式環境（Cloud Run）
"""
import os
from livekit.agents import cli, WorkerOptions
from agent.realtime_agent_v9 import entrypoint

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        agent_name="ailivex-realtime-v9",
        port=port,
        initialize_process_timeout=60.0,
        num_idle_processes=0,
        drain_timeout=30,
        shutdown_process_timeout=90.0,
    ))
