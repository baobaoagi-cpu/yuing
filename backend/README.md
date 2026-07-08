# Backend — 語音 token + 會議記錄後端

Flask 後端，服務兩個模組：

- **模組 A（即時通話）**：`POST /api/yuling/token` 簽 LiveKit token、access secret 把關、派語音 agent 進房。
- **模組 B（會議記錄）**：`/api/yuling/meetings/*` 一系列端點（串流上傳 PCM、即時逐字稿、產會議記要、歷史）。

## 檔案
| 檔案 | 作用 |
|------|------|
| `app.py` | Flask 進入點，掛 blueprint + 服務前端 |
| `routes.py` | 兩個模組的所有端點（原 `routes/yuling.py`，已去識別化） |
| `deepgram_stt.py` | 會議 STT（Deepgram 包裝，可換） |
| `meeting_live_stt.py` | 即時會議 STT 管理器 |
| `storage.py` | ★ **儲存層骨架**（檔案式）——通話/會議的存取，可換成你的 DB |
| `character_provider.py` | ★ **角色靈魂骨架**——AI 是誰、記得什麼，接你的記憶系統 |

## 兩個「你要接自己東西」的接縫（seam）
1. **`character_provider.py`** — AI 的人格 + 記憶（`load_character` / `load_conversation`）。
   內附 `FileCharacterProvider`（讀 `config/character.md`）當範例。
2. **`storage.py`** — 通話/會議的持久化。內附檔案式最小實作，能跑；正式請換 SQLite/Postgres。

> 原專案把這兩個接縫都接到一個完整的長期記憶大腦，所以「語音裡」和「文字裡」是同一個 AI、同一份記憶。開源版把它們抽成可插拔介面。

## 執行
```bash
cp ../.env.example ../.env      # 填金鑰
pip install -r requirements.txt
python app.py                  # http://127.0.0.1:8080
```

## 注意
- LiveKit 三把金鑰：優先讀環境變數，退 `~/.ailivex_secrets.env`（見 routes.py）。
- `YULING_ACCESS_SECRET` 設了就強制檢查通話密語——**上線務必設**，否則陌生人可接通燒你的 LLM/TTS 錢。
- 這是**參考骨架**：核心流程完整、可讀可改；`storage.py` 的通話部分是最小佔位，會議部分可跑。
