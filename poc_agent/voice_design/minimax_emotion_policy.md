# 陳端倪通話的 MiniMax 情緒使用原則

通話預設讓 Speech 2.8 根據自然口語自行決定語氣。端倪通常說一兩句，
情緒跟當下內容一致，不為了展示功能突然唱歌、尖叫或大幅變調。
她可以溫柔、好奇、調皮，也可以不同意對方；碰到難過、風險或拒絕時，
不用玩笑聲掩蓋意思。

MiniMax 官方 Speech 2.8 支援有限的語氣詞標籤。通話的 TTS 小工具箱在
每段話送出前，根據清楚的口語線索選一個對應聲音：笑聲 `(laughs)` 或
`(chuckle)`、嘆氣 `(sighs)`、驚訝 `(gasps)`、遲疑 `(emm)`、鬆口氣
`(exhale)`。例如自然說出「嘿嘿」、「唉」、「咦」時，以標籤取代該
口語聲；「開個玩笑」、「真沒想到」、「總算松了口氣」等明確語意，
則在 TTS 文字前加標籤。普通問候、泛泛的開心或他人難過的內容保持原文，
避免亂笑或過度表演。每次回覆最多兩個聲音提示；每段等待最長 48 個字元，
避免為了選情緒拖住語音。它不額外呼叫付費 LLM。

標籤只在 LiveKit 的 TTS node 加到送往 MiniMax 的音訊文字；LLM 回覆、
對話紀錄及畫面逐字稿仍是原來的口語字句。日誌只記模型和標籤種類或
`none`，不記對話內容。不要要求 LLM 自行寫標籤，也不承諾可生成任意聲音。

MiniMax 的 `emotion` 參數屬於整段 TTS 的設定。現行 LiveKit 串流接法
沒有安全、已驗證的逐句切換機制，所以保持未指定，由模型自行表達。
不可在多人通話共用的 TTS 實例上動態修改這個參數。實際聲音效果與
標籤是否被模型正確實現，仍需手機聽測；程式測試只能證明標籤送入 TTS。

來源：
- MiniMax API：<https://platform.minimax.io/docs/api-reference/speech-t2a-http>
- LiveKit MiniMax TTS：<https://docs.livekit.io/agents/models/tts/minimax/>
- LiveKit Agent TTS node：<https://docs.livekit.io/agents/logic/nodes/>
