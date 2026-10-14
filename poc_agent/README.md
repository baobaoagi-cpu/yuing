# 陳端倪 MiniMax 通話 POC

這是獨立的 LiveKit Agent，使用原版語靈通話前端與現有 LiveKit 專案：

`手機 → LiveKit → Deepgram Nova-3 (LiveKit Inference) → MiniMax M3 → MiniMax Speech 2.8 Turbo → 手機`

STT 固定使用 Nova-3 的 `zh-CN` 普通話模式，並提示專名 `陈端倪`。Deepgram 的 `multi` 模式不包含中文；此設定僅由 SDK 建構測試確認可用，專名辨識改善仍須真人通話比較逐字稿。切換為單語模式後，混合語言發話也須另測。

LLM 與 TTS 共用同一把 `MINIMAX_API_KEY`，只放在 Agent 服務端。國際站測試使用 `https://api.minimax.io` 與 `https://api.minimax.io/anthropic`；已購點數的 Subscription Key 由使用者親自存入 Railway。尚未接入完整 Hermes、知識庫或圖資料庫；登入帳號的明示記憶已有簽署上下文接線，狀態見下節。Agent 名稱固定為 `duanni-minimax-poc`，與現有 Builder Agent 隔離。

聲音設計候選提示詞與試聽文案在 `voice_design/chen_dunni_v1.md`。產生的聲音需經實際聆聽，再設定 `MINIMAX_VOICE_ID`；不能以 API 成功取代音色驗收。

陳端倪的人格憲法固定在 `persona/chen_dunni_constitution_v1.md`，每次建立通話 Agent 時作為底層 system instructions 載入。改動須新增版本並核對差異；目前的能力限制另外追加，不以人格描述冒充已接入的感知或記憶功能。這份檔案屬於此 MiniMax POC，原 Builder Agent 不會自動使用。

第二層原則篇是 `persona/pset_principles_v0.1.md`，固定接在核心人格之後、通話能力邊界之前。PSET 的「演化」是產品規則；此 POC 尚無自動修改人格檔或長期記憶的寫入流程。

## 通話與文字記憶的邊界

文字聊天和通話是兩條獨立的模型會話。通話 Agent 不讀 LiveKit `identity`、`userId` 或 `convId` 當作身分證明。`/api/yuling/token` 由伺服器產生隨機身分與房間，不採信瀏覽器提交的身分。Agent 與 PWA 位於不同 Railway 服務，沒有共享 SQLite 檔案。新版簽發時只取**目前已登入帳號**最近八則本服務文字或通話逐字稿片段，以及主動保存的記憶；每通開始時帶入，通話中新增的片段要到下一通才會成為近期上下文。片段有來源標示，可能有辨識錯誤，不當作已核實事實或永久記憶。其他 Codex 對話、DUANNI HOME 與 Hermes 的內容沒有自動接入。

`signed_context.py` 是**預設關閉**的接收端：沒有 `POC_VOICE_CONTEXT_HMAC_KEY` 就不讀任何上下文。即使設定密鑰，仍只有同一把伺服器端密鑰簽署、綁定實際 LiveKit 房間、有效期不超過兩分鐘的封包才會進入提示詞；明示記憶與近期對話各最多八筆，近期對話不能冒充正式記憶。舊版未簽署 metadata 一律沒有記憶。這份接收端不能單獨證明線上通話已讀到上下文。

簽發端在 `POC_VOICE_CONTEXT_ENABLED=1` 時生效：PWA 驗證登入 cookie 與同源請求後，按 `session['account']` 查取該帳號的明示記憶，由伺服器建立 LiveKit 房間及 token，並把簽名封包放入 `roomConfig.agents[0].metadata.voice_context`。前端提交的 `identity`、`owner_id`、記憶內容或 metadata 不會選擇帳號。缺登入、簽名密鑰或儲存時拒絕簽發。兩服務需要同一個獨立的 HMAC 密鑰；目前 `/tmp` 資料會隨容器重建消失，仍須把 ChatStore 移到經驗證的持久儲存。**2026-10-14 03:08 兩端設定及部署已成功，且本機簽發與驗簽測試通過；尚未以實際通話核對 Agent 收到記憶，也未驗證刪除後已在房間的提示詞會即時更新。**

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

通話用量收據另需 `POC_USAGE_HMAC_KEY` 與 `POC_USAGE_ENDPOINT`。Agent 只使用房間綁定且已驗簽的登入帳號，不把語音辨識到的姓名當作帳號。收據送往 PWA 持久化資料庫；Agent 容器本身沒有持久化 Volume，送達失敗不得當作已入帳。
