"""Isolated Chen Dunni phone POC: LiveKit STT -> MiniMax M3 -> MiniMax TTS.

This worker uses the existing LiveKit project but a distinct agent name. It can
read only server-signed, user-explicit memories for its room; Hermes and the
knowledge base are not connected.
"""

import asyncio
import hashlib
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession, TurnHandlingOptions, inference
from livekit.plugins import anthropic, minimax
from emotion_policy import minimax_tts_text
from call_usage_meter import UsageLedgerError, send_signed_receipts
from signed_context import ContextRejected, signed_context_text, verified_context_claims
from tts_config import minimax_tts_options
from visual_emotion import VisualEmotionEmitter


load_dotenv(".env.local")

logger = logging.getLogger("duanni.call")

AGENT_NAME = "duanni-minimax-poc"
ROOM_PREFIX = "ailivex-yuling-"
# Nova-3's "multi" language set excludes Mandarin.  Shanghai POC calls use
# Mandarin. Only the agent's name is global; a user's name must come from the
# authenticated, room-bound account context rather than biasing every STT turn.
STT_OPTIONS = {
    "model": "deepgram/nova-3",
    "language": "zh-CN",
    "extra_kwargs": {"keyterm": ["陈端倪"]},
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
    def __init__(self, context_text: str = "", visual_emotion: VisualEmotionEmitter | None = None) -> None:
        self.visual_emotion = visual_emotion
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
                "只有在本次通话实际附带登录账号的记忆或近期对话片段时才能参考它；"
                "近期逐字稿可能有误，不等于已确认的个人事实或永久记忆。"
                "不要根据语音辨识出的名字猜当前用户是谁；以本次签名的登录账号资料为准。"
                "如果对方自称与账号资料不一致，先询问，不要把两个人的记忆合并。"
                "未提供的往事不要假装记得。不要声称能识别咳嗽、跌倒或车声等生活声，"
                "除非系统真的提供了识别结果。听不清楚时请对方重说。"
                + context_text
            )
        )

    async def tts_node(self, text, model_settings):
        # Provider interjections belong only in audio input. LiveKit's assistant
        # transcript and conversation history consume the original LLM stream.
        if self.visual_emotion:
            self.visual_emotion.start_reply()
        model = os.environ.get("MINIMAX_TTS_MODEL", "speech-2.8-turbo")
        def log_cue(category: str | None) -> None:
            # Never log spoken text, room metadata, user identity or API keys.
            logger.info(
                "call_stage tts_emotion model=%s category=%s",
                model if model in {"speech-2.8-hd", "speech-2.8-turbo"} else "unsupported",
                category or "none",
            )

        async for frame in Agent.default.tts_node(
            self, minimax_tts_text(
                text, model=model, on_decision=log_cue,
                on_source_part=self.visual_emotion.on_assistant_part if self.visual_emotion else None,
            ), model_settings
        ):
            yield frame


def _attach_call_diagnostics(session: AgentSession, call_ref: str) -> None:
    """Log SDK lifecycle stages without logging speech, prompts, URLs, or keys.

    These are the AgentSession event names supported by livekit-agents 1.8.6.
    A queued reply or speaking state does not prove the phone played audio.
    """
    counts = {
        "stt_final": 0, "assistant_item": 0, "speech_queued": 0,
        "speech_done": 0, "speech_interrupted": 0, "speech_error": 0,
        "false_interruption": 0, "provider_error": 0,
    }

    @session.on("agent_state_changed")
    def _agent_state(event) -> None:
        state = getattr(event, "new_state", None)
        if state in {"initializing", "idle", "listening", "thinking", "speaking"}:
            logger.info("call_stage agent_state=%s", state)

    @session.on("user_input_transcribed")
    def _user_transcribed(event) -> None:
        if getattr(event, "is_final", False):
            counts["stt_final"] += 1
            logger.info("call_stage stt_final_received call_ref=%s count=%d",
                        call_ref, counts["stt_final"])

    @session.on("conversation_item_added")
    def _conversation_item(event) -> None:
        role = getattr(getattr(event, "item", None), "role", None)
        if role in {"user", "assistant"}:
            logger.info("call_stage conversation_item call_ref=%s role=%s", call_ref, role)
            if role == "assistant":
                counts["assistant_item"] += 1

    @session.on("speech_created")
    def _speech_created(event) -> None:
        source = getattr(event, "source", None)
        if source in {"say", "generate_reply"}:
            handle = event.speech_handle
            counts["speech_queued"] += 1
            logger.info("call_stage speech_queued call_ref=%s source=%s speech_id=%s count=%d",
                        call_ref, source, handle.id, counts["speech_queued"])

            def _speech_done(done_handle) -> None:
                # The pinned LiveKit 1.8.6 SpeechHandle reports generation
                # failures through exception() once its callback fires.
                if done_handle.exception() is not None:
                    outcome = "error"
                elif done_handle.interrupted:
                    outcome = "interrupted"
                else:
                    outcome = "done"
                counts[f"speech_{outcome}"] += 1
                logger.info("call_stage speech_terminal call_ref=%s speech_id=%s outcome=%s",
                            call_ref, done_handle.id, outcome)

            handle.add_done_callback(_speech_done)

    @session.on("agent_false_interruption")
    def _false_interruption(event) -> None:
        # Observe the event without retaining anything the caller said.
        counts["false_interruption"] += 1
        logger.info("call_stage false_interruption call_ref=%s resumed=%s",
                    call_ref, bool(getattr(event, "resumed", False)))

    @session.on("error")
    def _session_error(event) -> None:
        counts["provider_error"] += 1
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
        logger.info("call_stage session_closed call_ref=%s reason=%s", call_ref, reason)
        logger.info(
            "call_stage turn_summary call_ref=%s stt_final=%d assistant_item=%d speech_queued=%d "
            "speech_done=%d speech_interrupted=%d speech_error=%d "
            "false_interruption=%d provider_error=%d",
            call_ref,
            *(counts[key] for key in (
                "stt_final", "assistant_item", "speech_queued", "speech_done",
                "speech_interrupted", "speech_error", "false_interruption", "provider_error",
            )),
        )


