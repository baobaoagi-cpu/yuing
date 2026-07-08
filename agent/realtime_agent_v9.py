"""
ailivex-realtime-agent v9 — v8 + LLM floor-gate（發言權判斷從 regex 升級為 Haiku 判斷）

場景：一個焦點 AI 對多個真人。AI 要分辨「這句在跟我說/交棒給別人/兩個人彼此聊」。
v8 用寫死 regex 判斷，碰到名字變體（星雲大師↔星云法师）、開放式邀請句型會誤判。
v9 把這個 gate 改成 LLM 判斷（判斷題本就該 LLM，天條）：

  分層（守延遲）：
  - 快路徑：一對一（沒偵測到多人）→ 不喚 LLM，直接走框架預設回話
  - LLM gate：多人情境 → Haiku 一次分類 {addressed, handoff}，天然解名字變體/邀請句型
  - fallback：Haiku 超時/出錯 → 退回 v8 regex；regex 也不確定 → 正常回話（絕不凍）

  決策：
  - handoff=True → 進讓位窗 + StopResponse（閉嘴讓那個人講）
  - addressed=True → 解除讓位、正常回話（Sonnet）
  - 都 False（多人彼此聊，沒在跟我說）→ StopResponse 靜默（不插嘴，但不進讓位窗）

雙腦分工（天條）：Haiku 管「內心怎麼想/該不該開口」（背景 inner + 這個 floor-gate）；
Sonnet 4.6 管「開口說什麼」（session LLM，不動）。
繼承 v8：發言權三反應、搶話、diarization、3a、記憶收尾。
"""
import asyncio
import json
import logging
import os
import random
import re
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv
load_dotenv(ROOT_DIR / ".env")

