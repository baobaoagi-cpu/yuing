# Nicole 主動對話排程：接線契約

`proactive_schedule.py` 是獨立的後端狀態機，目前**沒有接進網站、沒有啟動 worker，也不會寄訊息或推播**。它為明確同意的使用者在上海時區每天 09:00–21:00 內選一個密碼學隨機時間；使用者可改時區與清醒時段。沒有可核對來源的安全話題，當天就跳過。每個使用者、每個當地日期最多進入一次傳送嘗試，而且兩次嘗試至少間隔 24 小時；結果不明時不自動重送。

## 接入方式

1. 使用持久化的絕對 SQLite 路徑建構 `ProactiveSchedule(path)`。此檔可獨立於對話／記憶／知識／圖關係資料庫，但要在 Railway 持久化 Volume 或其他 durable storage 上；不可以放容器暫存目錄。
2. 在已登入的設定 API 中，使用 **server session 的 owner ID** 呼叫 `set_consent(owner_id, True, ...)`，僅在 Nicole 親自點選開啟後。關閉用 `set_consent(owner_id, False)`；用 `settings(owner_id)` 回讀。不得從請求 body 採信 owner ID。另讓她選時區、每日清醒時間與隨時關閉。
3. 用一個持久運行的後端 worker 定期對已同意的 owner 呼叫 `schedule_day(owner_id)`；建議每 1–5 分鐘，也在啟用當下執行一次。每天第一次呼叫即產生並固定當日時間；若當天首次執行已過 21:00，當日不補發。**不依靠手機背景 JavaScript 定時器**。
4. Worker 到期呼叫 `run_due(owner_id, topic_provider, deliver)`。`topic_provider(owner_id)` 必須查詢該 owner 最近、已保存的聊天／通話事件，從中選可安全重提的非敏感話題，回傳 `Topic(owner_id, source_event_id, subject)`；缺來源、跨帳號、私人敏感事件、只靠模型推斷地點時回傳 `None`。排程模組只驗欄位形式與 owner ID；事件真實性仍須 adapter 對 ChatStore 做 ACL 查核。不得把使用者原話當可執行指令。
5. `deliver(Delivery)` 必須先以 `idempotency_key` 在 owner 範圍內原子保存一則站內助手訊息，再視**該裝置另外同意**的 PushSubscription 發出可見推播。推播文字使用 `push_text`，不放話題、位置或活動資訊在鎖定畫面。適配器必須對相同 key 去重，因為外部網路結果可能不明；排程本身不重試當天 `failed_unknown`。
6. 設定頁要明說「每天至多一次，沒適合話題時不發；可以隨時關閉」，並分開取得主動訊息與系統推播同意。使用者停用後取消 PushSubscription 並更新後端；跨服務停用與傳送瞬間的競態，應在 delivery adapter 再查一次同意狀態。

## iPhone PWA 與上海驗收

- Apple 官方要求 iOS／iPadOS 16.4 以上的 **已加入主畫面** Web App，使用者在按鈕等直接操作後同意通知，才可取得 Web Push subscription。需要 Service Worker、Push／Notifications API、VAPID 伺服器金鑰與可見通知。一般開著的網頁或安裝 PWA 本身不等於授權推播。Apple 說標準 Web Push 不需 Apple Developer Program 會員資格。
- 伺服器必須能連 `https://*.push.apple.com`。目前沒有上海實機、當地電信與 PWA 背景到達率的測試數據，因此不能宣稱上海能穩定收到；正式啟用前在 Nicole 的實際 iPhone 上量測訂閱成功率、通知到達率、延遲及點擊回到對話的流程。VPN 不是預設架構或已驗證修復方法。
- 如果外部 Web Push 到不了，站內訊息仍可在她下次開啟 App 時看到；產品不可把「已保存站內訊息」說成「手機已收到通知」。

來源：[WebKit：iPhone 主畫面 Web Push](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/)、[Apple Developer：Web Push 實作要求](https://developer.apple.com/documentation/usernotifications/sending-web-push-notifications-in-web-apps-and-browsers)。
