# 陳端倪通話的 MiniMax 情緒使用原則

通話預設讓 Speech 2.8 根據自然口語自行決定語氣。端倪通常說一兩句，
情緒跟當下內容一致，不為了展示功能突然唱歌、尖叫或大幅變調。
她可以溫柔、好奇、調皮，也可以不同意對方；碰到難過、風險或拒絕時，
不用玩笑聲掩蓋意思。

MiniMax 官方 Speech 2.8 支援有限的語氣詞標籤，例如 `(chuckle)`、
`(sighs)` 和 `(gasps)`。目前只在端倪自然說出「嘿嘿／哈哈」、
句首「唉」或句首「咦」時，將該口語聲換成對應標籤，且每次回覆最多一次。
標籤只在 LiveKit 的 TTS node 加到送往 MiniMax 的音訊文字；
LLM 回覆、對話紀錄及畫面逐字稿仍是原來的口語字句。
不要求 LLM 自行寫標籤，也不承諾可以生成任意聲音。

MiniMax 的 `emotion` 參數屬於整段 TTS 的設定，現行接法沒有經實測的
逐句切換機制，所以保持未指定，由模型自行表達。日後若要更自由地切換
喜怒哀樂，先建立獨立的意圖判斷、每次合成的安全設定及實際手機聽測，
再擴大。情緒標籤不應成為文字對話的內容或說出來的字。

來源：
- MiniMax API：<https://platform.minimax.io/docs/api-reference/speech-t2a-http>
- LiveKit MiniMax TTS：<https://docs.livekit.io/agents/models/tts/minimax/>
- LiveKit Agent TTS node：<https://docs.livekit.io/agents/logic/nodes/>
