/**
 * PatternViewer application controller.
 *
 * Keeps the teaching UI independent from its host page so the same component can
 * run in pattern_viewer/index.html today and inside reports_manager.html later.
 */
(function () {
  'use strict';

  class PatternViewerApp {
    constructor(root, options = {}) {
      this.root = typeof root === 'string' ? document.querySelector(root) : root;
      if (!this.root) throw new Error('PatternViewer: 找不到掛載容器');

      this.options = {
        reportsIndexUrl: '/api/reports-index',
        cardsUrl: '/api/cards',
        reportsBaseUrl: '/reports/',
        initialCode: '',
        ...options
      };
      this.cards = [];
      this.cardByCode = {};
      this.reportsIndex = [];
      this.currentStockData = null;
      this.currentCode = null;
      this.currentView = 'card';
      this.requestSerial = 0;
      this.destroyed = false;
      this.eventController = new AbortController();
      this.resizeObserver = null;
    }

    q(selector) {
      return this.root.querySelector(selector);
    }

    async init() {
      this.bindEvents();
      this.observeSize();
      await this.loadSources();
      if (this.destroyed) return this;

      this.populateStockSelect();
      const select = this.q('#stockSelect');
      const initialCode = this.options.initialCode || (select && select.value);
      if (initialCode) {
        await this.loadStock(initialCode);
      } else {
        this.showEmpty('目前沒有可選擇的個股', '請先產生一份個股報表，再回到型態教學。');
      }
      return this;
    }

    async loadSources() {
      const fallbackCards = typeof STOCK_CARDS !== 'undefined' ? STOCK_CARDS : [];
      let cards = fallbackCards;

      console.log(`[PatternViewer Debug] 🚀 loadSources: 請求 ${this.options.reportsIndexUrl} 與 ${this.options.cardsUrl}`);
      try {
        const [indexResult, cardsResult] = await Promise.allSettled([
          fetch(this.options.reportsIndexUrl, { cache: 'no-store' }),
          fetch(this.options.cardsUrl, { cache: 'no-store' })
        ]);

        console.log(`[PatternViewer Debug] indexResult status:`, indexResult.status);
        if (indexResult.status === 'fulfilled') {
          console.log(`[PatternViewer Debug] indexResult HTTP status:`, indexResult.value.status, indexResult.value.ok);
          if (indexResult.value.ok) {
            const payload = await indexResult.value.json();
            this.reportsIndex = Array.isArray(payload) ? payload : (payload.reports || []);
            console.log(`[PatternViewer Debug] ✅ 成功載入 reportsIndex，共 ${this.reportsIndex.length} 筆`);
          } else {
            console.error(`[PatternViewer Debug] ❌ reportsIndex HTTP 錯誤:`, indexResult.value.status);
          }
        } else {
          console.error(`[PatternViewer Debug] ❌ reportsIndex fetch 拋出異常:`, indexResult.reason);
        }

        if (cardsResult.status === 'fulfilled' && cardsResult.value.ok) {
          const payload = await cardsResult.value.json();
          const apiCards = payload.cards || payload;
          cards = Array.isArray(apiCards) ? apiCards : Object.values(apiCards || {});
        }

        this.cards = cards.filter(card => card && card.code);
        this.cardByCode = {};
        this.cards.forEach(card => { this.cardByCode[String(card.code)] = card; });
      } catch (err) {
        console.error(`[PatternViewer Debug] ❌ loadSources 總體錯誤:`, err);
      }
    }

    async refresh({ reloadCurrent = false } = {}) {
      const previousCode = this.currentCode;
      await this.loadSources();
      if (this.destroyed) return false;
      this.populateStockSelect();

      const select = this.q('#stockSelect');
      if (previousCode && select.querySelector(`option[value="${CSS.escape(previousCode)}"]`)) {
        select.value = previousCode;
      }
      if (reloadCurrent && previousCode) {
        return this.loadStock(previousCode);
      }
      return true;
    }

    populateStockSelect() {
      const select = this.q('#stockSelect');
      if (!select) return;

      const availableMap = new Map(this.reportsIndex.map(item => [String(item.code), item]));
      const candidatesByCode = new Map();

      // 以真實報表為主體
      this.reportsIndex.forEach(item => {
        const code = String(item.code);
        const card = this.cardByCode[code];
        candidatesByCode.set(code, {
          code: code,
          name: item.name || (card ? card.name : code),
          group: (card && card.group) ? card.group : (item.path ? item.path.split('/')[0] : '未分類'),
          decision: card ? card.decision : '技術指標',
          winRate: card ? (card.winRate || 0) : 0
        });
      });

      // 補足有卡片的其他項目
      this.cards.forEach(card => {
        const code = String(card.code);
        if (!candidatesByCode.has(code)) {
          candidatesByCode.set(code, card);
        }
      });

      const candidates = Array.from(candidatesByCode.values());
      const groups = {};
      candidates.forEach(card => {
        const group = card.group || '未分類';
        (groups[group] = groups[group] || []).push(card);
      });

      select.innerHTML = '';
      Object.keys(groups).sort((a, b) => a.localeCompare(b, 'zh-Hant')).forEach(group => {
        const optgroup = document.createElement('optgroup');
        optgroup.label = group;
        groups[group]
          .sort((a, b) => (b.winRate || 0) - (a.winRate || 0))
          .forEach(card => {
            const option = document.createElement('option');
            option.value = card.code;
            const hasRep = availableMap.has(String(card.code));
            const reportMark = hasRep ? '' : '｜尚無報表';
            option.textContent = `${card.code} ${card.name || ''} (${card.decision || '技術指標'}${reportMark})`;
            optgroup.appendChild(option);
          });
        select.appendChild(optgroup);
      });
    }

    async loadStock(code) {
      const normalizedCode = String(code || '').trim();
      const requestId = ++this.requestSerial;
      this.currentCode = normalizedCode;
      this.showLoading(normalizedCode);

      console.log(`[PatternViewer Debug] 🔄 開始載入股票: "${normalizedCode}"`);
      console.log(`[PatternViewer Debug] 目前全域 reportsIndex 總數:`, this.reportsIndex.length);

      // 如果尚未抓取索引或索引為空，主動補抓一次
      if (!this.reportsIndex || this.reportsIndex.length === 0) {
        console.log(`[PatternViewer Debug] reportsIndex 為空，立即呼叫 loadSources()...`);
        await this.loadSources();
        this.populateStockSelect();
      }

      console.log(`[PatternViewer Debug] reportsIndex 更新後總數:`, this.reportsIndex.length);
      console.log(`[PatternViewer Debug] reportsIndex 前 5 筆:`, this.reportsIndex.slice(0, 5));

      let entry = this.reportsIndex.find(item => String(item.code).trim() === normalizedCode);
      console.log(`[PatternViewer Debug] 比對代號 "${normalizedCode}" 結果:`, entry);

      if (!entry) {
        console.warn(`[PatternViewer Debug] ❌ 在 reportsIndex 找不到代號 "${normalizedCode}"！`);
        console.warn(`[PatternViewer Debug] 所有可用的代號清單:`, this.reportsIndex.map(x => x.code));
        this.currentStockData = null;
        this.updateAiCardBox(normalizedCode);
        this.showEmpty(
          `${normalizedCode} 尚無原始報表`,
          `【除錯資訊】在前端索引 (共 ${this.reportsIndex.length} 筆) 中找不到代號 "${normalizedCode}"。請打開 F12 Console 查看詳細清單。`
        );
        return false;
      }

      const fetchReport = async reportEntry => {
        const reportUrl = this.options.reportsBaseUrl + reportEntry.path
          .split('/')
          .map(segment => encodeURIComponent(segment))
          .join('/');
        console.log(`[PatternViewer Debug] 🌐 Fetch URL:`, reportUrl);
        return fetch(reportUrl, { cache: 'no-store' });
      };

      let parsed;
      try {
        let response = await fetchReport(entry);
        console.log(`[PatternViewer Debug] 🌐 Fetch Response Status:`, response.status);
        if (!response.ok) {
          console.warn(`[PatternViewer Debug] 首次抓取失敗 (HTTP ${response.status})，嘗試重新整理 sources...`);
          await this.loadSources();
          if (requestId !== this.requestSerial || this.destroyed) return false;
          this.populateStockSelect();
          entry = this.reportsIndex.find(item => String(item.code).trim() === normalizedCode);
          if (!entry) throw new Error(`刷新索引後仍找不到代號 ${normalizedCode} 的報表路徑`);
          response = await fetchReport(entry);
        }
        if (!response.ok) throw new Error(`讀取 ${entry.path} 時伺服器回傳 HTTP ${response.status}`);
        const htmlText = await response.text();
        console.log(`[PatternViewer Debug] 📄 成功讀取 HTML 內容，長度:`, htmlText.length);
        if (requestId !== this.requestSerial || this.destroyed) return false;

        parsed = PatternParser.parseStockHtml(htmlText);
        console.log(`[PatternViewer Debug] 📊 Parser 解析結果:`, parsed);
        if (!parsed || !parsed.dates || parsed.dates.length === 0) {
          throw new Error(`報表 ${entry.path} 中找不到可解析的 K 線資料`);
        }
      } catch (error) {
        console.error(`[PatternViewer Debug] 💥 loadStock 錯誤:`, error);
        if (requestId !== this.requestSerial || this.destroyed) return false;
        this.currentStockData = null;
        this.updateAiCardBox(normalizedCode);
        this.showEmpty(`${normalizedCode} 報表載入失敗`, `【除錯錯誤】${error.message}`);
        return false;
      }

      this.currentStockData = parsed;
      this.updateAiCardBox(normalizedCode);
      try {
        this.updateUI();
        return true;
      } catch (error) {
        this.showEmpty(`${normalizedCode} 圖表顯示失敗`, error.message || '無法繪製圖表');
        return false;
      }
    }

    getActiveOverlay() {
      // 依使用者指示全面移除型態比對與教學折線，回歸純粹、標準、乾淨的看盤終端。
      return null;
    }

    updateAiCardBox(code) {
      const box = this.q('#statCardBox');
      if (!box) return;
      const card = this.cardByCode[code];
      if (!card) {
        box.style.display = 'none';
        this.updateAnalysisPanel(null);
        return;
      }
      box.style.display = '';
      const winText = card.winRate ? (String(card.winRate).includes('%') ? card.winRate : `${card.winRate}%`) : '—';
      const rrText = card.rr ? `｜風報比 ${card.rr}` : '';
      this.q('#statCardDate').textContent = card.date || '—';
      this.q('#statCardDecision').textContent =
        `${card.decision || '—'}｜勝率 ${winText}${rrText}｜${card.pattern || '無明確型態'}`;
      this.updateAnalysisPanel(card);
    }

    updateAnalysisPanel(card) {
      const section = this.q('#patternAnalysisSection');
      const body = this.q('#patternAnalysisBody');
      if (!section || !body) return;
      if (!card) {
        section.hidden = true;
        body.innerHTML = '';
        return;
      }

      const winText = card.winRate ? (String(card.winRate).includes('%') ? card.winRate : `${card.winRate}%`) : '—';
      const rrText = card.rr ? `｜風報比 ${card.rr}` : '';
      section.hidden = false;
      this.q('#patternAnalysisTitle').textContent = `${card.code} ${card.name}・技術分析`;
      this.q('#patternAnalysisMeta').textContent =
        `${card.date || '日期未知'}｜${card.decision || '未定'}｜勝率 ${winText}${rrText}｜${card.pattern || '無明確型態'}`;

      if (card.raw && typeof renderRaw === 'function') {
        body.innerHTML = renderRaw(card.raw);
      } else {
        body.innerHTML = '';
        const plain = document.createElement('pre');
        plain.className = 'analysis-plain';
        plain.textContent = card.raw || card.action || '這張 AI 卡片沒有完整分析文字。';
        body.appendChild(plain);
      }
    }

    updateUI() {
      if (!this.currentStockData) return;
      const overlay = this.getActiveOverlay();
      const chart = this.q('#echart-main');
      const empty = this.q('#patternEmptyState');
      if (chart) chart.hidden = false;
      if (empty) empty.hidden = true;

      if (chart) {
        const toggleBoll = this.q('#toggleBoll');
        const toggleMa = this.q('#toggleMa');
        ChartEngine.render(chart, this.currentStockData, overlay, {
          showBoll: toggleBoll ? toggleBoll.checked : true,
          showMa: toggleMa ? toggleMa.checked : true
        });
      }

      const chartTitle = this.q('#chartTitle');
      if (chartTitle) chartTitle.textContent = `${this.currentStockData.title} — K線 / RSI / MACD / KD / 成交量圖`;

      const cardStockName = this.q('#cardStockName');
      if (cardStockName) cardStockName.textContent = this.currentStockData.title;

      const candles = this.currentStockData.candles || [];
      const lastCandle = candles[candles.length - 1];
      const statCurrentPrice = this.q('#statCurrentPrice');
      if (statCurrentPrice && lastCandle) statCurrentPrice.textContent = `$${lastCandle[1]}`;

      const statHighLow = this.q('#statHighLow');
      if (statHighLow && candles.length) {
        const highest = Math.max(...candles.map(item => item[3]));
        const lowest = Math.min(...candles.map(item => item[2]));
        statHighLow.textContent = `${highest} / ${lowest}`;
      }

      const statRsiTags = this.q('#statRsiTags');
      if (statRsiTags) {
        const cleanCode = String(this.currentCode || '').split('.')[0].trim();
        const allCards = (window.cardsByCode || (window.parent && window.parent.cardsByCode) || (typeof cardsByCode !== 'undefined' ? cardsByCode : {})) || {};
        const card = allCards[cleanCode] || allCards[this.currentCode] || null;
        let rsiTagsStr = card ? (card.rsiTags || '') : '';
        if (!rsiTagsStr && card && card.raw) {
          const m = String(card.raw).match(/RSI\s*(?:指標)?標籤[】\]\s\*]*[：:]\s*([^\r\n]+)/i);
          if (m) rsiTagsStr = m[1].trim();
        }
        
        if (rsiTagsStr) {
          const parts = rsiTagsStr.split(/[、,]/).map(s => s.trim()).filter(Boolean);
          statRsiTags.innerHTML = parts.map(t => {
            let style = 'background:rgba(148,163,184,0.15); color:#cbd5e1; border:1px solid rgba(148,163,184,0.3);';
            if (/過熱|超買|頂背離|死亡交叉|死叉/.test(t)) {
              style = 'background:rgba(239,68,68,0.18); color:#fca5a5; border:1px solid rgba(239,68,68,0.38);';
            } else if (/超跌|底背離|黃金交叉|金叉/.test(t)) {
              style = 'background:rgba(234,179,8,0.18); color:#fde047; border:1px solid rgba(234,179,8,0.38);';
            } else if (/鈍化/.test(t)) {
              style = 'background:rgba(34,197,94,0.18); color:#86efac; border:1px solid rgba(34,197,94,0.38);';
            } else if (/多方|推進/.test(t)) {
              style = 'background:rgba(56,189,248,0.15); color:#7dd3fc; border:1px solid rgba(56,189,248,0.3);';
            }
            return `<span style="display:inline-block; padding:2px 8px; border-radius:4px; font-size:11.5px; font-weight:750; ${style}">${t}</span>`;
          }).join('');
        } else {
          statRsiTags.innerHTML = `<span style="display:inline-block; padding:2px 8px; border-radius:4px; font-size:11.5px; font-weight:750; background:rgba(148,163,184,0.15); color:#94a3b8; border:1px solid rgba(148,163,184,0.3);">RSI 數據正常</span>`;
        }
      }

      // 渲染「📊 VOL 指標狀態」
      const statVolTags = this.q('#statVolTags') || document.getElementById('statVolTags');
      if (statVolTags) {
        const cleanCode = String(this.currentCode || '').split('.')[0].trim();
        const allCards = (window.cardsByCode || (window.parent && window.parent.cardsByCode) || (typeof cardsByCode !== 'undefined' ? cardsByCode : {})) || {};
        const card = allCards[cleanCode] || allCards[this.currentCode] || null;
        let volTagsStr = card ? (card.volTags || card.vol_tags || '') : '';
        if (!volTagsStr && card && card.raw) {
          const m = String(card.raw).match(/VOL\s*(?:指標)?標籤[】\]\s\*]*[：:]\s*([^\r\n]+)/i) || String(card.raw).match(/量能標籤[】\]\s\*]*[：:]\s*([^\r\n]+)/i);
          if (m) volTagsStr = m[1].trim();
        }

        if (volTagsStr) {
          const parts = volTagsStr.split(/[、,]/).map(s => s.trim()).filter(Boolean);
          statVolTags.innerHTML = parts.map(t => {
            let style = 'background:rgba(148,163,184,0.15); color:#cbd5e1; border:1px solid rgba(148,163,184,0.3);';
            if (/天量|倒貨|頂背離|死亡交叉|死叉|退潮/.test(t)) {
              style = 'background:rgba(239,68,68,0.18); color:#fca5a5; border:1px solid rgba(239,68,68,0.38);';
            } else if (/突破|黃金交叉|金叉|窒息量|洗淨/.test(t)) {
              style = 'background:rgba(234,179,8,0.18); color:#fde047; border:1px solid rgba(234,179,8,0.38);';
            } else if (/滾量|量價齊揚|主升/.test(t)) {
              style = 'background:rgba(34,197,94,0.18); color:#86efac; border:1px solid rgba(34,197,94,0.38);';
            } else if (/溫和|增量/.test(t)) {
              style = 'background:rgba(56,189,248,0.15); color:#7dd3fc; border:1px solid rgba(56,189,248,0.3);';
            }
            return `<span style="display:inline-block; padding:2px 8px; border-radius:4px; font-size:11.5px; font-weight:750; ${style}">${t}</span>`;
          }).join('');
        } else {
          statVolTags.innerHTML = `<span style="display:inline-block; padding:2px 8px; border-radius:4px; font-size:11.5px; font-weight:750; background:rgba(148,163,184,0.15); color:#94a3b8; border:1px solid rgba(148,163,184,0.3);">常態量能換手</span>`;
        }
      }

      // 渲染「🌊 MACD 指標狀態」
      const statMacdTags = this.q('#statMacdTags') || document.getElementById('statMacdTags');
      if (statMacdTags) {
        const cleanCode = String(this.currentCode || '').split('.')[0].trim();
        const allCards = (window.cardsByCode || (window.parent && window.parent.cardsByCode) || (typeof cardsByCode !== 'undefined' ? cardsByCode : {})) || {};
        const card = allCards[cleanCode] || allCards[this.currentCode] || null;
        let macdTagsStr = card ? (card.macdTags || card.macd_tags || '') : '';
        if (!macdTagsStr && card && card.raw) {
          const m = String(card.raw).match(/MACD\s*(?:指標)?標籤[】\]\s\*]*[：:]\s*([^\r\n]+)/i);
          if (m) macdTagsStr = m[1].trim();
        }

        if (macdTagsStr) {
          const parts = macdTagsStr.split(/[、,]/).map(s => s.trim()).filter(Boolean);
          statMacdTags.innerHTML = parts.map(t => {
            let style = 'background:rgba(148,163,184,0.15); color:#cbd5e1; border:1px solid rgba(148,163,184,0.3);';
            if (/死亡交叉|死叉|翻綠|頂背離|轉弱/.test(t)) {
              style = 'background:rgba(239,68,68,0.18); color:#fca5a5; border:1px solid rgba(239,68,68,0.38);';
            } else if (/金叉|黃金交叉|翻紅|底背離|反彈|起漲/.test(t)) {
              style = 'background:rgba(234,179,8,0.18); color:#fde047; border:1px solid rgba(234,179,8,0.38);';
            } else if (/零軸上強勢多頭|多頭發散|強勢攻擊/.test(t)) {
              style = 'background:rgba(34,197,94,0.18); color:#86efac; border:1px solid rgba(34,197,94,0.38);';
            } else if (/多方|波段/.test(t)) {
              style = 'background:rgba(56,189,248,0.15); color:#7dd3fc; border:1px solid rgba(56,189,248,0.3);';
            }
            return `<span style="display:inline-block; padding:2px 8px; border-radius:4px; font-size:11.5px; font-weight:750; ${style}">${t}</span>`;
          }).join('');
        } else {
          statMacdTags.innerHTML = `<span style="display:inline-block; padding:2px 8px; border-radius:4px; font-size:11.5px; font-weight:750; background:rgba(148,163,184,0.15); color:#94a3b8; border:1px solid rgba(148,163,184,0.3);">MACD 數據正常</span>`;
        }
      }
      if (this.currentStockData && this.currentStockData.dates && this.currentStockData.dates.length) {
        window.updateFocusHUD(this.currentStockData.dates.length - 1, this.currentStockData);
      }
      requestAnimationFrame(() => this.resize());
    }

    showLoading(code) {
      this.showEmpty(`正在載入 ${code || '個股'}…`, '讀取真實報表與技術指標資料。', false);
    }

    showEmpty(title, detail, destroyChart = true) {
      const chart = this.q('#echart-main');
      const empty = this.q('#patternEmptyState');
      if (destroyChart) ChartEngine.destroy();
      chart.hidden = true;
      empty.hidden = false;
      this.q('#patternEmptyTitle').textContent = title;
      this.q('#patternEmptyDetail').textContent = detail;
    }

    bindEvents() {
      const signal = this.eventController.signal;
      this.q('#stockSelect').addEventListener('change', event => this.loadStock(event.target.value), { signal });
      this.q('#viewToggleContainer').addEventListener('click', event => {
        const button = event.target.closest('.btn-pattern');
        if (!button) return;
        this.root.querySelectorAll('#viewToggleContainer .btn-pattern').forEach(item => item.classList.remove('active'));
        button.classList.add('active');
        this.currentView = button.dataset.view;
        this.updateUI();
      }, { signal });
      this.q('#toggleBoll').addEventListener('change', () => this.updateUI(), { signal });
      this.q('#toggleMa').addEventListener('change', () => this.updateUI(), { signal });

      const fullscreenBtn = this.q('#toggleFullscreenChart');
      if (fullscreenBtn) {
        fullscreenBtn.addEventListener('click', () => {
          if (typeof window.toggleChartFullscreen === 'function') {
            window.toggleChartFullscreen();
          }
        }, { signal });
      }
    }

    observeSize() {
      if (typeof ResizeObserver === 'undefined') return;
      this.resizeObserver = new ResizeObserver(() => this.resize());
      this.resizeObserver.observe(this.root);
    }

    resize() {
      ChartEngine.resize();
    }

    destroy() {
      this.destroyed = true;
      this.requestSerial += 1;
      this.eventController.abort();
      if (this.resizeObserver) this.resizeObserver.disconnect();
      ChartEngine.destroy();
    }
  }

  window.updateFocusHUD = function(idx, stockData) {
    if (!stockData || !stockData.dates || idx < 0 || idx >= stockData.dates.length) return;
    
    const date = stockData.dates[idx];
    const isLatest = (idx === stockData.dates.length - 1);
    const dateEl = document.getElementById('hudFocusDate');
    if (dateEl) {
      dateEl.textContent = isLatest ? `${date} (最新收盤)` : `📅 ${date}`;
      dateEl.style.color = isLatest ? '#38bdf8' : '#fbbf24';
      dateEl.style.borderColor = isLatest ? 'rgba(56,189,248,0.35)' : 'rgba(251,191,36,0.4)';
    }

    const candle = stockData.candles && stockData.candles[idx]; // [open, close, lowest, highest]
    if (candle) {
      const open = candle[0];
      const close = candle[1];
      const low = candle[2];
      const high = candle[3];
      const prevClose = idx > 0 && stockData.candles[idx - 1] ? stockData.candles[idx - 1][1] : open;
      const diff = close - prevClose;
      const pct = prevClose ? (diff / prevClose * 100) : 0;
      const isUp = diff >= 0;
      const color = isUp ? '#ef4444' : '#10b981';

      const ocEl = document.getElementById('hudOpenClose');
      if (ocEl) {
        const oColor = open >= prevClose ? '#ef4444' : '#10b981';
        ocEl.innerHTML = `<span style="color:${oColor}">${open.toFixed(2)}</span> ／ <span style="color:${color}; font-weight:800;">${close.toFixed(2)}</span>`;
      }

      const hlEl = document.getElementById('hudHighLow');
      if (hlEl) {
        hlEl.innerHTML = `<span style="color:#ef4444">${high.toFixed(2)}</span> ／ <span style="color:#10b981">${low.toFixed(2)}</span>`;
      }

      const chgEl = document.getElementById('hudChange');
      if (chgEl) {
        chgEl.style.color = color;
        chgEl.textContent = `${isUp ? '+' : ''}${diff.toFixed(2)} (${isUp ? '+' : ''}${pct.toFixed(2)}%)`;
      }
    }

    // Volume & Volume Moving Averages
    const vol = stockData.volumes && stockData.volumes[idx];
    const volEl = document.getElementById('hudVolume');
    if (volEl) {
      if (vol != null && !isNaN(vol)) {
        volEl.textContent = `${Number(vol).toLocaleString()} 張`;
      } else {
        volEl.textContent = '—';
      }
    }

    const v5 = stockData.vma5 && stockData.vma5[idx];
    const v20 = stockData.vma20 && stockData.vma20[idx];
    const volMaEl = document.getElementById('hudVolMa');
    if (volMaEl) {
      const s5 = (v5 != null && !isNaN(v5)) ? `${Number(v5).toLocaleString()}` : '—';
      const s20 = (v20 != null && !isNaN(v20)) ? `${Number(v20).toLocaleString()}` : '—';
      volMaEl.innerHTML = `<span style="color:#fbbf24;">MV5:</span> <span style="font-weight:800; color:#fef08a;">${s5}</span> ／ <span style="color:#a78bfa;">MV20:</span> <span style="font-weight:800; color:#ddd6fe;">${s20}</span>`;
    }

    // Helper
    const setVal = (id, val, color) => {
      const el = document.getElementById(id);
      if (el) {
        if (val != null && !isNaN(val)) {
          el.textContent = Number(val).toFixed(2);
          if (color) el.style.color = color;
        } else {
          el.textContent = '—';
        }
      }
    };

    // MAs
    setVal('hudMa5', stockData.ma5 && stockData.ma5[idx], '#f59e0b');
    setVal('hudMa10', stockData.ma10 && stockData.ma10[idx], '#38bdf8');
    setVal('hudMa20', stockData.ma20 && stockData.ma20[idx], '#ec4899');
    setVal('hudMa60', stockData.ma60 && stockData.ma60[idx], '#8b5cf6');

    // RSI
    const r6 = stockData.rsi6 && stockData.rsi6[idx];
    const r12 = stockData.rsi12 && stockData.rsi12[idx];
    const rsiVal = r6 != null ? r6 : (stockData.rsi && stockData.rsi[idx]);
    setVal('hudRsi6', r6, '#fbbf24');
    setVal('hudRsi12', r12, '#38bdf8');

    // MACD
    setVal('hudMacdDif', stockData.dif && stockData.dif[idx], '#bae6fd');
    setVal('hudMacdSignal', stockData.macdSignal && stockData.macdSignal[idx], '#fed7aa');
    const macd = stockData.macdHist && stockData.macdHist[idx];
    const macdEl = document.getElementById('hudMacdHist');
    if (macdEl) {
      if (macd != null && !isNaN(macd)) {
        macdEl.textContent = `${macd > 0 ? '+' : ''}${macd.toFixed(2)}`;
        macdEl.style.color = macd >= 0 ? '#ef4444' : '#10b981';
      } else {
        macdEl.textContent = '—';
        macdEl.style.color = '#cbd5e1';
      }
    }

    // KD
    setVal('hudK', stockData.kList && stockData.kList[idx], '#f1f5f9');
    setVal('hudD', stockData.dList && stockData.dList[idx], '#f1f5f9');

    // BOLL
    setVal('hudBollUp', stockData.bollUpper && stockData.bollUpper[idx], '#f472b6');
    setVal('hudBollMid', stockData.bollMid && stockData.bollMid[idx], '#e2e8f0');
    setVal('hudBollLow', stockData.bollLower && stockData.bollLower[idx], '#34d399');
  };

  let activeInstance = null;
  window.PatternViewer = {
    async init(root = '.pattern-viewer', options = {}) {
      if (activeInstance) activeInstance.destroy();
      activeInstance = new PatternViewerApp(root, options);
      await activeInstance.init();
      return activeInstance;
    },
    loadStock(code) {
      return activeInstance ? activeInstance.loadStock(code) : Promise.resolve(false);
    },
    refresh(options) {
      return activeInstance ? activeInstance.refresh(options) : Promise.resolve(false);
    },
    resize() {
      if (activeInstance) activeInstance.resize();
    },
    destroy() {
      if (activeInstance) activeInstance.destroy();
      activeInstance = null;
    },
    get instance() {
      return activeInstance;
    }
  };
})();
