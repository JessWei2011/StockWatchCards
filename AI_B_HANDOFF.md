# AI 選股改造交接：LLM 不再參與分數或候選篩選

## 決策與背景

目前 `evolution_engine.py` 把 Gemini 的 Google Search 結果用於：

1. 掃描「市場主流產業風口」並給個股產業加分／扣分。
2. 在 LLM 查核的營收、EPS、催化劑、目標價資料上加分／扣分。
3. 在 LLM 查核之前，先以產業風口分數改變候選池優先順序。

這個設計應取消。公開網路新聞、題材文章與 LLM 彙整通常是落後資訊；來源會互相轉載，且公司產業標籤可能誤判（例如緯創被過度簡化為 AI 伺服器概念）。一則分類或敘事錯誤不應影響股票排名、候選資格或資金決策。

**新原則：LLM／網路搜尋只能產生「研究與風險提示」，不得直接或間接改變任何選股分數、候選池、門檻、排序或買進資格。**

本次不要求刪除公司資料夾的分類功能；`classify_stock()` 可繼續只用於報表歸檔與畫面顯示。分類絕不可用於選股計分。

---

## 改造目標

選股流程改成：

```text
本機可重現的量價／技術／籌碼規則
    → 固定規則產出候選與排序
    → （可選）LLM 只為候選產生研究卡：正反論點、風險、待查資料
    → 人工決策與部位／停損規則
```

注意：研究卡不得使用「建議買進／強力買進／目標價上看」等指令性語言；不得顯示為選股理由或排名依據。

---

## 必做修改

### 1. 將 `evolution_engine.py` 的 LLM 產業風口完全移出選股流程

以下函式與資料可保留作為獨立的唯讀市場筆記，或停用；但**不得**在選股管線呼叫後影響資料：

- `fetch_market_hot_sectors()`（約 897 行）
- `match_stock_to_hot_sectors()`
- `SECTOR_CACHE_FILE`／`market_hot_sectors_cache.json`

移除或改寫 `main()` 中的「Step 1 先產業後個股」邏輯。執行選股時不應因 `hot_sectors` 的內容不同而產生不同候選或排序。

**特別要移除：**

- `pre_audit_score = candidate['score'] + ({5: 18, 4: 12, 3: 6}...)`
- 使用 `pre_audit_score` 排序 `momentum_pool`、`dip_pool`、`watch_pool` 的程式。
- `evaluate_holistic_score()` 約 1214 行中 `sec_bonus` 的 +22／+16／+9／+3 分。
- 沒有匹配熱門產業時的 `score -= 6.0`。
- 輸出中的「踩中市場主流風口」選股理由。

候選池只能以 `candidate['score']` 與既有、明確定義的技術狀態條件排序。若需排序鍵，請使用穩定的次排序，例如 `(score, code)`，避免同分時無法重現。

### 2. 將所有 LLM 查核欄位移出 `holistic_score`

`evaluate_holistic_score()` 目前會根據 `audit_data` 加減分，以下都要取消：

- LLM 查得的月營收 MoM／YoY：`rev_bonus`
- LLM 查得的 EPS：`earn_bonus`
- LLM 判讀的正負催化劑：`cat_bonus`
- LLM 查得的法人目標價：`target_bonus`
- 因處置／其他 LLM 文字造成的任何排名分數變動（若有）

改造後的唯一分數來源是本機既有量化掃描器產生的 `candidate['score']`。可以保留欄位名稱 `holistic_score` 以降低其他模組改動，但其值**必須精確等於** `round(candidate['score'], 1)`，且 `holistic_reasons` 只能包含該本機分數已使用的理由。

LLM API 缺失、超時、回覆格式錯誤、回覆內容改變，都不得改變：

- 候選池成員
- 排名順序
- `holistic_score`
- 合格門檻判定

### 3. LLM 研究卡改成「風險／反證模式」且預設關閉

保留 Gemini 功能時，改為獨立、可選的 `--research` 或設定旗標（預設 `False`）。主選股命令不應發出網路搜尋請求。

研究卡資料建議寫到新檔案 `ai_research_cards.json`，每一檔資料至少包括：

```json
{
  "code": "3231",
  "name": "緯創",
  "as_of_date": "YYYY-MM-DD",
  "generated_at": "ISO-8601 timestamp",
  "data_cutoff": "YYYY-MM-DD",
  "sources": [
    {"url": "https://...", "publisher": "...", "published_at": "YYYY-MM-DD", "source_type": "official|news|unknown"}
  ],
  "supporting_facts": ["僅陳述可核對的事實"],
  "counter_evidence_and_risks": ["可能推翻投資論點的資料或風險"],
  "questions_for_human_review": ["下次財報／公告要確認什麼"],
  "data_quality_flags": ["news_lag", "duplicate_source", "no_primary_source"],
  "disclaimer": "此研究卡不參與評分、排序或投資建議。"
}
```

