# Railway 語靈 POC 預覽

`/tools` 提供已登入帳號的時區換算入口。`GET/POST /api/v2/tools/time` 是後端白名單、同源保護的本機工具適配，參考開源 MCP Time Server 的唯讀工具用途；目前**沒有啟動或連接 MCP server**。它不連外、不使用使用者記憶或外部帳號，夏令時間切換的歧義時段會拒絕換算。容器需 `tzdata` 才能取得 IANA 時區資料。

聊天頁右上角選單由登入後的 `GET /api/v2/tools/catalog` 決定狀態。時區換算有入口；公開網頁讀取只有在後端開關與對話儲存都可用時才有入口。其他生活與工作候選維持灰色、不可點擊，並列出缺少的憑證、授權或費用邊界；候選清單不會自行接入第三方或讀取帳號資料。

此服務提供受登入保護的聊天、圖片、明示記憶、通話頁和裝置端 YAMNet 生活聲候選分類。文字與圖片對話會呼叫 MiniMax；語音 Agent 是另一個服務。Hermes、向量搜尋和圖推理尚未接入。對話與圖片目前存在 `POC_CHAT_DB_PATH` 指向的 SQLite 檔案；若設為 `/tmp`，部署或容器重建後可能遺失。Railway 未掛載 Volume 時，其他容器路徑也不能視為持久化。`/api/v2/status` 會標示 `ephemeral`；路徑已設定但未做還原驗證時標示 `path_configured_unverified`，`persistent_history` 仍為 `false`。

