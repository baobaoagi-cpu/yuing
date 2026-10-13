# 語靈 × Hermes 最小本機測試

分支：`feature/local-hermes-persona-memory-kb-poc`，從 `yuing` 公開版 `980ba8b9` 建立。本目錄是獨立的本機薄橋接，沒有修改 DUANNI HOME 的正式人格、記憶、知識或 Hermes 設定。

## 一條最小回應鏈

```text
瀏覽器打字／錄音 6 秒
  → 本機 faster-whisper small 轉文字（錄音需手動確認後送出）
  → yuing CharacterProvider 介面載入 persona_public.md 公開體驗人格
  → 既有 MemoryRuntime，限定兩個 project:yuing-local-poc-* namespace
  → fixtures.json 的合成記憶與公開測試知識
  → Hermes HOME 已設定的 qwen3:4b-duanni-voice 本機 Ollama 模型
  → 文字回覆／可選的裝置測試聲音
```

這個分支驗證人格、記憶、知識與模型的接線。模型使用 Hermes HOME 的設定與現有本機別名，但**不啟動完整 Hermes Agent、Command Bus 或 yuing LiveKit**。因此它不是即時雙向語音，也不是正式端倪聲音。原本的 iPhone PWA／生活聲 POC 1.1 是另一個候選交付，不能以本機測試結果取代其驗收。

## 在這台 Mac 上測

雙擊 `開啟本機測試.command`，或在 `yuing` 專案根目錄執行：

```sh
"$HOME/codex/dunni/duanni-codex-dev/runtime/hermes/venv/bin/python" local_poc/server.py
```

然後開啟 `http://127.0.0.1:9135/`。服務僅綁定 `127.0.0.1`；本機 Ollama 須已在 `127.0.0.1:11435` 運作，且 HOME 設定仍為零工具、8K context 和 `qwen3:4b-duanni-voice`。不會自動下載 Whisper 模型或啟動雲端服務。

測試「阿明喜歡喝什麼茶？」應命中**合成測試記憶**；問「語靈本機 POC 串起哪些部分？」應命中**公開測試知識**。也可按「錄音 6 秒」，等本機轉寫出文字，檢查後手動送出。選擇「裝置測試聲音」時使用瀏覽器系統語音，**不是正式端倪聲紋**。

## 資料與權限邊界

- `persona_public.md` 是新寫的公開候選，不複製 `four-spirit/personas/duanni/profile.json` 中的私人關係資訊。
- `fixtures.json` 僅含合成或公開測試資料。既有 MemoryRuntime 實作被載入後，canonical 與索引路徑會改到本目錄的 `.state/`；原本的 HOME 記憶路徑不會被讀寫。查詢顯式指定測試 namespace，回傳結果再以 fixture 內容白名單過濾。
- HTTP 請求沒有寫入記憶／知識的入口；本次對話只存在瀏覽器記憶體，重新整理後消失。
- 本機 HTTP 服務限制 Host、Origin 與 Content-Type，拒絕跨站頁面呼叫本機模型；只綁定 `127.0.0.1`。
- 本機模型調用沒有工具欄位，也不接任務派送、郵件或智慧家電。`/api/status` 會先驗證 HOME 的模型、端點、8K 與零工具設定；不符合即拒絕聊天。
- 程式與 ZIP 不包含金鑰、私人記憶或真實對話。`.state/` 是本機合成測試索引，受 `.gitignore` 排除。

## 已驗證與未驗證

- 已在本機驗證：Hermes 配置狀態、合成記憶命中、公開測試知識命中、實際本機模型回覆、Mac 系統語音產生的 WAV 經本機 Whisper 轉寫、Chrome 390px 畫面輸入與回覆。
- 尚未驗證：真人麥克風、正式聲紋、完整 Hermes Agent、LiveKit 即時通話、iPhone PWA、上海網路及朋友的人類驗收。
