# 陳端倪 MiniMax 通話 POC

這是獨立的 LiveKit Agent，使用原版語靈通話前端與現有 LiveKit 專案：

`手機 → LiveKit → Deepgram Nova-3 (LiveKit Inference) → MiniMax M3 → MiniMax Speech 2.8 Turbo → 手機`

STT 固定使用 Nova-3 的 `zh-CN` 普通話模式，並提示專名 `陈端倪`。Deepgram 的 `multi` 模式不包含中文；此設定僅由 SDK 建構測試確認可用，專名辨識改善仍須真人通話比較逐字稿。切換為單語模式後，混合語言發話也須另測。

LLM 與 TTS 共用同一把 `MINIMAX_API_KEY`，只放在 Agent 服務端。國際站測試使用 `https://api.minimax.io` 與 `https://api.minimax.io/anthropic`；已購點數的 Subscription Key 由使用者親自存入 Railway。沒有接入 Hermes、私人記憶、知識庫或圖資料庫。Agent 名稱固定為 `duanni-minimax-poc`，與現有 Builder Agent 隔離。

聲音設計候選提示詞與試聽文案在 `voice_design/chen_dunni_v1.md`。產生的聲音需經實際聆聽，再設定 `MINIMAX_VOICE_ID`；不能以 API 成功取代音色驗收。

陳端倪的人格憲法固定在 `persona/chen_dunni_constitution_v1.md`，每次建立通話 Agent 時作為底層 system instructions 載入。改動須新增版本並核對差異；目前的能力限制另外追加，不以人格描述冒充已接入的感知或記憶功能。這份檔案屬於此 MiniMax POC，原 Builder Agent 不會自動使用。

第二層原則篇是 `persona/pset_principles_v0.1.md`，固定接在核心人格之後、通話能力邊界之前。PSET 的「演化」是產品規則；此 POC 尚無自動修改人格檔或長期記憶的寫入流程。

## 通話與文字記憶的邊界

文字聊天和通話使用內容相同的兩份人格檔，但目前是兩條獨立的模型會話。文字聊天讀取登入帳號名下的 `ChatStore`；通話 Agent 不讀 LiveKit `identity`、`userId` 或 `convId` 當作身分證明。未啟用新功能時，`/api/yuling/token` 的舊版 `identity` 仍由瀏覽器提交，且 Agent 與 PWA 位於不同 Railway 服務、沒有共享 SQLite 檔案。網頁寫回的通話轉錄只供使用者查看，不代表 Agent 在通話中看到了文字聊天或長期記憶。

`signed_context.py` 是**預設關閉**的接收端：沒有 `POC_VOICE_CONTEXT_HMAC_KEY` 就不讀任何上下文。即使設定密鑰，仍只有同一把伺服器端密鑰簽署、綁定實際 LiveKit 房間、有效期不超過兩分鐘的封包才會進入提示詞；只接受 `user_explicit` 的明示記憶，最多八筆。舊版未簽署 metadata 一律沒有記憶。這份接收端本身沒有讓現在線上服務接上記憶，也**不要單獨在 Railway 設定此變數就宣稱接通**。

本機新增的簽發端在 `POC_VOICE_CONTEXT_ENABLED=1` 時才生效：PWA 驗證登入 cookie 與同源請求後，按 `session['account']` 查取該帳號的明示記憶，由伺服器建立 LiveKit 房間及 token，並把簽名封包放入 `roomConfig.agents[0].metadata.voice_context`。前端提交的 `identity`、`owner_id`、記憶內容或 metadata 不會選擇帳號。缺登入、簽名密鑰或儲存時拒絕簽發。兩服務需要同一個獨立的 HMAC 密鑰；目前 `/tmp` 資料會隨容器重建消失，仍須把 ChatStore 移到經驗證的持久儲存。**此功能尚未在 Railway 啟用，也未經實際通話回憶與刪除驗收。**

本機簽發與接收端驗證：`python3 -m unittest deploy_pwa.test_voice_context_issuer poc_agent.test_signed_context -v`。測試涵蓋缺密鑰、舊 metadata、跨帳號、同源、房間錯誤、過期、竄改及推測記憶拒絕；它不等於線上通話驗收。

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