聊天附件接受 JPG/PNG/WebP 圖片（4 MB 內）與 PDF、DOCX、PPTX、XLSX、TXT、MD、CSV、DOC、XLS、PPT、WPS、ET、DPS、OFD 文件（5 MB 內）。文件原件、SHA-256、檔名與最多 2500 字文字預覽保存在同帳號 SQLite，事件引用附件 ID；重開對話後可看到文件卡片並下載原件。模型本輪只看到文字預覽；掃描 PDF、圖片、表格版式及預覽以外內容未驗證，空白抽取會明示無文字。DOC、XLS、PPT、WPS、ET、DPS、OFD **只能保存與下載，不能解析內容**，聊天卡片明示狀態；舊版 Office 限 OLE 容器，WPS/ET/DPS 限 OLE 或 ZIP 容器，OFD 限含 `OFD.xml` 的 ZIP，這只是基本封裝檢查，不證明檔案內容正確。需要端倪讀取時請先轉為 PDF 或 DOCX 等可抽取格式。PDF 使用已列入依賴的 [pypdf](https://github.com/py-pdf/pypdf)（BSD-3-Clause）；XLSX 使用 [openpyxl](https://github.com/soxhub/openpyxl)（MIT）；Office XML 與文字檔使用 Python 標準函式庫。沒有外部上傳轉換服務。若 SQLite 位於 `/tmp`，附件原件與事件同樣可能在重新部署後消失。

`POST /api/v2/knowledge` 可將使用者已送出的原始訊息整理成知識條目，須帶該訊息的 `source_event_id`、`title`、`content`；後端只接受同帳號的使用者明示訊息。`GET /api/v2/knowledge` 列出同帳號條目，`DELETE /api/v2/knowledge/{id}` 可撤回。這些條目是有來源的使用者筆記，不代表內容已查證，也沒有向量檢索或圖推理；目前資料庫若在 `/tmp`，知識同樣會在重新部署時消失。貼網址只儲存網址，不會自動抓取網頁內容。

`POST /api/v2/relations` 可用同帳號兩個知識條目 ID、關係描述及一則使用者來源訊息 ID 保存**關係候選**；`GET /api/v2/relations` 與 `DELETE /api/v2/relations/{id}` 供列出和撤回。狀態固定為 `candidate`，不表示因果已證實。刪除知識條目會同步清除與其相連的候選關係。目前只是 SQLite 來源紀錄，沒有圖資料庫查詢或自動推論。

通話記憶簽發是預設關閉的 POC 功能。必須由 PWA 與 Agent 各自設定相同、至少 32 bytes 的 `POC_VOICE_CONTEXT_HMAC_KEY`，再於 PWA 設定 `POC_VOICE_CONTEXT_ENABLED=1`；缺任一條件時不能宣稱語音可讀記憶。簽發只取已登入帳號的使用者明示記憶，不使用前端提交的 identity、owner 或 metadata。啟用前先驗證同源檢查、跨帳號拒絕、部署後持久性及真人通話；現有 `/tmp` 資料仍會在重建時消失。

`Dockerfile` 只複製此目錄並以 Flask/Gunicorn 提供。`server.py` 要求部署環境設定 `POC_PREVIEW_USER`、`POC_PREVIEW_PASSWORD` 和獨立的 `POC_SESSION_SECRET`（至少 32 字元），未設定時健康檢查不通過。登入使用 Flask 簽章、HttpOnly、SameSite Cookie；勾選「保持登入 30 天」時設 30 天到期，未勾選為瀏覽器工作階段 Cookie 且伺服器端 12 小時到期。登出可清除目前瀏覽器的 Cookie；更換帳密或簽章祕密會使相應舊登入失效。HTTPS 網址可在手機 Safari 開啟並嘗試加入主畫面。iPhone 與上海網路的實際感知效果仍需實機驗收。

### 可選的個別測試帳號

保留既有 `POC_PREVIEW_USER` / `POC_PREVIEW_PASSWORD` 原樣；額外帳號以私密環境變數 `POC_EXTRA_ACCOUNTS_JSON` 設定為「帳號名稱 → Werkzeug PBKDF2-SHA256 雜湊」的 JSON 物件，最多 20 組。帳號名稱限英文字母起頭，總長 3–64 字元，後續可含數字、底線、點或連字號，且不能與舊帳號重複。格式不正確會使服務啟動失敗，避免悄悄關閉新增帳號。產生一組**未使用過的強密碼**及其雜湊時，在安裝 Flask/Werkzeug 的 Python 環境執行以下指令；密碼在終端機隱藏輸入，不寫入 shell 歷史。產出的 JSON 直接放入 Railway 私密變數，不貼到聊天或提交 Git。

```sh
python - <<'PY'
import getpass, json
from werkzeug.security import generate_password_hash
username = "shanghai_friend"
password_hash = generate_password_hash(
    getpass.getpass("New preview password: "), method="pbkdf2:sha256:1000000")
print(json.dumps({username: password_hash}))
PY
```

原帳號的 Cookie 驗證值維持舊算法；新增帳號的 Cookie 綁定其帳號與雜湊，移除帳號或更換雜湊會使其既有 Cookie 失效。登入錯誤仍受既有 IP 速率限制，寫入 API 保留來源檢查。這只是 POC 的少量測試帳號：沒有自助註冊、權限角色、找回密碼、伺服器端單一裝置登出撤銷或正式稽核。登出清除目前瀏覽器 Cookie；若 Cookie 已被複製，需更換該帳號雜湊或 `POC_SESSION_SECRET` 才能使複本失效。

YAMNet ONNX 模型、類別表和 ONNX Runtime Web 隨目錄提供。授權見 `assets/YAMNET-LICENSE.txt` 與 `assets/ONNXRUNTIME-LICENSE.txt`；模型來源為 [Audio Magic YAMNet ONNX](https://huggingface.co/audiomagic/yamnet-onnx)，類別表為 CC BY 4.0。

## 資料歸屬與離線快照

`ChatStore` 以登入帳號作為資料擁有者；會話、圖片、明示記憶、知識項與關係候選各有 `owner_id`，事件透過所屬會話隔離。舊版單帳號資料在首次升級時歸給 `POC_LEGACY_OWNER`，未設定則歸給當時的 `POC_PREVIEW_USER`。**升級前先固定舊帳號歸屬，不要先改掉原有登入帳號。** 若舊帳號是 `nicole`，請將 `POC_LEGACY_OWNER=nicole` 固定；新增好友帳號後，彼此的會話、圖片和記憶才會按不同帳號分開。共用同一組帳密的兩人仍會看到同一份資料。聊天頁在同一瀏覽器切換帳號時可能暫留上個帳號的本地會話編號；後端會拒絕讀取並由頁面清除編號，但建議不同人使用各自裝置或瀏覽器設定檔。

文字聊天的明示記憶採兩階段讀取：先用本輪文字做有界的字面話題搜尋，得到當前帳號的短索引，再按 ID 取最多三筆全文。泛問候不載入記憶；有使用者明示訊息來源的記憶與未綁來源的舊記憶會分別標示，後者需要本人確認。這不是向量或語意搜尋，知識條目目前只有同帳號的索引及全文介面，尚未接入回覆；通話僅在開始時載入近期上下文，沒有每回合動態搜尋。資料目前以共用 SQLite 檔案中的 `owner_id` 邏輯隔離，並非每位使用者各有獨立實體資料庫檔案。

登入後顯示的姓名由該帳號的個人資料提供；三個測試帳號有開帳稱呼，本人更正優先。新增帳號可在伺服器端 `POC_ACCOUNT_DISPLAY_NAMES_JSON` 指定「帳號 → 姓名」映射，不由瀏覽器決定。通話簽名上下文和前端都讀取同一來源。

## 通話用量與成本

Agent 在通話結束時把 LiveKit SDK 回報的累計 LLM token、STT 音訊秒數、TTS 字元及模型名稱，以獨立 `POC_USAGE_HMAC_KEY` 簽名送往 `POC_USAGE_ENDPOINT`（`/api/yuling/usage/ingest`）。兩服務須設定同一把至少 32 字元的專用金鑰。PWA 端只在 `POC_CHAT_DB_PATH` 位於 Railway 持久化 Volume 時接收，並按已驗簽的帳號和 `call_id` 儲存；`call_id` 可與同帳號通話逐字稿對照。登入者可讀 `/api/yuling/usage`，只能取得自己帳號的用量。重送同一模型的累計數據會覆寫，不會重複加總。

這些數字是 SDK 用量，**不是供應商帳單**。尚未配置有來源及生效日期的單價，因此美元、人民幣與 credit 成本為「未計價」。短時通話、斷線或收據送達失敗都可能造成缺記；對照供應商帳單前不可用於扣款。

有權存取實際 SQLite 檔案的操作者，可在服務停止或運行時做一致性快照：

```sh
python3 -m deploy_pwa.backup_chat_db /absolute/path/chat.sqlite3 /private/new-snapshot.sqlite3
```

工具以 SQLite backup API 複製完整資料庫，檢查完整性、表格數量和 SHA-256，建立權限 `0600` 的新檔，不覆寫既有目的檔。這是**離線工具**，沒有自動從 Railway `/tmp` 備份，也未證明線上資料已可復原。還原、搬移、改用持久磁碟或正式多帳號開放前，需先演練並核對資料擁有者和讀取權限。
