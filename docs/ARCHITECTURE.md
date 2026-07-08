# 架構文件

本套件在同一個前端頁面裡提供**兩個獨立模組**，各走不同管線。

---

## 模組 A：即時語音對話

即時雙向語音——使用者用麥克風跟 AI 對話。走 **LiveKit（WebRTC）+ 常駐語音 agent**。

```
前端 PWA
  │ ① POST /api/yuling/token（帶 access secret）
  ▼
Backend（Flask）── 簽 LiveKit token、派 agent 進房
  │ ② 前端拿 token 連進 LiveKit（WebRTC）
  ▼
LiveKit Cloud ── 把語音 agent 派進同一房
  ▼
語音 Agent（LiveKit worker）
  麥克風 → Deepgram STT → LLM（Claude/MiniMax）→ MiniMax TTS → 播回
  （角色/記憶來自 character_provider）
```

**端點**：`POST /api/yuling/token` → `{token, url, room, identity, agent}`

---

## 模組 B：AI 會議記錄

AI 當記錄員——聽真實會議、即時逐字稿、產會議記要、存查歷史。
**不走 LiveKit、不派 agent**，是「瀏覽器 PCM → 後端 STT → LLM 摘要」的另一條管線。

```
前端（會議模式）
  麥克風 → WebAudio（PCM Int16）→ 分塊佇列
   ├─ 主路：POST /meetings/stream/{id}/append → 後端 STT（Deepgram/Soniox）
   └─ 備路：瀏覽器 SpeechRecognition（容錯）
   → 輪詢 /meetings/stream/{id}/events 取即時逐字稿
   → POST /meetings/minute-note → LLM 產會議記要
   → /stop、/finalize、/meetings（歷史）
```

**端點**：
| 端點 | 方法 | 作用 |
|------|------|------|
| `/api/yuling/meetings/stream/start` | POST | 開串流 session |
| `/api/yuling/meetings/stream/{id}/append` | POST | 上傳 PCM 分塊 |
| `/api/yuling/meetings/stream/{id}/events` | GET | 取即時逐字稿 |
| `/api/yuling/meetings/stream/{id}/stop` | POST | 停止串流 |
| `/api/yuling/meetings/live-stt` | POST | 即時 STT |
| `/api/yuling/meetings/chunks` | POST | 音訊分塊上傳 |
| `/api/yuling/meetings/minute-note` | POST | LLM 產會議記要 |
| `/api/yuling/meetings/finalize` | POST | 定稿 |
| `/api/yuling/meetings` | GET | 歷史清單 |
| `/api/yuling/meetings/{id}` | GET | 單場明細 |

---

## 兩個可插拔接縫
- **`backend/character_provider.py`** — AI 的靈魂（system prompt + voice_id）與記憶。兩個模組共用同一角色。
- **`backend/storage.py`** — 通話 / 會議的持久化。

> 註：端點路徑的 `yuling` 前綴是本專案沿用的舊名，可自行全域改名。