Prompt 必須要求：

- 優先公司公告、交易所重大訊息、財報、法說會等一手來源；新聞只可作為線索。
- 每項事實附 URL 與日期；缺資料就寫 `unknown`，不可猜測。
- 至少提出一項反證或風險；不得只蒐集支持題材的內容。
- 對「題材受惠」、「AI 概念股」等敘事標記為 `news_lag`，不得轉為正面結論。
- 不輸出買、賣、目標價、預期報酬或分數。

研究卡僅能在報告新增「AI 研究卡（不計分）」區塊，且必須清楚顯示資料截止日、來源與上述免責文字。若未執行 `--research`，顯示「未執行 AI 研究；不影響排名」，不能把舊快取偽裝成今日查核。

### 4. 更新輸出與文件，使使用者不會誤解

更新 `write_evolution_ranking_md()`、`generate_ai_evolution_log()` 與相關輸出文字：

- 移除「先產業新聞與法說會掃描 → 綜合加權排榜」、「全維度評分」等描述。
- 移除熱門產業表格作為排行榜的正當性依據；若保留，放在獨立的市場觀察區，標題標示「不計分、可能為落後資訊」。
- 排名表的分數欄改稱「量化規則分數」而非「終極實戰評分」。
- 移除目標價、催化劑、LLM 查核狀態作為排名欄位或特徵；可移到研究卡連結／附錄。
- 更新 `stock_winrate_ranking_gemini.md` 的方法論說明，明示產業分類只供顯示或歸檔，沒有共振加分。

請不要改動 `batch_scanner.py`／`batch_scanner_gemini.py` 中純由本機價格資料導出的族群計數或「族群共振」訊號，除非它們實際使用 LLM 分類、LLM 搜尋或新聞題材。這次變更的範圍是 **LLM／網路新聞造成的分類、題材與基本面計分**。

---

## 測試與驗收（必須完成）

請新增或調整 `test_evolution_engine.py`，至少覆蓋下列測試。測試不得需要 API key、網路或真實市場資料。

1. **熱門產業不影響評分**
   - 準備同一個 `candidate`，分別傳入空 `hot_sectors` 與一個 heat level 5 且完全匹配的產業。
   - 兩次 `holistic_score` 必須相同，並等於原始 `candidate['score']`。

2. **LLM 審計資料不影響評分**
   - 同一個 `candidate` 的 `audit_data` 分別放入極度正面與極度負面的營收、EPS、催化劑、目標價資料。
   - 分數、候選資格、排序都必須相同。

3. **候選池不受熱門產業影響**
   - 以固定的假候選列表執行候選池選取邏輯兩次，一次提供熱門產業、一次不提供。
   - 程式碼與順序完全相同。

4. **沒有 API key 時可完整執行**
   - 預設流程不得呼叫 `call_gemini_search()`、`call_gemini_rest()` 或寫入 LLM 快取。
   - 應正常產生量化排行榜。

5. **研究模式隔離**
   - 僅在明確啟用 `--research` 時才允許呼叫 LLM。
   - 不論 mock 的 LLM 回覆為何，啟用與未啟用研究模式的排行榜（股票代號、順序、分數）完全一致。

驗收指令請在完成後提供，例如：

```powershell
python -m pytest -q test_evolution_engine.py
python evolution_engine.py
python evolution_engine.py --research
```

若專案沒有 pytest，請沿用現有測試執行方式，並在交付說明中清楚寫出。

---

## 完工定義

完成後，請在交付訊息列出：

1. 修改的檔案與每個檔案的目的。
2. 已移除的 LLM 計分與候選池影響點。
3. 研究模式的啟用方式、輸出位置與預設狀態。
4. 測試指令與實際結果。
5. 已知限制：本機量化分數仍須經歷史樣本外回測，不能視為投資建議。

我會依本文件的「測試與驗收」及程式碼搜尋結果驗收；若仍能找到 LLM／熱門產業資料寫入 `score`、`holistic_score`、`pre_audit_score`、候選池排序鍵或合格門檻，即視為未完成。

---

# 後續任務：修正網頁「重新執行 AI 進化」未啟動 LLM

## 問題原因

`reports_manager_server.py` 的 `_run_evolution_engine()` 目前執行：

```python
[sys.executable, str(EVOLUTION_ENGINE_SCRIPT)]
```

