# Agent — 即時語音對話 Agent（模組 A）

基於 **LiveKit Agents** 的即時語音 worker。派進 LiveKit 房間後，跑「聽 → 想 → 說」迴圈：

```
麥克風(WebRTC) → Deepgram STT → LLM(Claude/MiniMax) → MiniMax TTS → 播回
```

## 檔案
| 檔案 | 作用 |
|------|------|
| `main_v9.py` | 進入點（`python -m agent.main_v9 start`） |
| `realtime_agent_v9.py` | 即時對話迴圈 v9（含 floor-gate 讓位、開場、STT keyterms 等） |
| `minimax_tts.py` | MiniMax 串流 TTS |
| `firestore_loader.py` | ★ 角色 + 記憶載入器（骨架版：`load_character` 接 `backend/character_provider`；記憶/持久化函式為安全 no-op，接你自己的系統） |

## 角色（靈魂）從哪來
Agent 呼叫 `firestore_loader.load_character()` 拿到角色的 **system prompt + 聲紋 voice_id**，
它轉接到 `backend/character_provider`（預設讀 `config/character.md`）。
要有「長期記憶、會成長」的角色 → 把 `firestore_loader` 裡的
`load_memories / write_memory / recall_memory / ...` 接到你的記憶系統 / RAG。

## 執行
```bash
cp ../.env.example ../.env      # 填 LiveKit / Deepgram / Anthropic / MiniMax
pip install -r requirements.txt
python -m agent.main_v9 start
```

## 上線注意（實戰教訓）
- **heavy 模組（VAD/DB/ML）在 entrypoint 內初始化**，別 module-level 預載——會讓 LiveKit worker 啟動超時而 crash。
- **每個角色用獨立的 LiveKit project**，別跟別的角色共用——agent 會互搶 dispatch。
- 聲紋 / 金鑰一律走 `.env`，別寫死在程式（本專案已遵守）。
