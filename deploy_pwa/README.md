# Railway 公開感知預覽

本目錄來自獨立的端倪感知 POC 1.0，加入語靈分支後供 Railway 靜態部署。它只提供裝置端 YAMNet 生活聲分類與預寫畫面回應。沒有把本機 Hermes、正式人格、私人記憶或測試知識複製到雲端，也不接雲端語音供應商。

`Dockerfile` 只複製此目錄並以 Flask/Gunicorn 提供。`server.py` 要求部署環境設定 `POC_PREVIEW_USER`、`POC_PREVIEW_PASSWORD` 和獨立的 `POC_SESSION_SECRET`（至少 32 字元），未設定時健康檢查不通過。登入使用 Flask 簽章、HttpOnly、SameSite Cookie；勾選「保持登入 30 天」時設 30 天到期，未勾選為瀏覽器工作階段 Cookie 且伺服器端 12 小時到期。登出可清除 Cookie；更換帳密或簽章祕密會使舊登入失效。這仍是單一內部預覽帳號，尚非朋友的個別帳號與權限系統。HTTPS 網址可在手機 Safari 開啟並嘗試加入主畫面。iPhone 與上海網路的實際感知效果仍需實機驗收。

YAMNet ONNX 模型、類別表和 ONNX Runtime Web 隨目錄提供。授權見 `assets/YAMNET-LICENSE.txt` 與 `assets/ONNXRUNTIME-LICENSE.txt`；模型來源為 [Audio Magic YAMNet ONNX](https://huggingface.co/audiomagic/yamnet-onnx)，類別表為 CC BY 4.0。
