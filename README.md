# Voice Companion Kit（語音夥伴套件）

> 一套**即時語音 AI 夥伴 + AI 會議記錄員**的開源骨架。
> 你插入自己的「角色靈魂」與 API 金鑰，就能得到一個會**即時語音對話**、也會**邊聽邊做會議記要**的語音 AI。
>
> 衍生自內部 `ailivex` 專案，已抽離所有私有金鑰與特定人格，做成通用可插拔骨架。

---

## 這套件包含兩個獨立模組

| | **模組 A：即時語音對話** | **模組 B：AI 會議記錄** |
|---|---|---|
| 做什麼 | 使用者用麥克風跟 AI **即時雙向語音對話** | AI 當**記錄員**：聽會議 → 即時逐字稿 → 產會議記要 |
| 傳輸 | LiveKit（WebRTC） | 瀏覽器 PCM 直傳後端（不走 LiveKit） |
| 有無語音 Agent | 有（常駐 worker） | 無 |
| AI 角色 | 對話夥伴 | 摘要/記錄者 |
| 產出 | 即時語音回話 | 逐字稿 + 會議記要 + 歷史 |

**兩個模組共用同一個前端頁面、同一套金鑰與同一個「角色」，但走不同管線。**

---

## 架構總覽

```
┌──────────────────────────────────────────────────────────┐
│  前端 PWA（frontend/index.html）                          │
│  ・LiveKit JS client   ・WebAudio 錄音                    │
└──────┬───────────────────────────┬───────────────────────┘
       │ 模組A: /api/voice/token    │ 模組B: /api/meetings/*
       ▼                            ▼
┌──────────────────────┐   ┌──────────────────────────────┐
│  Backend（Flask）     │   │  LiveKit Cloud（WebRTC）      │
│  ・簽 token / 存取控制│   │   ┌────────────────────────┐  │
│  ・meetings 端點      │   │   │  Voice Agent（常駐）    │  │
│  ・character_provider │──▶│   │  STT → LLM → TTS 迴圈   │  │
│    （← 你的角色靈魂） │   │   └────────────────────────┘  │
└──────────────────────┘   └──────────────────────────────┘
        │ 模組B 的 STT + 記要
        ▼
   Deepgram / Soniox STT  +  LLM 摘要
```

### 技術棧（預設）
| 環節 | 預設供應商 | 可換 |
|------|-----------|------|
| 即時傳輸 | LiveKit Cloud | — |
| STT 語音辨識 | Deepgram（`stt-rt-v4`）／Soniox 備援 | 任何 STT |
| LLM 對話/摘要 | Anthropic Claude Haiku ／ MiniMax M3 | 任何 LLM |
| TTS 語音合成 | MiniMax（`speech-02-turbo` + 你的克隆聲紋） | 任何 TTS |
| VAD 斷句 | Silero | — |

---

## 快速開始

```bash
# 1. 複製環境變數範本, 填入你的金鑰
cp .env.example .env
#    編輯 .env, 填 LiveKit / Deepgram / Anthropic / MiniMax 等金鑰

# 2. 定義你的 AI 角色 (它的靈魂)
cp config/character.example.md config/character.md
#    編輯 config/character.md = 你的角色 system prompt

# 3. 後端
cd backend && pip install -r requirements.txt && python app.py

# 4. 語音 Agent (模組 A 需要)
cd agent && pip install -r requirements.txt && python -m agent.main start

# 5. 前端: 用任何靜態伺服器 serve frontend/index.html (或 nginx)
```

---

## 給它一個「靈魂」：`character_provider`

這套件**故意不含任何特定人格**。AI 是誰、記得什麼，由你透過 `backend/character_provider.py` 這個介面提供：

```python
class CharacterProvider:
    def load_character(self, character_id) -> CharacterContext:
        # 回傳: system_prompt(靈魂) + voice_id(聲紋) + display_name
    def load_conversation(self, conv_id) -> ConversationContext:
        # 回傳: 對話歷史 + 關於使用者的長期記憶
```

- 內附 `FileCharacterProvider`：從 `config/character.md` 讀一個靜態角色（最小可跑範例）。
- 想要**有長期記憶、會成長**的角色？把這個介面接到你自己的記憶系統／資料庫／RAG。
- （原專案就是把這裡接到一個完整的長期記憶大腦，所以語音裡跟文字裡是「同一個 AI、同一份記憶」。）

---

## 存取控制

`YULING_ACCESS_SECRET`（通話密語）：設了之後，前端要帶對的密語才簽得到 token——**防止陌生人接通、燒你的 TTS/LLM 錢**。留空＝不檢查（僅供本地測試）。

---

## 部署

- `deploy/agent.service.example`：語音 Agent 的 systemd 範本（常駐 worker）。
- `deploy/nginx.conf.example`：前端 + 反向代理範本。
- 後端與 Agent 可各自容器化（見 `*/Dockerfile`）。

---

## ⚠️ 上線前必看（來自實戰教訓）

1. **每個角色用獨立的 LiveKit project**——不同角色共用會互搶 dispatch。
2. **MiniMax 用 Pay-as-you-go 餘額**——餘額不足會「接通即沒聲音」(錯誤 1008)，且不會報錯。
3. **聲紋/金鑰不要寫死在程式**——一律走 `.env`（本套件已遵守）。
4. **語音成本要另外計量**——即時語音直連 STT/LLM/TTS，容易失控，記得加用量監控。
5. **heavy 模組（VAD/DB）在 entrypoint 內初始化**——別 module-level 預載，會讓 worker 啟動超時。

---

## 授權

MIT License — 見 [LICENSE](LICENSE)。

*衍生自 ailivex。私有金鑰與人格已抽離。*
