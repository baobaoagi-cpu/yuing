# 陳端倪 MiniMax 通話 POC

這是獨立的 LiveKit Agent，使用原版語靈通話前端與現有 LiveKit 專案：

`手機 → LiveKit → Deepgram Nova-3 (LiveKit Inference) → MiniMax M3 → MiniMax Speech 2.8 Turbo → 手機`

LLM 與 TTS 共用同一把中國站 `MINIMAX_API_KEY`，只放在 Agent 服務端。沒有接入 Hermes、私人記憶、知識庫或圖資料庫。Agent 名稱固定為 `duanni-minimax-poc`，與現有 Builder Agent 隔離。

陳端倪的人格憲法固定在 `persona/chen_dunni_constitution_v1.md`，每次建立通話 Agent 時作為底層 system instructions 載入。改動須新增版本並核對差異；目前的能力限制另外追加，不以人格描述冒充已接入的感知或記憶功能。這份檔案屬於此 MiniMax POC，原 Builder Agent 不會自動使用。

第二層原則篇是 `persona/pset_principles_v0.1.md`，固定接在核心人格之後、通話能力邊界之前。PSET 的「演化」是產品規則；此 POC 尚無自動修改人格檔或長期記憶的寫入流程。

## 本機

在倉庫根目錄建立 `poc_agent/.env.local`（不要提交），填入 `.env.example` 裡的 LiveKit 憑證與 MiniMax 金鑰。執行：

```bash
python3 -m venv .venv-poc
.venv-poc/bin/pip install -r poc_agent/requirements.txt
cd poc_agent && ../.venv-poc/bin/python agent.py dev
```

## Railway

建立第二個服務，指向本倉庫分支，Dockerfile 路徑設為 `poc_agent/Dockerfile`。環境變數填入上列憑證。新 Agent 確認已連上 LiveKit、完成通話測試後，再將原預覽服務的 `YULING_AGENT_NAME` 改為 `duanni-minimax-poc`。要回退只需把它改回原 Builder Agent 名稱；前端網址不變。

中國地區流暢性仍須用上海手機實測，包括頁面開啟、入房、STT、首字回應、語音首包、整句完成時間與通話斷線率。LiveKit 和目前的 STT 仍是跨境依賴，MiniMax 中國 API 成功不代表整通電話在上海流暢。