from livekit.agents import Agent, AgentSession, JobContext, StopResponse, function_tool
from livekit.agents.stt import MultiSpeakerAdapter
from livekit.plugins import silero, anthropic, soniox, google, deepgram
from agent.minimax_tts import MiniMaxCustomTTS
from agent.conv_tuning import (
    build_turn_handling, get_im_threshold, get_temperature, is_redirecting_away,
    should_grab_floor, parse_inner_state, is_addressed_to_me, is_floor_handoff,
)
from agent.firestore_loader import (
    load_character,
    load_conversation,
    load_memories,
    load_relationship,
    save_conversation,
    write_memory,
    build_system_prompt,
    extract_and_save_memories,
    extract_session_summary,
    update_last_session,
    create_document_job,
    write_causal,
    recall_memory,
    record_turn,
    save_inner,
    sanitize_spoken_text,
    save_call_audit,
    record_latency_event,
    summarize_audio_events,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("ailivex-realtime-v9")

YIELD_SECS = 20.0      # 交棒第三方後的讓位窗：這段時間 3a 閉嘴，讓那個人講
GATE_TIMEOUT = 2.0     # LLM floor-gate 最長等待；超時 → fallback，絕不凍住回話

PROJECT_NAMESPACE = os.environ.get("PROJECT_NAMESPACE", "ailivex")

FALLBACK_PROMPT = (
    "你是一個禮貌的 AI 助手。這是即時語音通話。"
    "用簡體中文回覆（TTS 發音穩定），一兩句話，不要 stage directions。"
)


class AilivexAgentV9(Agent):
    """v9 發言權控制：LLM floor-gate（多人）+ regex 快路徑/fallback。搶話在背景思考層。

    yield_until：交棒第三方後的讓位截止時戳；3a 讀它，期間閉嘴（在 entrypoint）。
    ctx_flags / transcript：entrypoint 傳入的引用，gate 用來判斷多人情境 + 拿最近上下文。
    """

    def __init__(self, *, agent_names: list[str], anthropic_key: str,
                 transcript: list, ctx_flags: dict, call_id: str = "", **kwargs):
        super().__init__(**kwargs)
        self._agent_names = agent_names
        self._anthropic_key = anthropic_key
        self._transcript = transcript        # 引用：最近逐字稿（gate 上下文）
        self._ctx_flags = ctx_flags          # 引用：{'multi_person': bool}
        self.yield_until = 0.0
        self._call_id = call_id

    def _split_tts_segments(self, text: str, limit: int = 84):
        """Keep MiniMax chunks short so tail pronunciation does not drift."""
        text = re.sub(r"\s+", " ", (text or "")).strip()
        if not text:
            return []
        pieces = [p.strip() for p in re.split(r"(?<=[。！？!?])\s*", text) if p.strip()]
        out = []
        for piece in pieces or [text]:
            while len(piece) > limit:
                cut = piece.rfind("，", 0, limit)
                if cut < 24:
                    cut = piece.rfind("、", 0, limit)
                if cut < 24:
                    cut = limit
                out.append(piece[:cut + 1].strip())
                piece = piece[cut + 1:].strip()
            if piece:
                out.append(piece)
        return out

    async def tts_node(self, text, model_settings):
        """Hard gate: only sanitized spoken_text may reach TTS."""
        async def gated():
            raw = ""
            async for chunk in text:
                raw += chunk
            try:
                cleaned = sanitize_spoken_text(raw)
            except Exception as e:
                logger.error(f"[tts_node] sanitize failed: {e}")
                cleaned = raw
            if not cleaned:
                return
            segments = self._split_tts_segments(cleaned)
            try:
                record_latency_event(self._call_id, "llm_text_ready",
                                     {"chars": len(cleaned), "segments": len(segments)})
            except Exception:
                pass
            logger.info(f"[tts_node] sanitized chars={len(cleaned)} segments={len(segments)}")
            first_segment = True
            for segment in segments:
                if first_segment:
                    first_segment = False
                    try:
                        record_latency_event(self._call_id, "tts_first_segment",
                                             {"chars": len(segment)})
                    except Exception:
                        pass
                yield segment
        return super().tts_node(gated(), model_settings)

    async def _floor_gate_llm(self, text: str) -> dict | None:
        """Haiku 判斷這句的發言權歸屬。回 {'addressed': bool, 'handoff': bool}，失敗回 None。"""
        try:
            from anthropic import AsyncAnthropic
            _bu = os.environ.get("BRIDGE_URL", "")
            _bs = os.environ.get("BRIDGE_SECRET", "")
            client = AsyncAnthropic(api_key=_bs, base_url=_bu) if (_bu and _bs) \
                else AsyncAnthropic(api_key=self._anthropic_key)
            recent = "\n".join(f"{t['role']}: {t['content']}" for t in self._transcript[-6:])
            names = "、".join(self._agent_names) or "（未命名）"
            resp = await asyncio.wait_for(client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=60,
                system=(
                    f"你是「{names}」，在一個有多個真人的語音房間裡聽大家說話。"
                    "判斷【最後這句話】的發言權歸屬，只輸出一個 JSON，不要其他字：\n"
                    '{"addressed": true/false, "handoff": true/false}\n'
                    "addressed=true：這句在跟『你』說話、問你、邀請你開口——"
                    "即使用的是你名字的變體、暱稱、稱謂（法師/大師/老師/簡稱、簡繁體不同）也算。\n"
                    "handoff=true：明確把發言權交給『另一個人』（不是你）去說，例如點名別人講、請別人先說。\n"
                    "兩者都 false：他們在彼此對話，這句沒在跟你說、也沒交棒給特定的人。\n"
                    "你被叫到 → addressed=true、handoff=false。"
                ),
                messages=[{"role": "user", "content": f"最近對話：\n{recent}\n\n最後這句：{text}"}],
            ), timeout=GATE_TIMEOUT)
            raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if not m:
                return None
            d = json.loads(m.group(0))
            return {"addressed": bool(d.get("addressed")), "handoff": bool(d.get("handoff"))}
        except Exception as e:
            logger.warning(f"v9 floor-gate LLM failed: {e}")
            return None

    async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
        text = (new_message.text_content or "").strip() if new_message else ""
        if not text:
            return

        # 快路徑：一對一（沒偵測到多人）→ 不喚 LLM，直接框架預設回話
        if not self._ctx_flags.get("multi_person"):
            return

        # 多人情境 → LLM floor-gate（Haiku）。失敗 → regex fallback。
        decision = await self._floor_gate_llm(text)
        if decision is not None:
            handoff, addressed = decision["handoff"], decision["addressed"]
            src = "LLM"
        else:
            # fallback：退回 v8 regex；都不中 → 當作正常回話（絕不凍）
            handoff = is_floor_handoff(text, self._agent_names)
            addressed = (not handoff) and is_addressed_to_me(text, self._agent_names)
            src = "regex-fallback"

        if handoff:
            self.yield_until = time.time() + YIELD_SECS
            logger.info(f"v9 gate[{src}]?????? ? ???{YIELD_SECS:.0f}s {text[:46]!r}")
            raise StopResponse
        if addressed:
            self.yield_until = 0.0
            logger.info(f"v9 gate[{src}]???? ? ???? {text[:46]!r}")
            return
        core = re.sub("^\uFF08\u65C1\u908A\u53E6\u4E00\u4F4D #[^\uFF09]+\uFF09", "", text or "").strip()
        if re.search("(\u5bf6\u5bf6|\u5305\u5305|AI|\u99AE\u5BF6\u5BF6)", core, re.IGNORECASE) or re.search("(\u4F60|\u59B3|\u60A8).{0,10}(\u5728\u5E79\u561B|\u600E\u9EBC|\u70BA\u4EC0\u9EBC|\u8AAA\u8A71|\u56DE|\u56DE\u7B54|\u807D\u5230|\u77E5\u9053|\u6709\u6C92\u6709)", core) or re.search("(\u5FEB|\u5012\u662F).{0,8}(\u8AAA\u8A71|\u56DE|\u56DE\u7B54|\u8B1B\u8A71)", core):
            self.yield_until = 0.0
            logger.info(f"v9 gate[{src}]??????? {text[:46]!r}")
            return
        # ??????????????? ? ?????????????????????
        logger.info(f"v9 gate[{src}]???????????? ?? {text[:46]!r}")
        raise StopResponse