新版 `evolution_engine.py` 預設關閉 LLM，只有傳入 `--research` 才會更新 AI 研究卡。因此使用者按下網頁的「重新執行 AI 進化」後，只執行量化排行與頁面重載，LLM 內容不會改變，看起來像 LLM 失效。

## 必做修改

### 1. 既有 AI 按鈕必須明確啟動研究模式

修改 `reports_manager_server.py`，讓 `/api/batch-scanner-evolution` 觸發的子程序執行：

```text
python evolution_engine.py --research
```

不要恢復任何 LLM 計分。`--research` 只能更新不計分研究卡與相關顯示；股票代號、順序、量化分數及合格資格仍須與未開啟研究模式一致。

建議讓 `_run_evolution_engine()` 接受明確參數，例如 `research_enabled: bool`，由 API 路由傳入，避免將來不清楚子程序執行模式。

### 2. 人工接力重新啟動時必須保留研究模式

`/api/llm-handoff` 收到 Gemini JSON 後，目前也會再次啟動 `_run_evolution_engine()`。這次重新啟動同樣必須帶入 `--research`，否則 `llm_manual_handoff.json` 的回覆不會被研究流程接續處理。

人工接力相關文字不得再出現「繼續評分」，請改為「繼續產生研究卡」或「繼續研究查核」，避免使用者誤以為 LLM 會改變選股分數。

### 3. 更新網頁按鈕與狀態文字

修改 `reports_manager.html`：

- 按鈕文字由「重新執行 AI 進化」改為「更新 AI 研究卡」。
- 執行中顯示「AI 研究中」，不要顯示「AI 查核中」或「繼續評分」。
- 完成後清楚顯示「AI 研究卡已更新；量化排名不受影響」。
- 若名單與順序不變，不要提示使用者查看「分數是否更新」；改為提示查看研究卡的產生時間與內容。
- 保留背景工作進度與錯誤訊息，不能只重新整理頁面而沒有狀態。

若頁面另有純量化重新執行需求，可另設「重新計算量化排名」按鈕並執行不含 `--research` 的模式；但不得讓兩個按鈕名稱或執行模式混淆。本次至少必須確保既有 AI 按鈕會真正進入 `--research`。

### 4. API key 或 LLM 失敗必須在畫面明確顯示

- 沒有 `GEMINI_API_KEY` 時，任務不可顯示為「AI 研究卡已更新」；畫面應顯示未更新原因。
- Gemini 配額、HTTP、解析或人工接力錯誤必須保留在工作狀態中。
- 只有研究卡成功寫入且本次產生時間更新後，才能宣告 AI 研究完成。
- 不要因 LLM 失敗而清空或覆寫上一版有效研究卡。

## 測試與驗收

請新增後端測試或抽出可測試的命令組裝函式，至少證明：

1. `/api/batch-scanner-evolution` 使用的子程序命令包含 `--research`。
2. `/api/llm-handoff` 接收人工回覆後重新啟動的命令仍包含 `--research`。
3. 純量化模式若保留，命令不含 `--research`，且兩種模式不會混用。
4. 模擬成功研究後，頁面／API 狀態可辨識研究卡已更新。
5. 模擬缺少 API key 或 LLM 失敗時，不會誤報成功，也不會覆寫舊研究卡。
6. 執行原有 `test_evolution_engine.py`，確認 LLM 研究模式仍不改變排行、分數或候選資格。

驗收時我會另外檢查：

- `subprocess.Popen` 的實際參數，而不只看按鈕文字。
- 人工接力完成後的第二次子程序參數。
- 前端成功／失敗判定是否根據真實後端狀態。
- 研究模式前後排行榜內容是否完全一致。
- LLM 失敗時舊研究卡是否仍保留。

## 完工回報

AI_B 完成後請列出修改檔案、測試指令與結果，並說明：

- AI 按鈕實際啟動的完整 argv。
- 人工接力重新啟動所使用的模式。
- API key 缺失及 LLM 失敗時畫面如何呈現。
- 如何證明研究模式沒有影響量化排名。

---

# 最終需求：取消 Gemini API，改為單一步驟人工 AI 接力

> 本節是最新且優先的規格，**取代前面關於 `--research` 自動呼叫 Gemini API、API 配額處理及兩階段人工接力的要求**。若舊規格與本節衝突，一律以本節為準。

## 最終操作流程

使用者只需要完成一次人工接力：

