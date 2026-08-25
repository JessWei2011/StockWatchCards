# AI 個股評分共用雙重驗證流程

## 啟動條件

使用者輸入「開始選股」後，立即執行本流程，不要再要求使用者貼上其他提示詞。此觸發詞適用於 Gemini、ChatGPT 及其他能讀取本專案文件的 AI。

## 共同分析規則

- 第一次分析與第二次稽核都必須完整遵守 `AI_SCORING_RULES.md`，不得使用各 AI 自己的另一套評分標準。
- 分析 `reports/` 內全部最新個股報表，使用相同原始資料與截止日期。
- 固定檢查價格結構、MACD/KD、三大法人逐日方向、融資、防禦牆與 K 線型態。
- 集保只當背景，不得計分。
- 台股配色固定為紅漲綠跌。

## 第一次分析

1. 依 `AI_SCORING_RULES.md` 逐檔完成全面分析與綜合分數。
2. 寫入 `data.js` 每檔股票的 `aiAnalysis.<目前AI名稱>`，至少保留 `score`、`decision`、`reason`、`date` 與 `verified: false`。Gemini 只能修改 `aiAnalysis.Gemini`，ChatGPT 只能修改 `aiAnalysis.ChatGPT`，不得複製或覆蓋另一個 AI 的欄位。
3. `winRate` 僅為既有首頁相容顯示欄位，不是任何 AI 排行榜的資料來源。
4. 依目前 AI 自己的 `score` 降冪取 TOP 5；同分依股票代號排序。

## 第二次規則稽核

1. 使用隔離的新工作階段或子代理擔任稽核者，但不要重新憑主觀印象產生另一套分數。
2. 稽核者必須讀取 `AI_SCORING_RULES.md`、同批 `reports/`，以及第一次寫入 `data.js` 的該 AI 專屬分數、決策、證據與 TOP 5；不得讀取其他 AI 的分析作為答案。
3. 逐檔核對原始數據引用、事件與位置用詞、MACD 正負、法人逐日方向、防禦牆、決策門檻，以及分數降冪排序。
4. 沒有發現具體規則或數據錯誤時，確認第一次結果通過；不得只因模型偏好不同而另給分數。
5. 發現錯誤時，必須指出股票、原始數字、違反的規則與修正理由；先修正目前 AI 自己的 `aiAnalysis`，再對修正後結果重新執行完整稽核。
6. 全部通過後，將目前 AI 已稽核股票的 `verified` 改為 `true`；不得替其他 AI 設為通過。

## 驗證與儲存

- 稽核通過後，送出的 `top5` 必須是已經稽核確認的首頁 TOP 5，並依分數降冪排列；同分依股票代號排序。
- 在可存取本機專案的環境中，稽核通過後必須執行 `python save_ai_ranking.py --ai "目前AI名稱"`。Gemini 使用 `--ai Gemini`，ChatGPT 使用 `--ai ChatGPT`。
- 必須看到終端輸出 `RANKING_SAVED`，再重新讀取 `ai_rankings.json` 確認該日期與 AI 的 `verification.status` 為 `passed`，才算整個「開始選股」流程完成。
- 沒有成功寫入 `ai_rankings.json` 時，禁止回報「完成」、「驗證吻合」或建立成功的 commit；必須回報實際儲存錯誤並繼續修正。
- POST `/api/ai-rankings/upsert` 時必須附上 `audit`：

```json
{
  "date": "YYYY-MM-DD",
  "ai": "AI名稱",
  "top5": [
    {"rank": 1, "code": "1111", "name": "股票一", "score": 85, "decision": "加碼", "reason": "包含具體數字的精簡理由"},
    {"rank": 2, "code": "2222", "name": "股票二", "score": 82, "decision": "進場", "reason": "包含具體數字的精簡理由"},
    {"rank": 3, "code": "3333", "name": "股票三", "score": 78, "decision": "續抱觀望", "reason": "包含具體數字的精簡理由"},
    {"rank": 4, "code": "4444", "name": "股票四", "score": 75, "decision": "續抱觀望", "reason": "包含具體數字的精簡理由"},
    {"rank": 5, "code": "5555", "name": "股票五", "score": 72, "decision": "續抱觀望", "reason": "包含具體數字的精簡理由"}
  ],
  "audit": {
    "status": "passed",
    "issues": [],
    "top5": [
      {"rank": 1, "code": "1111", "score": 85},
      {"rank": 2, "code": "2222", "score": 82},
      {"rank": 3, "code": "3333", "score": 78},
      {"rank": 4, "code": "4444", "score": 75},
      {"rank": 5, "code": "5555", "score": 72}
    ]
  }
}
```

- `top5` 與 `audit.top5` 都必須各有完整 5 筆，且名次、股票與分數一致。
- 若稽核尚有問題，不得使用 `passed`、不得送出排行榜，也不得為了通過驗證直接複製或強制對齊錯誤結果。
- 後端只在儲存當下確認送出榜單與目前 `data.js` 同步；其他 AI 日後更新首頁分數，不會讓已通過稽核的歷史榜單變成矛盾。
- 若無法執行本機指令或呼叫 API，才輸出上述完整 JSON，明確告知使用者需要貼入「匯入 AI 規則稽核結果」欄位；不得假裝已經更新排行榜。

## 完成回報

簡要回報分析日期、第一次 TOP 5、稽核是否通過、發現及修正的問題、最終 TOP 5，以及 `RANKING_SAVED` 的成功結果。
