# Railway 公開感知預覽

本目錄來自獨立的端倪感知 POC 1.0，加入語靈分支後供 Railway 靜態部署。它只提供裝置端 YAMNet 生活聲分類與預寫畫面回應。沒有把本機 Hermes、正式人格、私人記憶或測試知識複製到雲端，也不接雲端語音供應商。

`Dockerfile` 只複製此目錄並以 Python 靜態伺服器提供。`server.py` 要求部署環境設定 `POC_PREVIEW_USER` 和 `POC_PREVIEW_PASSWORD`，未設定時不提供頁面；這是內部預覽密碼門，尚非日後的個別帳號與權限系統。HTTPS 網址可在手機 Safari 開啟並嘗試加入主畫面。iPhone 與上海網路的實際感知效果仍需實機驗收。

YAMNet ONNX 模型、類別表和 ONNX Runtime Web 隨目錄提供。授權見 `assets/YAMNET-LICENSE.txt` 與 `assets/ONNXRUNTIME-LICENSE.txt`；模型來源為 [Audio Magic YAMNet ONNX](https://huggingface.co/audiomagic/yamnet-onnx)，類別表為 CC BY 4.0。