```text
按「更新 AI 研究卡」
→ Python 依目前量化候選產生 Prompt
→ 使用者按「複製 Prompt」，貼到 Gemini／ChatGPT 網頁版
→ 使用者將完整 JSON 回覆貼回系統並按「驗證並更新」
→ 後端驗證、保存研究卡
→ 顯示完成 Toast
→ 重新載入下方研究卡／榜單畫面
```

沒有 Step 1／Step 2，也沒有產業風口搜尋。AI 回覆仍然不得影響量化分數、排名、候選資格或買賣判斷。

## 1. 移除自動 Gemini API 流程

修改 `evolution_engine.py` 與 `reports_manager_server.py`：

- 「更新 AI 研究卡」不得呼叫 Gemini REST API。
- 不再因 API key、429、配額、模型名稱、重試或 Search Grounding 進入不同流程。
- 不需要 `GEMINI_API_KEY` 即可完整使用人工接力。
- 不再由按鈕啟動 `python evolution_engine.py --research` 並等待 API。
- 可保留舊 API 函式供其他用途，但本頁人工研究卡流程不可呼叫它們；若無其他使用者，建議移除無用程式碼與舊測試。
- 不得再顯示「API 配額不足」、「等待模型重試」、「第 1／2階段」等訊息。

量化排名仍由既有純量化流程更新。人工 AI 研究卡是一條獨立流程，不要為了產生 Prompt 重跑或改寫量化分數。

## 2. 新的一階段人工接力資料

建議建立以下後端端點（名稱可依現有架構微調，但職責需分離）：

- `POST /api/ai-research/start`
  - 讀取目前量化候單中的候選股。
  - 產生唯一 `prompt_id` 與 Prompt。
  - 將 handoff 狀態設成 `awaiting_response`。
  - 回傳 `prompt_id`、候選代號、建立時間與可複製的 Prompt。
  - 不啟動 LLM、不啟動背景 Python 子程序、不修改排行榜。

- `GET /api/ai-research/status`
  - 回傳 `idle | awaiting_response | validating | completed | error`。
  - 回傳診斷事件、錯誤訊息、建立／完成時間及目前 `prompt_id`。
  - 不回傳 API key 或敏感環境變數。

- `POST /api/ai-research/submit`
  - 接收 `prompt_id` 與 AI 回覆文字。
  - 去除外層 ```json code fence 後解析 JSON。
  - 嚴格驗證研究卡 schema、候選股票完整性及順序。
  - 驗證成功才以原子方式寫入 `ai_research_cards.json`。
  - 驗證失敗保留使用者輸入與上一版有效研究卡，回傳可理解的欄位錯誤。

可沿用 `llm_manual_handoff.json`，但資料模型必須簡化為單一步驟，例如：

```json
{
  "status": "awaiting_response",
  "prompt_id": "雜湊值",
  "kind": "AI 反證與風險研究卡",
  "expected_codes": ["3231", "3491"],
  "prompt": "...",
  "created_at": "ISO-8601",
  "completed_at": null,
  "error": null
}
```

不得再保存 `step`、`total_steps`、`hot_sectors` 或第二階段狀態。

## 3. Prompt 規格

使用者不需要閱讀 Prompt，因此 UI 只需提供複製功能；Prompt 內容仍必須由後端固定模板產生，包含：

- 當前候選股代號、名稱與純量化資料摘要。
- 明確說明 AI 只做反證、風險與待確認事項，不做買賣建議。
- 禁止產業題材、熱門新聞、目標價或情緒直接轉為分數。
- 缺少資料必須填 `unknown`，不可猜測。
- 優先使用一手公開資料；若 AI 無法上網，也必須如實標記來源不足。
- 嚴格要求只輸出 JSON，不加 Markdown 說明。

回覆 schema 沿用：

```json
{
  "cards": [
    {
      "code": "3231",
      "name": "緯創",
      "as_of_date": "YYYY-MM-DD",
      "data_cutoff": "YYYY-MM-DD或unknown",
      "sources": [
        {
          "url": "https://...或unknown",
          "publisher": "發布者或unknown",
          "published_at": "YYYY-MM-DD或unknown",
          "source_type": "official|news|unknown"
        }
      ],
      "supporting_facts": [],
      "counter_evidence_and_risks": ["至少一項"],
      "questions_for_human_review": [],
      "data_quality_flags": ["news_lag|duplicate_source|no_primary_source|unknown"],
      "disclaimer": "此研究卡不參與評分、排序或投資建議。"
    }
  ]
}
```

## 4. 畫面：1 列 × 3 欄

修改 `reports_manager.html`。桌面版使用單列三欄，建議比例 `1fr 1.4fr 1fr`；窄螢幕可自動改成單欄堆疊。

### 左欄：Prompt 複製區

- 標題：「1. 複製 AI Prompt」。
- 顯示候選股數量、Prompt 產生時間與 `prompt_id` 短碼。
- 使用者不需要閱讀 Prompt，**預設不顯示大型 Prompt 文字框**。
- 提供醒目的「複製 Prompt」按鈕。
- 可提供折疊的「查看 Prompt」作為除錯用途，預設收合。
- 複製成功後按鈕短暫顯示「已複製」。

### 中欄：AI 回覆貼上區

- 標題：「2. 貼上 AI 回覆」。
- 一個足夠高度的 JSON 文字區。
- 按鈕名稱：「驗證並更新研究卡」。
- 尚未產生 Prompt 時停用文字區與按鈕。
- 送出期間停用重複提交，顯示「驗證中」。
- 驗證失敗時保留原文字，不得清空，並指出具體錯誤，例如「缺少 cards」、「漏掉 3231」、「第 2 筆缺少 risks」。

### 右欄：狀態與診斷區

- 標題：「3. 執行狀態」。
- 顯示目前狀態：尚未開始、等待貼回、驗證中、完成、錯誤。
- 顯示精簡事件時間線：Prompt 已建立、已複製、收到回覆、JSON 解析成功、股票完整性通過、研究卡已保存、畫面已更新。
- 錯誤訊息直接顯示，不需要展開才能看見。
- 詳細 Debug 可放在折疊區；不得顯示金鑰或完整敏感資料。
- 不再顯示子程序 PID、Gemini API 配額或模型重試，因為此流程不啟動 API 子程序。

## 5. 完成 Toast 與畫面更新

只有後端成功驗證且成功寫入 `ai_research_cards.json` 後才算完成。

- 顯示 Toast：「AI 研究卡更新完成；量化排名未受影響」。
- Toast 顯示約 3～5 秒，並可手動關閉。
- Toast 後重新讀取研究卡及下方榜單顯示，不可整頁刷新或畫面閃爍。
- 下方量化榜單的股票、順序與分數不得因 AI 回覆改變。
- 若回覆驗證或保存失敗，不顯示成功 Toast、不重新載入榜單、不覆寫上一版研究卡。

## 6. 狀態機

前後端統一使用以下狀態，避免目前「失敗」與「等待人工接力」同時出現：

```text
idle
  → awaiting_response
  → validating
  → completed