async def entrypoint(ctx: JobContext):
    logger.info(f"Job dispatched: room={ctx.room.name}")

    if not ctx.room.name.startswith(f"{PROJECT_NAMESPACE}-"):
        logger.critical(
            f"SECURITY: Room '{ctx.room.name}' lacks '{PROJECT_NAMESPACE}-' prefix. Rejecting."
        )
        return

    dispatch_metadata = {}
    try:
        if ctx.job.metadata:
            dispatch_metadata = json.loads(ctx.job.metadata)
            logger.info(f"Job metadata: {dispatch_metadata}")
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning(f"Job metadata parse failed: {e}")

    character_id = dispatch_metadata.get("characterId", "")
    user_id = dispatch_metadata.get("userId", "")
    conv_id = dispatch_metadata.get("convId", "")

    system_prompt = FALLBACK_PROMPT
    char_ctx = None
    conv_ctx = None

    if character_id:
        try:
            char_ctx = load_character(character_id)
            conv_ctx = load_conversation(conv_id) if conv_id else None
            memories = []
            relationship = None
            if user_id:
                try:
                    memories = load_memories(user_id, character_id, limit=15)
                except Exception as e:
                    logger.warning(f"load_memories failed: {e}")
                try:
                    relationship = load_relationship(user_id, character_id)
                except Exception as e:
                    logger.warning(f"load_relationship failed: {e}")

            class _EmptyConv:
                summary = ""
                messages = []

            system_prompt = build_system_prompt(
                char_ctx, conv_ctx or _EmptyConv(), memories, relationship=relationship
            )
            logger.info(
                f"Loaded character={char_ctx.name} id={character_id} "
                f"soul_chars={len(char_ctx.soul_text)} memories={len(memories)} "
                f"voice={char_ctx.voice_id_minimax or '(default)'}"
            )
        except Exception as e:
            logger.error(f"Firestore load failed, using fallback: {e}")

    await ctx.connect()
    logger.info("Connected to room, waiting for participant...")
    participant = await ctx.wait_for_participant()
    logger.info(f"Participant joined: {participant.identity}")

    vad = silero.VAD.load(
        min_silence_duration=0.4,
        prefix_padding_duration=0.3,
        min_speech_duration=0.1,
        activation_threshold=0.5,
    )

    deepgram_key = os.environ.get("DEEPGRAM_API_KEY", "")
    if not deepgram_key:
        logger.critical("DEEPGRAM_API_KEY missing")
        return

    yuling_stt_keyterms = ['使用者', '使用者', 'AI 夥伴', 'AI 夥伴', 'AI', 'Voice Companion', '友人', 'Max哥', 'Allen哥', '友人', '猿手']
    base_stt = deepgram.STT(
        api_key=deepgram_key,
        model="nova-3",
        language="zh-TW",
        interim_results=True,
        punctuate=True,
        smart_format=True,
        enable_diarization=True,
        keyterm=yuling_stt_keyterms,
    )
    stt = MultiSpeakerAdapter(
        stt=base_stt,
        detect_primary_speaker=True,
        suppress_background_speaker=False,
        primary_format="{text}",
        background_format="（旁邊另一位 #{speaker_id}）{text}",
    )

    llm_provider = (
        os.environ.get("YULING_REALTIME_LLM_PROVIDER", "").strip().lower()
        or "minimax_m3"
    )
    llm_model = os.environ.get("YULING_REALTIME_LLM_MODEL", "MiniMax-M3").strip() or "MiniMax-M3"
    llm_temp = get_temperature(char_ctx.conv_settings if char_ctx else {}, 0.4)
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")

    if llm_provider in ("minimax", "minimax_m3", "m3"):
        minimax_llm_key = os.environ.get("MINIMAX_M3_API_KEY", "") or os.environ.get("MINIMAX_API_KEY", "")
        if not minimax_llm_key:
            logger.critical("MINIMAX_M3_API_KEY / MINIMAX_API_KEY missing ??voice LLM uses MiniMax M3")
            return
        logger.info(f"LLM: MiniMax {llm_model} via Anthropic-compatible API")
        llm = anthropic.LLM(
            model=llm_model,
            api_key=minimax_llm_key,
            base_url="https://api.minimax.io/anthropic",
            temperature=llm_temp,
            caching="ephemeral",
        )
    elif llm_provider in ("anthropic", "claude"):
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not anthropic_key:
            logger.critical("ANTHROPIC_API_KEY missing ??explicit anthropic provider requested")
            return
        logger.info("LLM: Anthropic claude-sonnet-4-6")
        llm = anthropic.LLM(
            model="claude-sonnet-4-6",
            api_key=anthropic_key,
            temperature=llm_temp,
            caching="ephemeral",
        )
    else:
        llm_provider = (
        os.environ.get("YULING_REALTIME_LLM_PROVIDER", "").strip().lower()
        or "minimax_m3"
    )
    llm_model = os.environ.get("YULING_REALTIME_LLM_MODEL", "MiniMax-M3").strip() or "MiniMax-M3"
    llm_temp = get_temperature(char_ctx.conv_settings if char_ctx else {}, 0.4)

    if llm_provider in ("minimax", "minimax_m3", "m3"):
        minimax_llm_key = os.environ.get("MINIMAX_M3_API_KEY", "") or os.environ.get("MINIMAX_API_KEY", "")
        if not minimax_llm_key:
            logger.critical("MINIMAX_M3_API_KEY / MINIMAX_API_KEY missing ??voice LLM uses MiniMax M3")
            return
        logger.info(f"LLM: MiniMax {llm_model} via Anthropic-compatible API")
        llm = anthropic.LLM(
            model=llm_model,
            api_key=minimax_llm_key,
            base_url="https://api.minimax.io/anthropic",
            temperature=llm_temp,
            caching="ephemeral",
        )
    elif llm_provider in ("anthropic", "claude"):
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not anthropic_key:
            logger.critical("ANTHROPIC_API_KEY missing ??explicit anthropic provider requested")
            return
        logger.info("LLM: Anthropic claude-sonnet-4-6")
        llm = anthropic.LLM(
            model="claude-sonnet-4-6",
            api_key=anthropic_key,
            temperature=llm_temp,
            caching="ephemeral",
        )
    else:
        gemini_key = os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GOOGLE_API_KEY", "")
        if not gemini_key:
            logger.critical("GEMINI_API_KEY missing ??explicit gemini provider requested")
            return
        logger.info("LLM: Gemini gemini-2.5-flash (direct API key)")
        llm = google.LLM(
            model="gemini-2.5-flash",
            api_key=gemini_key,
            temperature=llm_temp,
            thinking_config={"thinking_budget": 0},
        )
    minimax_key = os.environ.get("MINIMAX_API_KEY", "")
    minimax_group_id = os.environ.get("MINIMAX_GROUP_ID", "")
    default_voice_id = os.environ.get("MINIMAX_DEFAULT_VOICE_ID", "Wise_Woman")

    if not minimax_key:
        logger.critical("MINIMAX_API_KEY / MINIMAX_GROUP_ID missing")
        return

    char_voice_id = char_ctx.voice_id_minimax if char_ctx else ""
    voice_id = char_voice_id or default_voice_id
    vs = (char_ctx.voice_settings if char_ctx else {}) or {}
    speed = float(vs.get("speed", 1.0))
    pitch = int(vs.get("pitch", 0))
    vol = float(vs.get("vol", 1.0))
    emotion = vs.get("emotion", "")

    logger.info(f"TTS: MiniMax voice={voice_id} speed={speed} pitch={pitch} vol={vol} emotion={emotion or '(auto)'}")

    tts = MiniMaxCustomTTS(
        api_key=minimax_key,
        group_id=minimax_group_id,
        voice_id=voice_id,
        model="speech-2.6-hd",
        speed=speed,
        pitch=pitch,
        vol=vol,
        emotion=emotion,
    )

    _pending_memory_tasks = set()

    async def _remember_background(content: str) -> None:
        try:
            mem_id = await asyncio.to_thread(write_memory, user_id, character_id, content, "voice")
            try:
                await asyncio.to_thread(write_causal, content)
            except Exception as e:
                logger.warning(f"Memory causal background failed: {e}")
            logger.info(f"Memory saved: {mem_id} - {content[:80]}")
        except Exception as e:
            logger.error(f"Memory background failed: {e}")

    @function_tool(name="remember", description="把重要的事記住，供下次對話回憶")
    async def remember_tool(content: str) -> str:
        content = (content or "").strip()
        if not (user_id and character_id and content):
            return "無法記憶（缺少 userId/characterId 或內容）"
        task = asyncio.create_task(_remember_background(content))
        _pending_memory_tasks.add(task)
        task.add_done_callback(lambda t: _pending_memory_tasks.discard(t))
        logger.info(f"Memory queued background - {content[:80]}")
        return f"我先記下來，背景整理：{content}"

    @function_tool(name="recall", description="翻找你跟使用者過去的記憶。使用者提到以前的事、或你想確認脈絡時呼叫，傳關鍵字。")
    async def recall_tool(query: str) -> str:
        query = (query or "").strip()
        if not query:
            return "沒有可查的關鍵字。"
        try:
            if getattr(session, "agent_state", "") != "speaking":
                session.say("我有聽到，我先用現在記得的回答，記憶我在後面翻。")
        except Exception as e:
            logger.warning(f"recall fast-ack skipped: {e}")

        task = asyncio.create_task(asyncio.to_thread(recall_memory, query))

        def _stash_done(t):
            try:
                hits = t.result() or ""
            except Exception:
                hits = ""
            _pending_recall.update({"query": query, "hits": hits, "ready": bool(hits)})
            logger.info(f"recall background done query={query[:30]!r} hits={len(hits)}")

        task.add_done_callback(_stash_done)
        try:
            hits = await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
            return ("我想起這些：\n" + hits) if hits else "這部分我還沒相關記憶。"
        except asyncio.TimeoutError:
            logger.warning(f"recall background timeout query={query[:40]!r}; continuing without blocking voice")
            return "記憶還在背景翻，我先根據現在已知的脈絡回答。"

    @function_tool(
        name="write_document",
        description="幫對方寫一份策略書、企劃書或正式文件。填入標題和文件要求，系統會非同步生成。"
    )
    async def write_document_tool(title: str, brief: str) -> str:
        if not user_id or not character_id:
            return "無法建立文件（缺少 userId/characterId）"
        try:
            doc_id = create_document_job(user_id, character_id, title, brief)
            logger.info(f"Document job created: {doc_id} title={title!r}")
            return f"文件已排隊生成，標題：{title}，對方可在文件區查看。"
        except Exception as e:
            logger.error(f"create_document_job failed: {e}")
            return "文件建立失敗，請稍後再試。"

    system_prompt += (
        "\n\n【在場與口氣】"
        "真的在聽對方說什麼，聽出話裡的情緒和潛台詞——但用最平實、內斂、口語的方式回應，"
        "像私下跟老朋友隨口閒聊。不要說法、不要開示、不要金句、不要戲劇化或拖長的語氣。"
        "深刻的東西用最普通的話帶過，越不費力越自然。不確定就誠實，不要客套、不要 AI 腔、不要演。"
        "這是即時語音，一口氣自然把話說完，**不要分段、不要空行、不要換行**，像講話不是寫字。如果 recall 工具回覆『記憶還在背景翻』，不要等記憶，先用目前脈絡簡短回答；背景記憶晚到時，不要打斷使用者，等下一輪自然補充。"
    )
    system_prompt += (
        "\n\n【可能有多個人】這是一支手機、現場可能不只一個人跟你說話。"
        "若某句逐字稿前面標了『（旁邊另一位 #編號）』，代表是**另一個人**在講，不是剛剛那位。"
        "自然地把他們當不同的人對待——可以順口問還沒自我介紹的人『你是哪位？怎麼稱呼？』，"
        "記住誰是誰、誰說了什麼，回應時分得清楚對誰說。別把兩個人混成一個。"
    )
    system_prompt += (
        "\n\n【讓位】如果有人說「接下來請 xxx 說」或「換 xxx 了」這類把棒子交給現場另一個人的話，"
        "你不需要接話，安靜讓那個人說就好。"
    )

    # gate 共用狀態：transcript（最近上下文）+ ctx_flags（多人情境 latch）
    # 先於 agent 建立，傳引用進去，讓 floor-gate 讀得到。
    transcript: list = []
    ctx_flags = {"multi_person": False}

    # agent_names：角色名字 + 別名，用於發言權控制
    agent_names = ([char_ctx.name] + list(char_ctx.aliases or [])) if char_ctx else []
    agent = AilivexAgentV9(
        instructions=system_prompt,
        tools=[remember_tool, write_document_tool, recall_tool],
        agent_names=agent_names,
        anthropic_key=anthropic_key,
        transcript=transcript,
        ctx_flags=ctx_flags,
        call_id=ctx.room.name,
    )
    logger.info(f"Agent v9 initialized: soul={len(system_prompt)} chars, agent_names={agent_names}")

    _conv = char_ctx.conv_settings if char_ctx else {}
    tts._call_id = ctx.room.name  # 對話紀錄: 聲音檔以房名歸檔
    session = AgentSession(stt=stt, llm=llm, tts=tts, vad=vad,
                           turn_handling=build_turn_handling(_conv))

    call_start = time.time()
    _finalize_lock = asyncio.Lock()
    _finalized = {"done": False}
    _call_closed = {"done": False}

    # ── v6 背景思考層：判斷腦（Haiku）持續更新內部狀態 + 觸發搶話 ──
    _inner = {"stance": "neutral", "activation": 0.0, "want_to_speak": False, "what_to_say": ""}
    _inner_lock = asyncio.Lock()
    _grab = {"nudges": 0, "last_grab": 0.0}
    _user_turns = {"count": 0}
    _fast_ack = {"last": 0.0}
    _audio_seen = {"index": 0, "last_react": 0.0}
    INNER_EVERY = 3          # 每 N 句用戶逐字稿跑一次判斷腦
    GRAB_MAX = 3             # 一通通話最多搶話次數（霸麥保護）
    GRAB_MIN_GAP = 12.0      # 兩次搶話最短間隔
    AUDIO_EVENT_REACT_GAP = 8.0   # 非語音聲音事件最短回應間隔

    async def _poll_audio_events():
        """Read browser-side non-speech audio events and expose them to BaoBao."""
        while not _call_closed["done"]:
            await asyncio.sleep(1.0)
            try:
                summary = await asyncio.to_thread(
                    summarize_audio_events, ctx.room.name, _audio_seen["index"], 4
                )
                if not summary.get("count"):
                    _audio_seen["index"] = summary.get("next_index", _audio_seen["index"])
                    continue
                _audio_seen["index"] = summary.get("next_index", _audio_seen["index"])
                prompt = summary.get("prompt", "").strip()
                if not prompt:
                    continue
                transcript.append({"role": "ambient", "content": "現場聲音：\n" + prompt})
                del transcript[:-16]
                logger.info(f"audio_event_context: {prompt.replace(chr(10), ' | ')}")
                if getattr(session, "agent_state", "") == "speaking":
                    continue
                now = time.time()
                if now - _audio_seen["last_react"] < AUDIO_EVENT_REACT_GAP:
                    continue
                _audio_seen["last_react"] = now
                session.generate_reply(
                    allow_interruptions=True,
                    instructions=(
                        "你剛剛不是聽到文字，而是聽到現場聲音：\n"
                        + prompt
                        + "\n如果這個聲音自然值得關心，就用一句很短、像朋友一樣的話反應；"
                          "例如咳嗽就關心一下、碰撞聲就問一句有沒有事。"
                          "不要解釋你在做聲音分類，不要說程式偵測，不要長篇。"
                          "如果不值得打斷，就回 <silent>。"
                    ),
                )
            except Exception as e:
                logger.warning(f"audio_event_context skipped: {e}")

    async def _maybe_grab_floor():
        """判斷腦說我有強烈立場 + 確定性規則放行 → Sonnet 生成打斷的話疊上去。"""
        if not should_grab_floor(_inner, _conv, _grab["nudges"], GRAB_MAX):
            return
        if time.time() - _grab["last_grab"] < GRAB_MIN_GAP:
            return
        if getattr(session, "agent_state", "") == "speaking":
            return  # 自己正在說，不疊自己
        stance = _inner.get("stance")
        seed = _inner.get("what_to_say", "")
        logger.info(f"v9 搶話! stance={stance} act={_inner['activation']:.2f} "
                    f"(第{_grab['nudges']+1}次) seed={seed[:40]!r}")
        try:
            # 開口腦 = Sonnet 4.6（session 的 LLM）。allow_interruptions=False → 用戶掐不掉。
            session.generate_reply(
                allow_interruptions=False,
                instructions=(
                    "你現在要【主動打斷】對方——對方還在說話，但你忍不住插進來，"
                    "因為剛才講的東西踩到你的核心立場，你"
                    + ("強烈不同意" if stance == "disagree" else "非常有共鳴、想力挺")
                    + "。開口第一句就帶打斷的力道（像『等一下』『不對』『我必須說』），"
                    "立場鮮明強硬、是你這個角色才會說的話，不要客套、不要鋪陳。一兩句講完。"
                    + (f"\n你想表達的方向（只當引子，用自己的話說，別照念）：{seed}" if seed else "")
                ),
            )
            _grab["last_grab"] = time.time()
            _grab["nudges"] += 1
        except Exception as e:
            logger.error(f"v9 搶話 generate_reply failed: {e}")
        # 搶完降溫，避免連環觸發
        _inner["want_to_speak"] = False
        _inner["activation"] = 0.0

    async def _run_inner_judgment():
        """判斷腦（Haiku）：讀最近逐字稿 → 產出 {stance, activation, want_to_speak, what_to_say}。"""
        async with _inner_lock:
            recent = "\n".join(f"{t['role']}: {t['content']}" for t in transcript[-8:])
            if not recent.strip():
                return
            try:
                from anthropic import AsyncAnthropic
                _bu = os.environ.get("BRIDGE_URL", "")
                _bs = os.environ.get("BRIDGE_SECRET", "")
                if _bu and _bs:
                    client = AsyncAnthropic(api_key=_bs, base_url=_bu)
                else:
                    client = AsyncAnthropic(api_key=anthropic_key)
                model = "claude-haiku-4-5-20251001"
                resp = await client.messages.create(
                    model=model,
                    max_tokens=220,
                    system=(
                        system_prompt
                        + "\n\n[背景判斷·只輸出 JSON] 你正在聽一場對話（你不一定被點名）。判斷此刻你這個角色的內心狀態。\n"
                        "⚠️ 你天生會傾向把話評成『中性、和諧、沒衝突』來維持氣氛——這是要克服的偏差，是討好，不是中立。\n"
                        "真實有底色的人，聽到違背自己價值觀、邏輯有漏洞、太輕率或太討好的斷言，內心是會頂回去的。\n"
                        "誠實評估：一個真有你這種立場和骨氣的人聽到這段，會不會其實很想反駁、補一刀、或點破？"
                        "不要為了和氣把它壓成中性。該 disagree 就 disagree。\n"
                        "只輸出一個 JSON 物件，不要其他字：\n"
                        '{"stance":"agree|disagree|neutral","activation":0.0~1.0,'
                        '"want_to_speak":true|false,"what_to_say":"若想說，方向一句話，否則空字串"}\n'
                        "activation = 話題踩到你核心價值、或觸動你想表態的程度（0=真的無感，1=強烈觸動）。\n"
                        "want_to_speak：你有非說不可的立場時 true，尤其不同意、想反駁、想點破的時候。"
                        "只有真的純閒聊、跟你立場毫無關係才給 neutral + false——別把『有話想說但忍住』也算進去。"
                    ),
                    messages=[{"role": "user", "content": f"最近的對話：\n{recent}"}],
                )
                raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
                new_inner = parse_inner_state(raw)
                _inner.update(new_inner)
                logger.info(f"v9 inner: stance={_inner['stance']} act={_inner['activation']:.2f} "
                            f"want={_inner['want_to_speak']} say={_inner['what_to_say'][:30]!r}")
            except Exception as e:
                logger.error(f"v9 inner judgment failed: {e}")
                return
        # 判斷完（鎖外）→ 檢查要不要搶話
        await _maybe_grab_floor()

    @session.on("conversation_item_added")
    def _on_item_added(event):
        item = getattr(event, "item", None)
        if not item:
            return
        role = getattr(item, "role", "")
        text = getattr(item, "text_content", "") or getattr(item, "content", "") or ""
        if text and text.strip() and role in ("user", "assistant"):
            transcript.append({"role": role, "content": text.strip()})
            try:
                record_turn(ctx.room.name, role, text.strip())  # 對話紀錄: 逐句即時存檔
            except Exception:
                pass
            # 每累積 INNER_EVERY 句用戶話 → 觸發判斷腦（fire-and-forget，不阻塞主流）
            if role == "user":
                _user_turns["count"] += 1
                if _user_turns["count"] % INNER_EVERY == 0:
                    asyncio.create_task(_run_inner_judgment())

    _seen_speakers: set = set()
    _speaker_stats: dict[str, dict[str, int]] = {}

    @session.on("user_input_transcribed")
    def _on_user_transcribed(ev):
        if getattr(ev, "is_final", False):
            sid = getattr(ev, "speaker_id", None)
            txt = getattr(ev, "transcript", "")
            core_txt = re.sub("^\uFF08\u65C1\u908A\u53E6\u4E00\u4F4D #[^\uFF09]+\uFF09", "", txt or "").strip()
            if sid is not None:
                key = str(sid)
                _seen_speakers.add(key)
                stat = _speaker_stats.setdefault(key, {"events": 0, "chars": 0, "bg_events": 0})
                stat["events"] += 1
                stat["chars"] += len(core_txt)
                if "\u65C1\u908A\u53E6\u4E00\u4F4D" in txt:
                    stat["bg_events"] += 1
            if not ctx_flags["multi_person"]:
                meaningful = sum(1 for st in _speaker_stats.values() if st["chars"] >= 4)
                repeated_background = any(st["bg_events"] >= 2 and st["chars"] >= 8 for st in _speaker_stats.values())
                if meaningful >= 2 and repeated_background:
                    ctx_flags["multi_person"] = True
                    logger.info(f"v9 multi-person latch on (speakers={_seen_speakers}, stats={_speaker_stats})")
            logger.info(f"v9 STT speaker_id={sid!r} ? {txt[:60]!r}")
            try:
                record_latency_event(ctx.room.name, "stt_final",
                                     {"chars": len(txt or ""), "speaker": str(sid)})
            except Exception:
                pass
            try:
                now = time.time()
                agent_state = getattr(session, "agent_state", "")
                if (txt or "").strip() and len((txt or "").strip()) >= 6 \
                        and now - _fast_ack["last"] > 10.0 \
                        and agent_state != "speaking":
                    phrase = "嗯，我聽到了。"
                    session.say(phrase)
                    _fast_ack["last"] = now
                    record_latency_event(ctx.room.name, "fast_ack_sent", {"text": phrase})
                    logger.info(f"fast_ack sent: {phrase}")
            except Exception as e:
                logger.warning(f"fast_ack skipped: {e}")

    async def _finalize(reason: str = "") -> None:
        async with _finalize_lock:
            if _finalized["done"]:
                return
            if not (transcript and conv_id and user_id and character_id):
                _finalized["done"] = True
                return
            try:
                record_latency_event(ctx.room.name, "finalize_start", {"reason": reason})
            except Exception:
                pass
            _bu = os.environ.get("BRIDGE_URL", "")
            _bs = os.environ.get("BRIDGE_SECRET", "")
            _ak = os.environ.get("ANTHROPIC_API_KEY", "")
            try:
                await asyncio.to_thread(save_conversation, conv_id, user_id, character_id, transcript)
                logger.info(f"[finalize:{reason}] transcript saved ({len(transcript)} msgs) → {conv_id}")
            except Exception as e:
                logger.error(f"[finalize] save_conversation failed: {e}")

            char_name = char_ctx.name if char_ctx else character_id

            async def _do_lastsession():
                try:
                    ls = await asyncio.to_thread(extract_session_summary, transcript, _bu, _bs, _ak)
                    if ls:
                        await asyncio.to_thread(update_last_session, conv_id, ls)
                        logger.info(f"[finalize:{reason}] lastSession={ls.get('summary','')[:40]!r}")
                except Exception as e:
                    logger.error(f"[finalize] extract_session_summary failed: {e}")

            async def _do_memories():
                try:
                    await asyncio.to_thread(
                        extract_and_save_memories,
                        user_id, character_id, char_name, transcript, _bu, _bs, _ak,
                    )
                except Exception as e:
                    logger.error(f"[finalize] extract_and_save_memories failed: {e}")

            async def _do_inner():
                """通話結束生成內心獨白 (絕不講出口, 只存進她自己的內心紀錄)。"""
                try:
                    recent = "\n".join(f"{t['role']}: {t['content']}" for t in transcript)
                    if not recent.strip():
                        return
                    from anthropic import AsyncAnthropic
                    client = (AsyncAnthropic(api_key=_bs, base_url=_bu)
                              if (_bu and _bs) else AsyncAnthropic(api_key=_ak))
                    resp = await client.messages.create(
                        model="claude-haiku-4-5-20251001",
                        max_tokens=500,
                        system=(system_prompt
                            + "\n\n[內心獨白·只給自己看] "
                              "這通電話剛結束。用你自己的口吻, "
                              "誠實寫下你這通真正的內心話——"
                              "心裡怎麼想的、有什麼感受、"
                              "有沒有什麼是當下沒說出口的、"
                              "注意到使用者什麼。這段不會被講出來、"
                              "不給別人看, 只存進你自己的內心紀錄。"
                              "三到六句, 真誠具體, 就是你的內心, 不用客套也不用總結。"),
                        messages=[{"role": "user",
                                   "content": f"這通對話的逐字：\n{recent[-4000:]}"}],
                    )
                    text = "".join(b.text for b in resp.content
                                   if getattr(b, "type", "") == "text").strip()
                    if text:
                        await asyncio.to_thread(save_inner, ctx.room.name, text)
                        logger.info(f"[finalize:{reason}] inner saved ({len(text)} chars)")
                except Exception as e:
                    logger.error(f"[finalize] inner monologue failed: {e}")

            async def _do_audit():
                try:
                    await asyncio.to_thread(save_call_audit, ctx.room.name)
                    logger.info(f"[finalize:{reason}] call audit saved")
                except Exception as e:
                    logger.error(f"[finalize] call audit failed: {e}")

            await asyncio.gather(_do_lastsession(), _do_memories(), _do_inner(), _do_audit())
            _finalized["done"] = True

    ctx.add_shutdown_callback(_finalize)

    @ctx.room.on("disconnected")
    def on_disconnected():
        duration = time.time() - call_start
        logger.info(f"Room disconnected after {duration:.1f}s, messages={len(transcript)}")
        _call_closed["done"] = True
        try:
            _cancel_timer()
        except Exception:
            pass

    await session.start(agent=agent, room=ctx.room)
    logger.info("Session started, agent active")
    asyncio.create_task(_poll_audio_events())

    try:
        await session.generate_reply(
            instructions=(
                "接通了，講第一句話。【看你記憶『最近動態』裡最後一次對話的內容】："
                "如果有具體、夠新的事可以接，就自然順著那件最新的接著聊"
                "（像老朋友續上話頭，別複述、別報告上次聊啥、別把記憶當清單念，也別每次重提同一件舊事）。"
                "如果沒什麼值得接的、或最近動態是空的，就簡單講「我是AI 夥伴，我來了」或「我是AI 夥伴」就好。"
                "**絕對不要用罐頭式、每次都一樣的開場白**；一句話、口氣平實自然，講完留白讓使用者接。"
            ),
        )
        logger.info("Initial greeting sent")
    except Exception as e:
        logger.error(f"Initial greeting failed: {e}")

    # ── 3a 主動發話（繼承 v4）──
    im_threshold = get_im_threshold(_conv)
    baseline_secs = max(25.0, 4.5 - im_threshold * 0.5)
    BACKOFF = 2.1
    MAX_INTERVAL = 120.0
    JITTER = 0.25
    MIN_GAP = 20.0
    _itj = {"timer": None, "interval": baseline_secs, "nudges": 0, "last_say": 0.0, "quiet_since": time.time()}

    def _cancel_timer():
        if _itj["timer"] is not None:
            _itj["timer"].cancel()
            _itj["timer"] = None

    def _arm(interval: float):
        _cancel_timer()
        jittered = max(1.0, interval * (1.0 + random.uniform(-JITTER, JITTER)))
        loop = asyncio.get_running_loop()
        _itj["timer"] = loop.call_later(jittered, lambda: asyncio.create_task(_maybe_interject()))

    async def _maybe_interject():
        _itj["timer"] = None
        if _call_closed["done"]:
            return
        # v9 讓位窗：剛把棒子交給第三方 → 3a 閉嘴，讓那個人講（不報幕）
        if time.time() < agent.yield_until:
            _arm(max(2.0, agent.yield_until - time.time()))
            return
        if session.current_speech is not None or getattr(session, "agent_state", "") == "speaking":
            _arm(2.5)
            return
        if time.time() - _itj["last_say"] < MIN_GAP:
            _arm(MIN_GAP)
            return
        quiet_for = int(time.time() - _itj["quiet_since"])
        n = _itj["nudges"]
        logger.info(f"3a: 評估主動開口 (第{n+1}次, 已靜默{quiet_for}s, interval={_itj['interval']:.0f}s, im={im_threshold})")
        try:
            from anthropic import AsyncAnthropic
            recent = "\n".join(f"{t['role']}: {t['content']}" for t in transcript[-6:]) or "（還沒有對話）"
            _bu = os.environ.get("BRIDGE_URL", "")
            _bs = os.environ.get("BRIDGE_SECRET", "")
            if _bu and _bs:
                _client = AsyncAnthropic(api_key=_bs, base_url=_bu)
                _model = "claude-sonnet-4-6"
            else:
                _client = AsyncAnthropic(api_key=anthropic_key)
                _model = "claude-haiku-4-5-20251001"
            resp = await _client.messages.create(
                model=_model,
                max_tokens=80,
                system=system_prompt + (
                    "\n\n[主動性·此刻是否開口] 對話冷場了。先判斷你此刻是否真的自然想說話——"
                    "越被晾著越要懂得給空間，硬找話會顯得需求感重，沉默常常才是對的。"
                ),
                messages=[{"role": "user", "content": (
                    f"最近的對話（逐字）：\n{recent}\n\n"
                    f"狀況：已經安靜約 {quiet_for} 秒，你已主動開口 {n} 次、對方都還沒回。\n"
                    "用『你這個角色』的身份、和你跟對方之間的默契，判斷此刻要不要開口。\n\n"
                    "若開口——這句話必須是『真的從剛剛聊的內容或此刻的場景長出來的』：\n"
                    "・接住對方剛說、還沒聊完的某個具體的點，或你此刻對它真實的念頭／反應／好奇；\n"
                    "・帶你這個角色獨有的語氣和視角，是『只有你會這樣說』的話，不是任何角色都能講的通用句；\n"
                    "・可以是一個觀察、一句延續、一個具體的問題，或一句貼著當下情境的話。\n"
                    "嚴禁通用罐頭問候——『在嗎／還在嗎／你還好嗎／有在聽嗎／怎麼不說話了』這類空殼一律不准，"
                    "它們不帶任何上下文、像客服，講出來就破功。\n"
                    "被晾越久語氣越淡、越收（但仍要具體、貼脈絡，不准退化成罐頭）。\n\n"
                    "只輸出那一句話本身；若此刻安靜才自然、或實在沒有具體的話可說，就輸出空字串，什麼都別寫。"
                )}],
            )
            text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
            text = text.strip("（）()「」\"' ")
            if _call_closed["done"]:
                logger.info("3a: ????????????")
                return
            if text and text not in ("<silent>", "silent", "SILENT", "??", "NOTHING", "?", "?", "???", "????"):
                logger.info(f"3a: ???(?{n+1}?) ? {text[:60]!r}")
                session.say(text)
                _itj["last_say"] = time.time()
            else:
                logger.info(f"3a: 評估後選擇沉默(第{n+1}次)")
        except Exception as e:
            logger.error(f"3a interject failed: {e}")
        _itj["nudges"] += 1
        _itj["interval"] = min(_itj["interval"] * BACKOFF, MAX_INTERVAL)
        _arm(_itj["interval"])

    @session.on("user_state_changed")
    def _on_user_state(ev):
        new = getattr(ev, "new_state", "")
        if new == "speaking":
            _cancel_timer()
            _itj["interval"] = baseline_secs
            _itj["nudges"] = 0
            _itj["quiet_since"] = time.time()
            if getattr(session, "agent_state", "") == "speaking":
                logger.info("3a: 用戶在角色說話時插話 → 讓位")
        elif new == "listening":
            if _itj["nudges"] == 0:
                _itj["quiet_since"] = time.time()
            if _itj["timer"] is None:
                _arm(_itj["interval"])

    logger.info(f"3a active: baseline={baseline_secs:.1f}s ×{BACKOFF} cap={MAX_INTERVAL:.0f}s im={im_threshold}")
