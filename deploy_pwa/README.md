# Railway 語靈 POC 預覽

此服務提供受登入保護的聊天、圖片、明示記憶、通話頁和裝置端 YAMNet 生活聲候選分類。文字與圖片對話會呼叫 MiniMax；語音 Agent 是另一個服務。Hermes、向量搜尋和圖推理尚未接入。對話與圖片目前存在 `POC_CHAT_DB_PATH` 指向的 SQLite 檔案；若設為 `/tmp`，部署或容器重建後可能遺失。`/api/v2/status` 會標示 `ephemeral`。

`Dockerfile` 只複製此目錄並以 Flask/Gunicorn 提供。`server.py` 要求部署環境設定 `POC_PREVIEW_USER`、`POC_PREVIEW_PASSWORD` 和獨立的 `POC_SESSION_SECRET`（至少 32 字元），未設定時健康檢查不通過。登入使用 Flask 簽章、HttpOnly、SameSite Cookie；勾選「保持登入 30 天」時設 30 天到期，未勾選為瀏覽器工作階段 Cookie 且伺服器端 12 小時到期。登出可清除 Cookie；更換帳密或簽章祕密會使舊登入失效。這仍是單一內部預覽帳號，尚非朋友的個別帳號與權限系統。HTTPS 網址可在手機 Safari 開啟並嘗試加入主畫面。iPhone 與上海網路的實際感知效果仍需實機驗收。

YAMNet ONNX 模型、類別表和 ONNX Runtime Web 隨目錄提供。授權見 `assets/YAMNET-LICENSE.txt` 與 `assets/ONNXRUNTIME-LICENSE.txt`；模型來源為 [Audio Magic YAMNet ONNX](https://huggingface.co/audiomagic/yamnet-onnx)，類別表為 CC BY 4.0。

## 資料歸屬與離線快照

`ChatStore` 以登入帳號作為資料擁有者；會話、圖片、明示記憶、知識項與關係候選各有 `owner_id`，事件透過所屬會話隔離。舊版單帳號資料在首次升級時歸給 `POC_LEGACY_OWNER`，未設定則歸給當時的 `POC_PREVIEW_USER`。**升級前先固定舊帳號歸屬，不要先改掉原有登入帳號。**目前登入系統仍只有一組共用帳密；不同的人若共用它，就會看到同一份資料，不能視為朋友的個別權限。

有權存取實際 SQLite 檔案的操作者，可在服務停止或運行時做一致性快照：

```sh
python3 -m deploy_pwa.backup_chat_db /absolute/path/chat.sqlite3 /private/new-snapshot.sqlite3
```

工具以 SQLite backup API 複製完整資料庫，檢查完整性、表格數量和 SHA-256，建立權限 `0600` 的新檔，不覆寫既有目的檔。這是**離線工具**，沒有自動從 Railway `/tmp` 備份，也未證明線上資料已可復原。還原、搬移、改用持久磁碟或正式多帳號開放前，需先演練並核對資料擁有者和讀取權限。