awaiting_response／validating
  → error
  → 使用者修正同一份回覆後重新提交
```

重新按「更新 AI 研究卡」會建立新的 `prompt_id`。舊視窗提交舊 `prompt_id` 時，應回覆「Prompt 已更新，請使用最新 Prompt」，不可寫入資料。

## 7. AI_B 實作順序

1. 先建立單一步驟 handoff 資料模型與三個後端端點。
2. 抽出純函式：候選讀取、Prompt 產生、JSON 清理、schema 驗證、原子寫入。
3. 移除本頁對 `--research`、Gemini API 與背景子程序的依賴。
4. 將原有兩階段前端替換成 1 列 × 3 欄 UI。
5. 加入完成 Toast 與局部資料重新載入。
6. 補齊後端與前端狀態測試。
7. 執行既有量化隔離測試，確認排名完全不受 AI 回覆影響。

## 8. 驗收條件

AI_B 必須新增測試，至少覆蓋：

1. 按開始後只產生 Prompt，不呼叫 `subprocess.Popen`、Gemini REST 或任何 LLM API。
2. 不設定 `GEMINI_API_KEY` 仍可完成開始、貼回、驗證與保存。
3. 合法 `cards` JSON 可保存並回傳 `completed`。
4. Markdown code fence 包住的合法 JSON 可正常解析。
5. JSON 錯誤、缺欄位、漏股票、股票順序錯誤及過期 `prompt_id` 都會被拒絕。
6. 驗證失敗不覆寫既有 `ai_research_cards.json`。
7. 成功後研究卡更新，但量化榜單股票代號、順序與分數完全相同。
8. 前端不整頁刷新；成功顯示 Toast，失敗不顯示成功 Toast。
9. 桌面版為 1 列 × 3 欄，窄螢幕可正常改為單欄。
10. 原有測試全部通過，且沒有殘留「第 1／2 階段」、「繼續評分」或 API 配額引導文字。

## 9. AI_B 完工回報格式

完成後請回報：

- 修改的檔案與用途。
- 新增／調整的端點。
- 人工接力狀態流程。
- 研究卡驗證與防覆寫方式。
- 測試指令、通過數量與實際結果。
- 證明沒有呼叫 LLM API，且 AI 回覆未改變量化排名。
