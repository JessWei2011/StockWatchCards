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
        getStarredCodes: null,
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
      this.stockCandidates = [];
    }

    q(selector) {
      if (!this.root) return null;
      const aliases = {
        '#stockSelect': '#stockSelect, .stock-selector',
        '#stockSearchInput': '#stockSearchInput, .stock-search-input',
        '#stockSearchSuggestions': '#stockSearchSuggestions, .stock-search-suggestions',
        '#stockSearchBtn': '#stockSearchBtn, .stock-search-btn',
        '#toggleMa': '#toggleMa, .toggle-ma',
        '#toggleBoll': '#toggleBoll, .toggle-boll',
        '#toggleFullscreenChart': '#toggleFullscreenChart, .btn-fullscreen-toggle',
        '#chartTitle': '#chartTitle, .chart-title',
        '#chartDisposalBadge': '#chartDisposalBadge, .chart-disposal-badge',
        '#patternEmptyState': '#patternEmptyState, .empty-state',
        '#patternEmptyTitle': '#patternEmptyTitle, .empty-title',
        '#patternEmptyDetail': '#patternEmptyDetail, .empty-detail',
        '#echart-main': '#echart-main, .chart-canvas',
        '#patternInstContainer': '#patternInstContainer, .inst-mini-chart',
        '#patternInstTableWrapper': '#patternInstTableWrapper, .pattern-inst-table-wrapper',
        '#patternStarBtn': '#patternStarBtn, .pattern-star-btn'
      };
      const resolved = aliases[selector] || selector;
      return this.root.querySelector(resolved);
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
      let cards = [];

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

      const selectedCode = String(this.currentCode || select.value || this.options.initialCode || '').trim();

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
      this.stockCandidates = candidates;
      const starredCodes = this.getStarredCodeSet();
      const groups = {};
      candidates.filter(card => !starredCodes.has(String(card.code))).forEach(card => {
        const group = card.group || '未分類';
        (groups[group] = groups[group] || []).push(card);
      });

      select.innerHTML = '';
      const appendGroup = (label, cards) => {
        if (!cards.length) return;
        const optgroup = document.createElement('optgroup');
        optgroup.label = label;
        cards
          .sort((a, b) => (b.winRate || 0) - (a.winRate || 0))
          .forEach(card => {
            const option = document.createElement('option');
            option.value = card.code;
            const hasRep = availableMap.has(String(card.code));
            const reportMark = hasRep ? '' : '｜尚無報表';
            const rep = availableMap.get(String(card.code));
            const mkt = (rep && rep.market) || (card && card.market) || ((rep && rep.path && rep.path.includes('(TWO)')) ? 'TWO' : 'TW');
            const mktTag = mkt === 'TWO' ? '🟪[上櫃]' : '🟦[上市]';
            option.textContent = `${mktTag} ${card.code} ${card.name || ''} (${card.decision || '技術指標'}${reportMark})`;
            optgroup.appendChild(option);
          });
        select.appendChild(optgroup);
      };

      appendGroup('⭐ 重點', candidates.filter(card => starredCodes.has(String(card.code))));
      Object.keys(groups).sort((a, b) => a.localeCompare(b, 'zh-Hant')).forEach(group => {
        appendGroup(group, groups[group]);
      });

      const suggestions = this.q('#stockSearchSuggestions');
      if (suggestions) {
        suggestions.innerHTML = '';
        candidates
          .slice()
          .sort((a, b) => String(a.code).localeCompare(String(b.code)))
          .forEach(card => {
            const rep = availableMap.get(String(card.code));
            const mkt = (rep && rep.market) || (card && card.market) || ((rep && rep.path && rep.path.includes('(TWO)')) ? 'TWO' : 'TW');
            const mktLabel = mkt === 'TWO' ? '上櫃' : '上市';
            const option = document.createElement('option');
            option.value = `${card.code} ${card.name || ''} [${mktLabel}]`.trim();
            suggestions.appendChild(option);
          });
      }

      if (selectedCode && Array.from(select.options).some(option => option.value === selectedCode)) {
        select.value = selectedCode;
      }
      this.syncStockControls(selectedCode || select.value, { updateSearch: !!this.currentCode });
    }

    getStarredCodeSet() {
      try {
        const source = typeof this.options.getStarredCodes === 'function'
          ? this.options.getStarredCodes()
          : JSON.parse(localStorage.getItem('stockCardsWatchlist') || '[]');
        return new Set(Array.from(source || []).map(code => String(code).trim()).filter(Boolean));
      } catch (_error) {
        return new Set();
      }
    }

    findStockCode(query) {
      const raw = String(query || '').trim();
      if (!raw) return '';
      const codeMatch = raw.match(/\d{4,6}/);
      if (codeMatch && this.stockCandidates.some(card => String(card.code) === codeMatch[0])) {
        return codeMatch[0];
      }
      const normalized = raw.toLocaleLowerCase('zh-Hant').replace(/\s+/g, '');
      const exact = this.stockCandidates.find(card =>
        String(card.name || '').toLocaleLowerCase('zh-Hant').replace(/\s+/g, '') === normalized
      );
      if (exact) return String(exact.code);
      const partial = this.stockCandidates.find(card => {
        const name = String(card.name || '').toLocaleLowerCase('zh-Hant').replace(/\s+/g, '');
        return name.includes(normalized) || normalized.includes(name);
      });
      return partial ? String(partial.code) : '';
    }

    syncStockControls(code, { updateSearch = true } = {}) {
      const normalizedCode = String(code || '').trim();
      if (!normalizedCode) return;
      const select = this.q('#stockSelect');
      if (select && Array.from(select.options).some(option => option.value === normalizedCode)) {
        select.value = normalizedCode;
      }
      if (updateSearch) {
        const search = this.q('#stockSearchInput');
        const stock = this.stockCandidates.find(card => String(card.code) === normalizedCode);
        if (search) search.value = stock ? `${normalizedCode} ${stock.name || ''}`.trim() : normalizedCode;
      }
    }

    async submitStockSearch() {
      const search = this.q('#stockSearchInput');
      if (!search) return false;
      const code = this.findStockCode(search.value);
      if (!code) {
        search.setCustomValidity('找不到這個股號或股名，請從建議清單選擇。');
        search.reportValidity();
        return false;
      }
      search.setCustomValidity('');
      if (code === this.currentCode && this.currentStockData) {
        this.syncStockControls(code);
        return true;
      }
      return this.loadStock(code);
    }

    async loadStock(code) {
      const normalizedCode = String(code || '').trim();
      if (!normalizedCode) return false;
      const requestId = ++this.requestSerial;
      const isStockChange = this.currentCode !== normalizedCode;
      this.currentCode = normalizedCode;
      this.syncStockControls(normalizedCode);
      this.root.dispatchEvent(new CustomEvent('patternviewer:stockchange', {
        detail: { code: normalizedCode }
      }));
      if (isStockChange) {
        const toggleMa = this.q('#toggleMa');
        if (toggleMa) toggleMa.checked = true;
      }
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
      if (!entry) {
        console.log(`[PatternViewer Debug] ⚠️ 在現有索引中找不到代號 "${normalizedCode}"，立即向伺服器重新拉取最新索引...`);
        await this.loadSources();
        this.populateStockSelect();
        entry = this.reportsIndex.find(item => String(item.code).trim() === normalizedCode);
      }
      console.log(`[PatternViewer Debug] 比對代號 "${normalizedCode}" 結果:`, entry);

      if (!entry) {
        console.warn(`[PatternViewer Debug] ❌ 重新拉取後仍找不到代號 "${normalizedCode}"！`);
        console.warn(`[PatternViewer Debug] 所有可用的代號清單:`, this.reportsIndex.map(x => x.code));
        this.currentStockData = null;
        const chartTitle = this.q('#chartTitle');
        if (chartTitle) chartTitle.innerHTML = `${normalizedCode} — 尚無報表資料`;
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
      this.syncStockControls(normalizedCode);
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
      const dateEl = this.q('#statCardDate');
      if (dateEl) dateEl.textContent = card.date || '—';
      const decEl = this.q('#statCardDecision');
      if (decEl) decEl.textContent = `${card.decision || '—'}｜勝率 ${winText}${rrText}｜${card.pattern || '無明確型態'}`;
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
      const titleEl = this.q('#patternAnalysisTitle');
      if (titleEl) titleEl.textContent = `${card.code} ${card.name}・技術分析`;
      const metaEl = this.q('#patternAnalysisMeta');
      if (metaEl) metaEl.textContent = `${card.date || '日期未知'}｜${card.decision || '未定'}｜勝率 ${winText}${rrText}｜${card.pattern || '無明確型態'}`;

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
      console.log(`[PatternViewer Debug] 🎨 updateUI() 開始繪製 ${this.currentCode}, 日期天數:`, this.currentStockData.dates ? this.currentStockData.dates.length : 0);
      const overlay = this.getActiveOverlay();
      const chart = this.q('#echart-main');
      const empty = this.q('#patternEmptyState');
      if (chart) {
        chart.hidden = false;
        chart.style.display = 'block';
      }
      if (empty) empty.hidden = true;

      if (chart) {
        const toggleBoll = this.q('#toggleBoll');
        const toggleMa = this.q('#toggleMa');
        ChartEngine.render(chart, this.currentStockData, overlay, {
          showBoll: toggleBoll ? toggleBoll.checked : true,
          showMa: toggleMa ? toggleMa.checked : true
        });
      }

      const code = String(this.currentCode || '').split('.')[0].trim();
      const rep = (this.reportsIndex || []).find(item => String(item.code) === code);
      const card = this.cardByCode && this.cardByCode[code];
      const mkt = (rep && rep.market) || (card && card.market) || (window.STOCK_MARKET_MAP && window.STOCK_MARKET_MAP[code]) || ((rep && rep.path && rep.path.includes('(TWO)')) ? 'TWO' : 'TW');
      const mktClass = mkt === 'TWO' ? 'market-two' : 'market-tw';
      const mktLabel = mkt === 'TWO' ? '上櫃' : '上市';

      const chartTitle = this.q('#chartTitle');
      if (chartTitle) chartTitle.innerHTML = `<span class="market-badge ${mktClass}">${mktLabel}</span>${this.currentStockData.title} — K線 / RSI / MACD / KD / 成交量圖`;
      this.updateDisposalBadge();

      const cardStockName = this.q('#cardStockName');
      if (cardStockName) cardStockName.textContent = this.currentStockData.title;

      const candles = this.currentStockData.candles || [];
      const lastCandle = candles[candles.length - 1];
      const prevCandle = candles.length > 1 ? candles[candles.length - 2] : null;

      const statCurrentPrice = this.q('#statCurrentPrice');
      if (statCurrentPrice && lastCandle) {
        statCurrentPrice.textContent = `$${lastCandle[1]}`;
        if (prevCandle) {
          const diff = lastCandle[1] - prevCandle[1];
          statCurrentPrice.className = `stat-value ${diff > 0 ? 'up' : (diff < 0 ? 'down' : '')}`;
        }
      }

      const statChangePct = this.q('#statChangePct') || this.q('#statHighLow');
      if (statChangePct && lastCandle) {
        if (prevCandle && prevCandle[1]) {
          const diff = lastCandle[1] - prevCandle[1];
          const pct = (diff / prevCandle[1]) * 100;
          const sign = diff > 0 ? '+' : '';
          statChangePct.className = `stat-value ${diff > 0 ? 'up' : (diff < 0 ? 'down' : '')}`;
          statChangePct.textContent = `${sign}${pct.toFixed(2)}%`;
        } else {
          statChangePct.className = 'stat-value';
          statChangePct.textContent = '0.00%';
        }
      }

      // 渲染「📈 K線指標狀態」
      const statKlineTags = this.q('#statKlineTags') || document.getElementById('statKlineTags');
      if (statKlineTags) {
        const cleanCode = String(this.currentCode || '').split('.')[0].trim();
        const allCards = (window.cardsByCode || (window.parent && window.parent.cardsByCode) || (typeof cardsByCode !== 'undefined' ? cardsByCode : {})) || {};
        const card = allCards[cleanCode] || allCards[this.currentCode] || null;
        let klineTagsStr = card ? (card.klineTags || card.kline_tags || card.technicalTags || '') : '';
        if (!klineTagsStr && card && card.raw) {
          const m = String(card.raw).match(/K線\s*(?:指標)?標籤[】\]\s\*]*[：:]\s*([^\r\n]+)/i) || String(card.raw).match(/技術標籤[】\]\s\*]*[：:]\s*([^\r\n]+)/i);
          if (m) klineTagsStr = m[1].trim();
        }

        if (klineTagsStr) {
          const parts = klineTagsStr.split(/[、,]/).map(s => s.trim()).filter(Boolean);
          statKlineTags.innerHTML = parts.map(t => {
            let style = 'background:rgba(148,163,184,0.15); color:#cbd5e1; border:1px solid rgba(148,163,184,0.3);';
            if (/多頭|上彎|仰角|噴出|金叉|站上|重回/.test(t)) {
              style = 'background:rgba(34,197,94,0.18); color:#86efac; border:1px solid rgba(34,197,94,0.38);';
            } else if (/空頭|下彎|俯角|探底|死叉|跌破|失守/.test(t)) {
              style = 'background:rgba(239,68,68,0.18); color:#fca5a5; border:1px solid rgba(239,68,68,0.38);';
            } else if (/糾纏|整理|糾結|蓄勢/.test(t)) {
              style = 'background:rgba(234,179,8,0.18); color:#fde047; border:1px solid rgba(234,179,8,0.38);';
            }
            return `<span style="display:inline-block; padding:3px 9px; border-radius:6px; font-size:15px; padding:3px 9px; font-weight:750; ${style}">${t}</span>`;
          }).join('');
        } else {
          statKlineTags.innerHTML = `<span style="display:inline-block; padding:3px 9px; border-radius:6px; font-size:15px; padding:3px 9px; font-weight:750; background:rgba(148,163,184,0.15); color:#94a3b8; border:1px solid rgba(148,163,184,0.3);">均線排列正常</span>`;
        }
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
            return `<span style="display:inline-block; padding:3px 9px; border-radius:6px; font-size:15px; padding:3px 9px; font-weight:750; ${style}">${t}</span>`;
          }).join('');
        } else {
          statRsiTags.innerHTML = `<span style="display:inline-block; padding:3px 9px; border-radius:6px; font-size:15px; padding:3px 9px; font-weight:750; background:rgba(148,163,184,0.15); color:#94a3b8; border:1px solid rgba(148,163,184,0.3);">RSI 數據正常</span>`;
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
            if (/天量|倒貨|頂背離|死亡交叉|死叉|退潮|阻力牆|防壓回/.test(t)) {
              style = 'background:rgba(239,68,68,0.18); color:#fca5a5; border:1px solid rgba(239,68,68,0.38);';
            } else if (/突破|黃金交叉|金叉|窒息量|洗淨/.test(t)) {
              style = 'background:rgba(234,179,8,0.18); color:#fde047; border:1px solid rgba(234,179,8,0.38);';
            } else if (/滾量|量價齊揚|主升|吞噬/.test(t)) {
              style = 'background:rgba(34,197,94,0.18); color:#86efac; border:1px solid rgba(34,197,94,0.38);';
            } else if (/溫和|增量/.test(t)) {
              style = 'background:rgba(56,189,248,0.15); color:#7dd3fc; border:1px solid rgba(56,189,248,0.3);';
            }
            return `<span style="display:inline-block; padding:3px 9px; border-radius:6px; font-size:15px; padding:3px 9px; font-weight:750; ${style}">${t}</span>`;
          }).join('');
        } else {
          statVolTags.innerHTML = `<span style="display:inline-block; padding:3px 9px; border-radius:6px; font-size:15px; padding:3px 9px; font-weight:750; background:rgba(148,163,184,0.15); color:#94a3b8; border:1px solid rgba(148,163,184,0.3);">常態量能換手</span>`;
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
            return `<span style="display:inline-block; padding:3px 9px; border-radius:6px; font-size:15px; padding:3px 9px; font-weight:750; ${style}">${t}</span>`;
          }).join('');
        } else {
          statMacdTags.innerHTML = `<span style="display:inline-block; padding:3px 9px; border-radius:6px; font-size:15px; padding:3px 9px; font-weight:750; background:rgba(148,163,184,0.15); color:#94a3b8; border:1px solid rgba(148,163,184,0.3);">MACD 數據正常</span>`;
        }
      }

      // 渲染「⚡ KD 指標狀態」
      const statKdTags = this.q('#statKdTags') || document.getElementById('statKdTags');
      if (statKdTags) {
        const cleanCode = String(this.currentCode || '').split('.')[0].trim();
        const allCards = (window.cardsByCode || (window.parent && window.parent.cardsByCode) || (typeof cardsByCode !== 'undefined' ? cardsByCode : {})) || {};
        const card = allCards[cleanCode] || allCards[this.currentCode] || null;
        let kdTagsStr = card ? (card.kdTags || card.kd_tags || '') : '';
        if (!kdTagsStr && card && card.raw) {
          const m = String(card.raw).match(/KD\s*(?:指標)?標籤[】\]\s\*]*[：:]\s*([^\r\n]+)/i);
          if (m) kdTagsStr = m[1].trim();
        }

        if (kdTagsStr) {
          const parts = kdTagsStr.split(/[、,]/).map(s => s.trim()).filter(Boolean);
          statKdTags.innerHTML = parts.map(t => {
            let style = 'background:rgba(148,163,184,0.15); color:#cbd5e1; border:1px solid rgba(148,163,184,0.3);';
            if (/死亡交叉|死叉|頂背離|超買|轉弱/.test(t)) {
              style = 'background:rgba(239,68,68,0.18); color:#fca5a5; border:1px solid rgba(239,68,68,0.38);';
            } else if (/金叉|黃金交叉|超賣|底背離|買點|轉強/.test(t)) {
              style = 'background:rgba(234,179,8,0.18); color:#fde047; border:1px solid rgba(234,179,8,0.38);';
            } else if (/鈍化|軋空/.test(t)) {
              style = 'background:rgba(34,197,94,0.18); color:#86efac; border:1px solid rgba(34,197,94,0.38);';
            } else if (/多方|推進/.test(t)) {
              style = 'background:rgba(56,189,248,0.15); color:#7dd3fc; border:1px solid rgba(56,189,248,0.3);';
            }
            return `<span style="display:inline-block; padding:3px 9px; border-radius:6px; font-size:15px; padding:3px 9px; font-weight:750; ${style}">${t}</span>`;
          }).join('');
        } else {
          statKdTags.innerHTML = `<span style="display:inline-block; padding:3px 9px; border-radius:6px; font-size:15px; padding:3px 9px; font-weight:750; background:rgba(148,163,184,0.15); color:#94a3b8; border:1px solid rgba(148,163,184,0.3);">KD 數據正常</span>`;
        }
      }

      // 渲染「🏛️ 籌碼指標狀態」
      const statChipTags = this.q('#statChipTags') || document.getElementById('statChipTags');
      if (statChipTags) {
        const cleanCode = String(this.currentCode || '').split('.')[0].trim();
        const allCards = (window.cardsByCode || (window.parent && window.parent.cardsByCode) || (typeof cardsByCode !== 'undefined' ? cardsByCode : {})) || {};
        const card = allCards[cleanCode] || allCards[this.currentCode] || null;
        let chipTagsStr = card ? (card.chipTags || card.chip_tags || '') : '';
        if (!chipTagsStr && card && card.raw) {
          const m = String(card.raw).match(/籌碼標籤[】\]\s\*]*[：:]\s*([^\r\n]+)/i);
          if (m) chipTagsStr = m[1].trim();
        }

        if (chipTagsStr) {
          const parts = chipTagsStr.split(/[、,]/).map(s => s.trim()).filter(Boolean);
          statChipTags.innerHTML = parts.map(t => {
            let style = 'background:rgba(148,163,184,0.15); color:#cbd5e1; border:1px solid rgba(148,163,184,0.3);';
            if (/倒貨|結帳|資增法賣|退潮|賣超|沉陷|警示/.test(t)) {
              style = 'background:rgba(239,68,68,0.18); color:#fca5a5; border:1px solid rgba(239,68,68,0.38);';
            } else if (/由賣轉買|護盤|對作|接刀|吃貨|避險|點火|起漲/.test(t)) {
              style = 'background:rgba(234,179,8,0.18); color:#fde047; border:1px solid rgba(234,179,8,0.38);';
            } else if (/大買|總攻擊|認養|鎖碼|資減法買|主升|狂拉/.test(t)) {
              style = 'background:rgba(34,197,94,0.18); color:#86efac; border:1px solid rgba(34,197,94,0.38);';
            } else if (/防守|建倉|重倉|集資|買超|積極/.test(t)) {
              style = 'background:rgba(56,189,248,0.18); color:#7dd3fc; border:1px solid rgba(56,189,248,0.38);';
            }
            return `<span style="display:inline-block; padding:3px 9px; border-radius:6px; font-size:15px; padding:3px 9px; font-weight:750; ${style}">${t}</span>`;
          }).join('');
        } else {
          statChipTags.innerHTML = `<span style="display:inline-block; padding:3px 9px; border-radius:6px; font-size:15px; padding:3px 9px; font-weight:750; background:rgba(148,163,184,0.15); color:#94a3b8; border:1px solid rgba(148,163,184,0.3);">法人籌碼中性</span>`;
        }
      }
      // 渲染「三大法人近15日逐日買賣超概數」表格 (放置於 KD 指標下方)
      const patternInstTableWrapper = this.q('#patternInstTableWrapper') || document.getElementById('patternInstTableWrapper');
      const patternInstContainer = this.q('#patternInstContainer') || document.getElementById('patternInstContainer');
      if (patternInstTableWrapper) {
        const cleanCode = String(this.currentCode || '').split('.')[0].trim();
        const allCards = (window.cardsByCode || (window.parent && window.parent.cardsByCode) || (typeof cardsByCode !== 'undefined' ? cardsByCode : {})) || {};
        let card = allCards[cleanCode] || allCards[this.currentCode] || null;
        if (!card && this.currentStockData && this.currentStockData.institutionalFlow && this.currentStockData.institutionalFlow.length) {
          card = { institutionalFlow: this.currentStockData.institutionalFlow };
        } else if (card && (!card.institutionalFlow || !card.institutionalFlow.length) && this.currentStockData && this.currentStockData.institutionalFlow) {
          card.institutionalFlow = this.currentStockData.institutionalFlow;
        }
        if (card && typeof window.renderInstitutionSummary === 'function') {
          patternInstTableWrapper.innerHTML = window.renderInstitutionSummary(card);
          if (patternInstContainer) patternInstContainer.style.display = 'block';
        } else if (typeof updatePatternInstitutionalSummary === 'function') {
          updatePatternInstitutionalSummary(cleanCode);
        } else {
          if (patternInstContainer) patternInstContainer.style.display = 'none';
        }
      }
      requestAnimationFrame(() => this.resize());
    }

    showLoading(code) {
      this.showEmpty(`正在載入 ${code || '個股'}…`, '讀取真實報表與技術指標資料。', false);
    }

    showEmpty(title, detail, destroyChart = true) {
      const chart = this.q('#echart-main');
      const empty = this.q('#patternEmptyState');
      if (destroyChart) {
        if (chart) ChartEngine.destroy(chart);
        else ChartEngine.destroy();
      }
      if (chart) chart.hidden = true;
      if (empty) empty.hidden = false;
      const titleEl = this.q('#patternEmptyTitle');
      if (titleEl) titleEl.textContent = title;
      const detailEl = this.q('#patternEmptyDetail');
      if (detailEl) detailEl.textContent = detail;
    }

    bindEvents() {
      const signal = this.eventController.signal;
      const stockSelect = this.q('#stockSelect');
      if (stockSelect) stockSelect.addEventListener('change', event => this.loadStock(event.target.value), { signal });

      const stockSearch = this.q('#stockSearchInput');
      if (stockSearch) {
        stockSearch.addEventListener('focus', () => {
          stockSearch.value = '';
          stockSearch.setCustomValidity('');
        }, { signal });
        stockSearch.addEventListener('click', () => {
          stockSearch.value = '';
          stockSearch.setCustomValidity('');
        }, { signal });
        stockSearch.addEventListener('blur', () => {
          if (!stockSearch.value.trim() && this.activeStock) {
            stockSearch.value = `${this.activeStock.code} ${this.activeStock.name || ''}`.trim();
          }
        }, { signal });
        stockSearch.addEventListener('input', () => {
          stockSearch.setCustomValidity('');
          const value = stockSearch.value.trim();
          const pickedSuggestion = this.stockCandidates.some(card =>
            `${card.code} ${card.name || ''}`.trim() === value
          );
          if (pickedSuggestion) this.submitStockSearch();
        }, { signal });
        stockSearch.addEventListener('keydown', event => {
          if (event.key !== 'Enter') return;
          event.preventDefault();
          this.submitStockSearch();
        }, { signal });
      }
      const stockSearchButton = this.q('#stockSearchBtn');
      if (stockSearchButton) {
        stockSearchButton.addEventListener('click', () => this.submitStockSearch(), { signal });
      }

      const viewToggle = this.q('#viewToggleContainer');
      if (viewToggle) {
        viewToggle.addEventListener('click', event => {
          const button = event.target.closest('.btn-pattern');
          if (!button) return;
          this.root.querySelectorAll('#viewToggleContainer .btn-pattern').forEach(item => item.classList.remove('active'));
          button.classList.add('active');
          this.currentView = button.dataset.view;
          this.updateUI();
        }, { signal });
      }

      const toggleBoll = this.q('#toggleBoll');
      if (toggleBoll) toggleBoll.addEventListener('change', () => this.updateUI(), { signal });

      const toggleMa = this.q('#toggleMa');
      if (toggleMa) toggleMa.addEventListener('change', () => this.updateUI(), { signal });

      const fullscreenBtn = this.q('#toggleFullscreenChart');
      if (fullscreenBtn) {
        fullscreenBtn.addEventListener('click', () => {
          if (typeof window.toggleChartFullscreen === 'function') {
            window.toggleChartFullscreen();
          }
        }, { signal });
      }
    }

    updateDisposalBadge() {
      const badge = this.q('#chartDisposalBadge');
      if (!badge) return;

      const code = String(this.currentCode || '').split('.')[0].trim();
      const noticeMap = window.DISPOSAL_NOTICE_MAP || (window.parent && window.parent.DISPOSAL_NOTICE_MAP) || {};
      const info = noticeMap[code];

      badge.hidden = true;
      badge.className = 'chart-disposal-badge';
      badge.textContent = '';
      badge.title = '';
      if (!info) return;

      const shortPeriod = (typeof window.formatDisposalPeriodShort === 'function')
        ? window.formatDisposalPeriodShort(info)
        : ((window.parent && typeof window.parent.formatDisposalPeriodShort === 'function')
          ? window.parent.formatDisposalPeriodShort(info)
          : '');

      if (info.type === 'disposal' && info.status === 'upcoming') {
        badge.classList.add('upcoming');
        badge.textContent = shortPeriod ? `🚨 明日進處置 ${shortPeriod}` : '🚨 明日進處置';
        badge.title = info.period_raw ? `已公告明日進處置（${info.period_raw}）` : '已公告明日進處置';
      } else if (info.type === 'disposal' && info.status === 'active') {
        badge.classList.add('active');
        badge.textContent = shortPeriod ? `🔒 處置中 ${shortPeriod}` : '🔒 處置中';
        badge.title = info.period_raw ? `目前處置中（${info.period_raw}）` : '目前處置中';
      } else {
        badge.classList.add('notice');
        badge.textContent = '👀 注意股票';
        badge.title = info.info || '注意股票公告';
      }
      badge.hidden = false;
    }

    observeSize() {
      if (typeof ResizeObserver === 'undefined') return;
      this.resizeObserver = new ResizeObserver(() => this.resize());
      this.resizeObserver.observe(this.root);
    }

    resize() {
      const chart = this.q('#echart-main');
      if (chart) ChartEngine.resize(chart);
      else ChartEngine.resize();
    }

    destroy() {
      this.destroyed = true;
      this.requestSerial += 1;
      this.eventController.abort();
      if (this.resizeObserver) this.resizeObserver.disconnect();
      const chart = this.q('#echart-main');
      if (chart) ChartEngine.destroy(chart);
      else ChartEngine.destroy();
    }
  }

  let activeInstance = null;
  window.PatternViewerApp = PatternViewerApp;
  window.PatternViewer = {
    App: PatternViewerApp,
    createApp(root, options = {}) {
      return new PatternViewerApp(root, options);
    },
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
    refreshStockSelect() {
      if (!activeInstance) return false;
      activeInstance.populateStockSelect();
      return true;
    },
    refreshDisposalBadge() {
      if (!activeInstance) return false;
      activeInstance.updateDisposalBadge();
      return true;
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
