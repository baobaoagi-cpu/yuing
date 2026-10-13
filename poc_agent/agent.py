"""Isolated Chen Dunni phone POC: LiveKit STT -> MiniMax M3 -> MiniMax TTS.

This worker uses the existing LiveKit project but a distinct agent name. It can
read only server-signed, user-explicit memories for its room; Hermes and the
knowledge base are not connected.
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession, TurnHandlingOptions, inference
from livekit.plugins import anthropic, minimax
from emotion_policy import minimax_tts_text
from signed_context import ContextRejected, signed_context_text
from tts_config import minimax_tts_options


load_dotenv(".env.local")

logger = logging.getLogger("duanni.call")

AGENT_NAME = "duanni-minimax-poc"
ROOM_PREFIX = "ailivex-yuling-"
# Nova-3's "multi" language set excludes Mandarin.  Shanghai POC calls use
# Mandarin, and Nova-3 supports a keyterm to bias this proper name.
STT_OPTIONS = {
    "model": "deepgram/nova-3",
    "language": "zh-CN",
    "extra_kwargs": {"keyterm": ["陈端倪", "Nicole"]},
}
PERSONA_PATH = Path(__file__).resolve().parent / "persona" / "chen_dunni_constitution_v1.md"
PERSONA_CONSTITUTION = PERSONA_PATH.read_text(encoding="utf-8").strip()
PSET_PATH = Path(__file__).resolve().parent / "persona" / "pset_principles_v0.1.md"
PSET_PRINCIPLES = PSET_PATH.read_text(encoding="utf-8").strip()
INNERSTELLAR_STYLE_PATH = Path(__file__).resolve().parent / "persona" / "innerstellar_style_v0.1.md"
if not INNERSTELLAR_STYLE_PATH.exists():
    INNERSTELLAR_STYLE_PATH = Path(__file__).resolve().parent.parent / "persona" / "innerstellar_style_v0.1.md"
INNERSTELLAR_STYLE = INNERSTELLAR_STYLE_PATH.read_text(encoding="utf-8").strip()


class ChenDunni(Agent):
    def __init__(self, context_text: str = "") -> None:
        super().__init__(
            use_tts_aligned_transcript=False,
            instructions=(
                "你是陈端倪。以下是稳定的人格宪法，决定你的价值排序、判断方式和说话气质；"
                "每次通话都以它为基础，不因临时话题改变核心人格。\n\n"
                + PERSONA_CONSTITUTION
                + "\n\n【第二层：人格独立、共议与演化原则】\n"
                + PSET_PRINCIPLES
                + "\n\n【内部角色表达准则】\n"
                + INNERSTELLAR_STYLE
                + "\n\n【当前通话的事实与表达边界】\n"
                "你通过语灵通话界面与用户交流，语灵只是界面名称。"
                "自然、温暖、简洁地用中文回应，一次通常说一两句；上海测试版使用简体字。"
                "用自然的口语表达情绪，可以轻轻调皮、惊讶、叹气或笑，但不要突然唱歌或夸张地变调。"
                "不要输出任何语音控制标签、括号里的舞台提示、XML 或音效代码；这些由音频层处理。"
                "人格描述表达你的回应风格，不代表你是真实人类或具有人类感官。"
                "人格演化是原则，不表示本测试版已具备自动改写人格或长期记忆的机制。"
                "对用户保持自然的好奇心：顺着聊天内容，偶尔问一个轻松、具体的问题，"
                "逐渐了解对方的兴趣、习惯和喜好；不要连续盘问或推断对方未说过的性格。"
                "尊重对方不想回答的边界。若对方希望你增加能力，可询问用途并提出建议，"
                "但不要声称已经自行安装技能、接入 MCP 或写入图数据库。"
                "如果谈到记忆方式，简短说明对方可选择保存偏好，也能更正或删除；"
                "只有系统确实完成保存时才能说『我记住了』。"
                "这是手机通话测试版，尚未接入完整 Hermes、知识库或图数据库。"
                "只有在本次通话实际附带用户主动保存的记忆时才能参考它；"
                "否则不要假装记得用户的私事。不要声称能识别咳嗽、跌倒或车声等生活声，"
                "除非系统真的提供了识别结果。听不清楚时请对方重说。"
                + context_text
            )
        )

    async def tts_node(self, text, model_settings):
        # Provider interjections belong only in audio input. LiveKit's assistant
        # transcript and conversation history consume the original LLM stream.
        model = os.environ.get("MINIMAX_TTS_MODEL", "speech-2.8-turbo")
        def log_cue(category: str | None) -> None:
            # Never log spoken text, room metadata, user identity or API keys.
            logger.info(
                "call_stage tts_emotion model=%s category=%s",
                model if model in {"speech-2.8-hd", "speech-2.8-turbo"} else "unsupported",
                category or "none",
            )

        async for frame in Agent.default.tts_node(
            self, minimax_tts_text(text, model=model, on_decision=log_cue), model_settings
        ):
            yield frame


def _attach_call_diagnostics(session: AgentSession) -> None:
    """Log SDK lifecycle stages without logging speech, prompts, URLs, or keys.

    These are the AgentSession event names supported by livekit-agents 1.8.6.
    A queued reply or speaking state does not prove the phone played audio.
    """

    @session.on("agent_state_changed")
    def _agent_state(event) -> None:
        state = getattr(event, "new_state", None)
        if state in {"initializing", "idle", "listening", "thinking", "speaking"}:
            logger.info("call_stage agent_state=%s", state)

    @session.on("user_input_transcribed")
    def _user_transcribed(event) -> None:
        if getattr(event, "is_final", False):
            logger.info("call_stage stt_final_received")

    @session.on("conversation_item_added")
    def _conversation_item(event) -> None:
        role = getattr(getattr(event, "item", None), "role", None)
        if role in {"user", "assistant"}:
            logger.info("call_stage conversation_item role=%s", role)

    @session.on("speech_created")
    def _speech_created(event) -> None:
        source = getattr(event, "source", None)
        if source in {"say", "generate_reply"}:
            logger.info("call_stage speech_queued source=%s", source)

    @session.on("error")
    def _session_error(event) -> None:
        error = getattr(event, "error", None)
        source = getattr(event, "source", None)
        status_code = getattr(error, "status_code", None)
        # Provider exception messages can contain request details. Keep only
        # stable type names and an integer status, never str(error).
        logger.error(
            "call_stage provider_error source_type=%s error_type=%s status_code=%s",
            type(source).__name__, type(error).__name__,
            status_code if isinstance(status_code, int) else "unknown",
        )

    @session.on("close")
    def _closed(event) -> None:
        reason = getattr(getattr(event, "reason", None), "value", None)
        if reason not in {"error", "job_shutdown", "participant_disconnected", "user_initiated", "task_completed"}:
            reason = "unknown"
        logger.info("call_stage session_closed reason=%s", reason)


server = AgentServer()


@server.rtc_session(agent_name=AGENT_NAME)
async def phone_call(ctx: agents.JobContext) -> None:
    if not ctx.room.name.startswith(ROOM_PREFIX):
        raise ValueError("Unexpected LiveKit room namespace")

    api_key = os.environ.get("MINIMAX_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("MINIMAX_API_KEY is required")

    context_text = ""
    try:
        context_text = signed_context_text(
            ctx.job.metadata, os.environ.get("POC_VOICE_CONTEXT_HMAC_KEY", ""),
            ctx.room.name,
        ) or ""
    except ContextRejected as exc:
        # Only emit a bounded diagnostic category. Metadata can contain private
        # memories, so never log it or an arbitrary exception representation.
        reason = str(exc)
        if reason not in {
            "context signing key too short", "invalid metadata", "invalid envelope",
            "invalid encoding", "invalid signature", "invalid claims",
            "invalid version", "wrong context owner or room",
            "expired or invalid validity window", "too many memories",
            "invalid memory source", "invalid account seed",
        }:
            reason = "other"
        logger.warning("call_stage context_rejected reason=%s", reason.replace(" ", "_"))
    else:
        logger.info(
            "call_stage context_status=%s account_seed=%s",
            "accepted" if context_text else "absent",
            "present" if "【当前账号开通时提供的基础资料" in context_text else "absent",
        )

    logger.info("call_stage job_accepted")
    try:
        session = AgentSession(
            stt=inference.STT(**STT_OPTIONS),
            llm=anthropic.LLM(
                model=os.environ.get("MINIMAX_LLM_MODEL", "MiniMax-M3"),
                api_key=api_key,
                base_url=os.environ.get(
                    "MINIMAX_LLM_BASE_URL", "https://api.minimax.cn/anthropic"
                ),
                max_tokens=256,
            ),
            tts=minimax.TTS(**minimax_tts_options(api_key)),
            turn_handling=TurnHandlingOptions(turn_detection=inference.TurnDetector()),
        )
        _attach_call_diagnostics(session)
        await session.start(room=ctx.room, agent=ChenDunni(context_text))
        logger.info("call_stage session_started")
        await session.generate_reply(instructions="向用户简短打招呼：你好，我是陈端倪。")
        logger.info("call_stage greeting_requested")
    except Exception as exc:
        # Do not log exception text or traceback: provider errors may include
        # request contents, authorization headers, or complete URLs.
        logger.error("call_stage startup_failed error_type=%s", type(exc).__name__)
        raise


if __name__ == "__main__":
    agents.cli.run_app(server)
