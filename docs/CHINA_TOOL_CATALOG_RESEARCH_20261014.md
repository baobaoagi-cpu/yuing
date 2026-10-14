# 中國場景工具目錄：候選、授權與可用狀態

查核日期：2026-10-14。本文件說明聊天頁右上角工具目錄的判斷依據。它是有日期的研究快照；第三方接口、價格及服務地域在實際接入前都要重查。灰色項目不會交給模型呼叫，也不代表已取得第三方帳號權限。

## 已接入的範圍

- `/api/v2/tools/catalog` 依後端狀態回傳選單。登入後的時區換算有可用入口；公開 HTTPS 網頁讀取只有在後端開關和對話儲存可用時顯示入口。
- 時區換算是本服務後端的受限工具，不是已連線的外部 MCP。其他生活、工作和趣味項目維持灰色候選。候選清單來源見 `deploy_pwa/server.py`，狀態與不可點擊行為見 `deploy_pwa/test_tool_catalog.py`。
- 通話 Agent 的阿里雲百煉 WebSearch 是**另一條能力**，只在服務端金鑰、明確開關、簽名通話身分與持久額度都就緒時提供。每通最多三次、試用額度共二十次；參見 `poc_agent/README.md`。這是應用呼叫上限，不能限制同一供應商帳戶的其他使用。

## 生活候選

| 服務 | 官方依據 | 接入前缺口 |
| --- | --- | --- |
| 和風天氣 | [開發配置](https://dev.qweather.com/docs/start/)、[價格](https://dev.qweather.com/docs/finance/pricing/)、[來源標示](https://dev.qweather.com/docs/terms/attribution/) | 專屬 Key、來源標示、費用硬停止；優先只取指定城市天氣。 |
| 高德地圖 | [MCP 建立流程](https://lbs.amap.com/api/mcp-server/create-project-and-key)、[服務授權](https://lbs.amap.com/pages/authorization/) | Web Key、授權與額度；位置資料需使用者同意。 |
| QQ 郵箱 | 可評估本人開啟的 IMAP 與專用授權碼 | 本人授權、服務條款、讀取範圍、郵件隔離與撤銷；不得索取主密碼。 |
| 支付寶、美團 | [支付寶開放平台](https://open.alipay.com/)、[美團開放平台](https://developer.meituan.com/) | 尚未證明普通個人帳號有合適的通用唯讀接口；不接支付或交易。 |
| 12306 | [官方政策](https://kyfw.12306.cn/otn/gonggao/privacyPolicy_web_other.html) | 不提供抓票、代購或帳號連接。 |

## 工作候選

| 服務 | 官方依據 | 接入前缺口 |
| --- | --- | --- |
| 飛書 | [官方 MCP 工具清單](https://open.feishu.cn/document/mcp_open_tools/supported-tools?lang=zh-CN)、[開源適配器](https://github.com/larksuite/lark-openapi-mcp) | 應用及本人 OAuth；逐項只讀權限。適配器開源不等於雲端 API 已授權或免費。 |
| 釘釘、企業微信 | [釘釘開放平台](https://open.dingtalk.com/)、[企業微信相關能力](https://cloud.tencent.com/document/product/1831/138290) | 組織管理員授權、可信 IP、資料範圍；個人通用唯讀 MCP 尚未核實。 |
| WPS 365 | [官方 MCP](https://open.wps.cn/documents/app-integration-dev/mcp-server/introduction) | 企業應用和本人 token；先限制讀取工具，避免意外開放改寫或分享。 |
| 騰訊會議 | [官方 MCP](https://meeting.tencent.com/support/topic/2233/index.html) | 本人 token、方案／灰度資格；讀、改、刪工具需白名單。 |

## 趣味候選

| 服務 | 官方依據 | 接入前缺口 |
| --- | --- | --- |
| B 站投稿回顧 | [開放平台](https://open.bilibili.com/doc) | 開發者資格、本人授權、只讀稿件權限。 |
| 抖音作品互動 | [作品數據方案](https://open.douyin.com/platform/resource/docs/ability/open-data/video-data-solution) | 應用審核、本人 OAuth、作品資料的時效與額度。 |
| 快手漫畫臉 | [開放平台](https://open.kuaishou.com/platform/openApi?group=GROUP_OPEN_PLATFORM&menu=58) | 圖片本人同意、接口資格、每次費用及保存條款。 |
| Lichess 每日棋題 | [開發者頁](https://lichess.org/developers)、[API 建議](https://lichess.org/page/api-tips) | 公開棋題展示、限流和中國大陸可達性；先不接個人棋帳號。 |
| NASA 每日宇宙圖片 | [API 頁](https://api.nasa.gov/) | 新版接口、逐圖權利與署名、中國大陸可達性。 |

上述排序是接入評估清單，不是中國 MCP 用量排行榜。尚無中國大陸真實裝置對這些候選的延遲與成功率驗收。下一項實作必須先確定資料擁有者、用途、可撤銷憑證、費用上限和最小唯讀範圍；缺一項就維持灰色。
