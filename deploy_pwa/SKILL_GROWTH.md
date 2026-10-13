# 技能養成候選流程（尚未接到線上對話）

`skill_growth.py` 是獨立的、按帳號隔離的 SQLite 候選紀錄器。它不搜尋網路、不下載開源元件、不執行 SKILL/MCP、不呼叫 Hermes，也不修改既有工具權限。`ready` 只表示「可交給接入工作再次審查」，並非已安裝或端倪已學會。此模組尚未接到聊天或語音路由，不能在 UI 聲稱功能已上線。

接入合約：聊天後端先驗證登入帳號，並從該帳號的對話資料庫確認 `source_event_id` 是一則**使用者明示**的需求訊息；`explicit_skill_need(text)` 只辨認少數直接要求，未命中時不推測技能需求。然後才可呼叫 `propose(owner_id=..., source_event_id=..., user_need=..., name=..., kind=..., source_url=..., license_id=..., requested_scopes=..., cost=...)`。不能信任前端自行傳入的 `owner_id` 或來源訊息 ID。模型可以提議候選，但不能呼叫 `review` 或 `mark_ready`。若來源或授權未知，填 `unknown`，不要假裝已核查。

狀態順序：`candidate` → 人工 `review` → `record_synthetic_practice` → `mark_ready`。審核或合成測試失敗則為 `rejected`。人工審核需明確記下 `license_verified` 與 `cost_verified`；未核實不能批准。每筆保存使用者需求、原始訊息 ID、來源網址、授權、權限範圍、成本及審核／測試證據。只有免費、零新增權限且合成測試通過的候選能進入 `ready`。需要付費、憑證或新增權限的項目留在候選紀錄，交由人決定；實際下載、安裝、真實外部操作及 Hermes 接入必須走另一個明確授權與驗收流程。

可在對話中用簡短自然的問題探詢需求，例如「你最近有沒有哪件事，會希望我能幫你做得更順手？」這是聊天內容，不代表已自動取得技能或可以記錄未經確認的性格結論。
