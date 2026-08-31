# 🔗 AI 股市型態視覺教學介面 (pattern_viewer) 整合說明指南

本文件記錄了 `pattern_viewer` 模組如何整合進 `reports_manager.html`，並以 HTML 個股報表與 Markdown 分析為資料來源。

---

## 📌 一、 架構設計特點（為什麼可以零風險整合）

1. **命名空間隔離 (Namespace Isolation)**：
   - 所有的 JS 模組均封裝在全域命名空間 `window.PatternParser`、`window.PatternEngine` 與 `window.PatternChart` 中，完全不與主專案全域變數產生衝突。
2. **標準化 Data-Driven API**：
   - HTML 報告由 `PatternParser` 轉成圖表序列；Markdown 分析由 `/api/cards` 轉成標準卡片物件。

---

## 🛠️ 二、 後續整合方案選擇

未來確認沒有 Bug 後，可以選擇以下三種方式之一整合至主專案：

### 方案 A：在 `reports_manager.html` 中新增「型態視覺教學」分頁 (推薦 🌟)

在 `reports_manager.html` 的選單頂部新增一個 `<iframe>` 視窗或分頁 Tab：
```html
<!-- reports_manager.html 新增型態教學視窗 -->
<div class="tab-content" id="pattern-tab">
  <iframe src="pattern_viewer/index.html" style="width:100%; height:750px; border:none;"></iframe>
</div>
```

### 方案 B：在卡片背面點擊「查看 K線圖形教學」彈出 Modal 視窗

在 `reports_manager.html` 繪製卡片時，於背面加一顆按鈕：
```javascript
// render.js 卡片背面元件
`<button onclick="openPatternModal('${card.code}', '${card.name}')">🎓 查看圖像化型態教學</button>`
```
並在開啟 Modal 時呼叫：
```javascript
function openPatternModal(code, name) {
  // 顯示 Modal DOM
  document.getElementById('patternModal').style.display = 'block';
  // 載入股票數據並動態畫圖
  PatternViewer.loadStock(code);
}
```

### 方案 C：將 `pattern_viewer` 的 JS/CSS 檔案直接引入 `reports_manager.html`

只需在 `index.html` 的 `<head>` 與 `<body>` 引入：
```html
<link rel="stylesheet" href="pattern_viewer/css/viewer.css">
<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
<script src="pattern_viewer/js/parser.js"></script>
<script src="pattern_viewer/js/pattern_engine.js"></script>
<script src="pattern_viewer/js/chart_engine.js"></script>
```

---

## 🧪 三、 整合前 Bug 檢查清單 (Pre-Integration Checklist)

在請 AI 將本專案正式合併整合前，請確認以下事項：
- [ ] 執行 `pattern_viewer/start_viewer.bat` 能正常開啟網頁。
- [ ] 切換 `2330` / `2303` / `3034` 時，K線與 RSI/MACD/KD 圖表流暢更新。
- [ ] 切換 N字、V轉、W底、M頭、箱型突破時，K線圖上的螢光向量連線與 P1~P4 轉折氣泡無位置錯位。
- [ ] 滑鼠在 K 線圖移動時，5 個圖表的十字光標數據完全同步。
- [ ] 瀏覽器 Console 無任何 JavaScript 報錯。

驗證完畢後，只需對 AI 發起指令：「**測試完成無 bug，請依照 INTEGRATION_GUIDE.md 將 pattern_viewer 整合進主專案**」，AI 即會進行安全合併。
