# 北京區雙工通話實驗

目前正式 `/call` 仍是 LiveKit、Deepgram、MiniMax M3 與 MiniMax TTS。打斷與誤打斷恢復由 LiveKit Agent 處理。

實驗分支新增 `/duplex`。瀏覽器透過 WebRTC 直接傳音訊至阿里百煉北京區 Qwen3.8-Omni-Flash-Realtime；本服務只代轉經登入與同源驗證的 SDP offer，API Key 留在伺服器。每帳號最多每分鐘四次建連。這條路徑尚未接入正式記憶、逐字稿儲存、通話後心聲或 MiniMax 自訂音色，不能當成正式端倪通話替代品。

啟用前需在**獨立測試部署**的 PWA 服務設定：

- `DASHSCOPE_API_KEY`：阿里百煉華北 2（北京）區 API Key。不要放在瀏覽器或 Git。
- `DASHSCOPE_WORKSPACE_ID`：同區、同 Key 所屬的業務空間 ID。
- `POC_DUPLEX_ENABLED=1`：完成費率與使用額度確認後才啟用。

申請入口：https://bailian.console.aliyun.com/cn-beijing/model/settings/api-key

業務空間 ID 說明：https://help.aliyun.com/zh/model-studio/obtain-the-app-id-and-workspace-id

服務端預檢：登入後 `GET /api/duplex/status` 應為 `available:true`，且回應中不含 Key。實測應依序核對麥克風權限、SDP 交換、WebRTC 連線、雙向音訊、說話中打斷、噪音誤打斷、逐字稿、iPhone/Android、上海網路延遲與費用。未配置 Key 前不要宣稱雙工已可用。

官方協定：https://help.aliyun.com/zh/model-studio/realtime