server = AgentServer()


@server.rtc_session(agent_name=AGENT_NAME)
async def phone_call(ctx: agents.JobContext) -> None:
    if not ctx.room.name.startswith(ROOM_PREFIX):
        raise ValueError("Unexpected LiveKit room namespace")

    api_key = os.environ.get("MINIMAX_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("MINIMAX_API_KEY is required")

    context_text = ""
    verified_owner_id = None
    try:
        claims = verified_context_claims(
            ctx.job.metadata, os.environ.get("POC_VOICE_CONTEXT_HMAC_KEY", ""),
            ctx.room.name,
        )
        verified_owner_id = claims["owner_id"] if claims else None
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
            "call_stage context_status=%s account_name=%s account_seed=%s",
            "accepted" if context_text else "absent",
            "present" if "【本次已登录账号的称呼】" in context_text or
                         "【已验证的账号称呼】" in context_text else "absent",
            "present" if "【当前账号开通时提供的基础资料" in context_text else "absent",
        )

    logger.info("call_stage job_accepted")
    visual_emotion = VisualEmotionEmitter(ctx.room, ctx.room.name)
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
            turn_handling=TurnHandlingOptions(
                turn_detection=inference.TurnDetector(),
                interruption={
                    "enabled": True,
                    "min_duration": 0.5,
                    "false_interruption_timeout": 2.0,
                    "resume_false_interruption": True,
                },
                preemptive_generation={"enabled": True, "preemptive_tts": False},
            ),
        )
        # Stable for one call, opaque in logs, and independent of account names.
        call_ref = hashlib.sha256(ctx.room.name.encode()).hexdigest()[:12]
        _attach_call_diagnostics(session, call_ref)
        usage_endpoint = os.environ.get("POC_USAGE_ENDPOINT", "").strip()
        usage_key = os.environ.get("POC_USAGE_HMAC_KEY", "").strip()
        usage_task = None

        async def _report_usage() -> None:
            # Only a room-bound, authenticated owner can be billed. Never take
            # this identity from a transcript or the browser's LiveKit name.
            if not verified_owner_id or not usage_endpoint or len(usage_key.encode()) < 32:
                logger.info("call_stage usage_receipt=unconfigured")
                return
            try:
                count = await asyncio.to_thread(
                    send_signed_receipts, endpoint=usage_endpoint,
                    verified_owner_id=verified_owner_id, room=ctx.room.name,
                    model_usage=session.usage.model_usage, secret=usage_key,
                    timeout=2,
                )
            except UsageLedgerError:
                logger.warning("call_stage usage_receipt=unavailable")
            else:
                logger.info("call_stage usage_receipt=accepted models=%d", count)

        @session.on("close")
        def _usage_on_close(_event) -> None:
            nonlocal usage_task
            if usage_task is None:
                usage_task = asyncio.create_task(_report_usage())

        async def _usage_on_shutdown(_reason: str) -> None:
            # The LiveKit SDK runs shutdown callbacks together. A call that
            # closes after this callback started can still have a pending send.
            if usage_task is not None:
                await usage_task
            else:
                await _report_usage()

        ctx.add_shutdown_callback(_usage_on_shutdown)
        await session.start(room=ctx.room, agent=ChenDunni(context_text, visual_emotion))
        visual_emotion.ready = True
        async def _close_visual_emotion(_reason: str) -> None:
            await visual_emotion.close()

        ctx.add_shutdown_callback(_close_visual_emotion)
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
