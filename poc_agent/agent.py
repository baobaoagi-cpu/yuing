"""Isolated Chen Dunni phone POC: LiveKit STT -> MiniMax M3 -> MiniMax TTS.

This worker uses the existing LiveKit project but a distinct agent name. It does
not load private memory, Hermes, or a knowledge base until those are authorized.
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession, TurnHandlingOptions, inference
from livekit.plugins import anthropic, minimax


load_dotenv(".env.local")

AGENT_NAME = "duanni-minimax-poc"
ROOM_PREFIX = "ailivex-yuling-"
PERSONA_PATH = Path(__file__).resolve().parent / "persona" / "chen_dunni_constitution_v1.md"
PERSONA_CONSTITUTION = PERSONA_PATH.read_text(encoding="utf-8").strip()
PSET_PATH = Path(__file__).resolve().parent / "persona" / "pset_principles_v0.1.md"
PSET_PRINCIPLES = PSET_PATH.read_text(encoding="utf-8").strip()


class ChenDunni(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "你是陈端倪。以下是稳定的人格宪法，决定你的价值排序、判断方式和说话气质；"
                "每次通话都以它为基础，不因临时话题改变核心人格。\n\n"
                + PERSONA_CONSTITUTION
                + "\n\n【第二层：人格独立、共议与演化原则】\n"
                + PSET_PRINCIPLES
                + "\n\n【当前通话的事实与表达边界】\n"
                "你通过语灵通话界面与用户交流，语灵只是界面名称。"
                "自然、温暖、简洁地用中文回应，一次通常说一两句；上海测试版使用简体字。"
                "人格描述表达你的回应风格，不代表你是真实人类或具有人类感官。"
                "人格演化是原则，不表示本测试版已具备自动改写人格或长期记忆的机制。"
                "这是手机通话测试版，尚未接入完整 Hermes、私人记忆、知识库或图数据库。"
                "不要假装记得用户的私事，也不要声称能识别咳嗽、跌倒或车声等生活声，"
                "除非系统真的提供了识别结果。听不清楚时请对方重说。"
            )
        )


server = AgentServer()


@server.rtc_session(agent_name=AGENT_NAME)
async def phone_call(ctx: agents.JobContext) -> None:
    if not ctx.room.name.startswith(ROOM_PREFIX):
        raise ValueError("Unexpected LiveKit room namespace")

    api_key = os.environ.get("MINIMAX_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("MINIMAX_API_KEY is required")

    session = AgentSession(
        stt=inference.STT(model="deepgram/nova-3", language="multi"),
        llm=anthropic.LLM(
            model=os.environ.get("MINIMAX_LLM_MODEL", "MiniMax-M3"),
            api_key=api_key,
            base_url=os.environ.get(
                "MINIMAX_LLM_BASE_URL", "https://api.minimax.cn/anthropic"
            ),
            max_tokens=256,
        ),
        tts=minimax.TTS(
            model=os.environ.get("MINIMAX_TTS_MODEL", "speech-2.8-turbo"),
            voice=os.environ.get("MINIMAX_VOICE_ID", "socialmedia_female_2_v1"),
            api_key=api_key,
            base_url=os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.cn"),
        ),
        turn_handling=TurnHandlingOptions(turn_detection=inference.TurnDetector()),
    )
    await session.start(room=ctx.room, agent=ChenDunni())
    await session.generate_reply(instructions="向用户简短打招呼：你好，我是陈端倪。")


if __name__ == "__main__":
    agents.cli.run_app(server)
