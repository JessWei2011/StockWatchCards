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

      const [indexResult, cardsResult] = await Promise.allSettled([
        fetch(this.options.reportsIndexUrl, { cache: 'no-store' }),
        fetch(this.options.cardsUrl, { cache: 'no-store' })
      ]);

      if (indexResult.status === 'fulfilled' && indexResult.value.ok) {
        const payload = await indexResult.value.json();
        this.reportsIndex = Array.isArray(payload) ? payload : (payload.reports || []);
      }

      if (cardsResult.status === 'fulfilled' && cardsResult.value.ok) {
        const payload = await cardsResult.value.json();
        const apiCards = payload.cards || payload;
        cards = Array.isArray(apiCards) ? apiCards : Object.values(apiCards || {});
      }

      this.cards = cards.filter(card => card && card.code);
      this.cardByCode = {};
      this.cards.forEach(card => { this.cardByCode[String(card.code)] = card; });
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

      const availableCodes = new Set(this.reportsIndex.map(item => String(item.code)));
      // 使用卡片與真實報表的聯集：沒有 AI 卡片的報表仍可查看原始指標，
      // 只有 AI 卡片但還沒產生報表的股票也要保留並清楚標示。
      const candidatesByCode = new Map(
        this.cards.map(card => [String(card.code), card])
      );
      this.reportsIndex.forEach(item => {
        const code = String(item.code);
        if (!candidatesByCode.has(code)) {
          candidatesByCode.set(code, {
            ...item,
            group: '已有報表・尚無 AI 卡片',
            decision: '原始指標',
            winRate: 0
          });
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
            const reportMark = availableCodes.has(String(card.code)) ? '' : '｜尚無報表';
            option.textContent = `${card.code} ${card.name} (${card.decision || '尚無分析'}${reportMark})`;
            optgroup.appendChild(option);
          });
        select.appendChild(optgroup);
      });
    }

    async loadStock(code) {
      const normalizedCode = String(code || '');
      const requestId = ++this.requestSerial;
      this.currentCode = normalizedCode;
      this.showLoading(normalizedCode);

      let entry = this.reportsIndex.find(item => String(item.code) === normalizedCode);
      if (!entry) {
        this.currentStockData = null;
        this.updateAiCardBox(normalizedCode);
        this.showEmpty(
          `${normalizedCode} 尚無原始報表`,
          'AI 卡片可能已存在，但型態線圖必須使用真實 K 線報表；請先在報表管理中產生或更新這檔股票。'
        );
        return false;
      }

      const fetchReport = async reportEntry => {
        const reportUrl = this.options.reportsBaseUrl + reportEntry.path
          .split('/')
          .map(segment => encodeURIComponent(segment))
          .join('/');
        return fetch(reportUrl, { cache: 'no-store' });
      };

      let parsed;
      try {
        let response = await fetchReport(entry);
        if (!response.ok) {
          // 路徑可能在元件初始化後被移動。刷新全域索引並依代號重找一次，
          // 只自動重試一輪，避免真正的伺服器錯誤形成無限請求。
          await this.loadSources();
          if (requestId !== this.requestSerial || this.destroyed) return false;
          this.populateStockSelect();
          entry = this.reportsIndex.find(item => String(item.code) === normalizedCode);
          if (!entry) throw new Error('刷新索引後仍找不到這檔股票的報表');
          response = await fetchReport(entry);
        }
        if (!response.ok) throw new Error(`讀取 ${entry.path} 時伺服器回傳 HTTP ${response.status}`);
        const htmlText = await response.text();
        if (requestId !== this.requestSerial || this.destroyed) return false;

        parsed = PatternParser.parseStockHtml(htmlText);
        if (!parsed || !parsed.dates || parsed.dates.length === 0) {
          throw new Error(`報表 ${entry.path} 中找不到可解析的 K 線資料`);
        }
      } catch (error) {
        if (requestId !== this.requestSerial || this.destroyed) return false;
        this.currentStockData = null;
        this.updateAiCardBox(normalizedCode);
        this.showEmpty(`${normalizedCode} 報表載入失敗`, error.message || '無法讀取報表');
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
      if (!this.currentStockData) return null;
      if (this.currentView === 'data') {
        return PatternEngine.buildDataGuideOverlay(this.currentStockData);
      }
      const card = this.cardByCode[this.currentCode];
      return PatternEngine.buildOverlayForCard(card ? card.pattern : '', this.currentStockData);
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
      chart.hidden = false;
      empty.hidden = true;

      ChartEngine.render(chart, this.currentStockData, overlay, {
        showBoll: this.q('#toggleBoll').checked,
        showMa: this.q('#toggleMa').checked
      });

      const sourceNote = this.q('#sourceTextNote');
      const pivotList = this.q('#pivotList');
      pivotList.innerHTML = '';
      if (overlay) {
        const badge = this.q('#teachingBadge');
        badge.textContent = overlay.badge || 'AI 型態分析';
        badge.style.color = overlay.color || '#f59e0b';
        this.q('#teachingExplanation').textContent = overlay.explanation || '目前沒有型態教學說明。';
        sourceNote.textContent = overlay.sourceText
          ? `依卡片型態文字「${overlay.sourceText}」比對繪製：${overlay.name}`
          : '';

        (overlay.pivots || []).forEach(pivot => {
          const item = document.createElement('li');
          item.className = 'pivot-item';
          item.style.borderLeftColor = overlay.color || '#3b82f6';
          const tag = document.createElement('span');
          tag.className = 'pivot-tag';
          tag.style.background = overlay.color || '#3b82f6';
          tag.textContent = pivot.tag;
          const label = document.createElement('span');
          label.textContent = pivot.label;
          const date = document.createElement('span');
          date.className = 'pivot-date';
          date.textContent = `(${pivot.date})`;
          const price = document.createElement('span');
          price.className = 'pivot-price';
          price.textContent = `$${pivot.price}`;
          item.append(tag, label, date, price);
          pivotList.appendChild(item);
        });
      } else {
        sourceNote.textContent = '';
      }

      this.q('#chartTitle').textContent = `${this.currentStockData.title} — K線 / RSI / MACD / KD / 成交量圖`;
      this.q('#cardStockName').textContent = this.currentStockData.title;
      const candles = this.currentStockData.candles || [];
      const lastCandle = candles[candles.length - 1];
      if (lastCandle) this.q('#statCurrentPrice').textContent = `$${lastCandle[1]}`;
      if (candles.length) {
        const highest = Math.max(...candles.map(item => item[3]));
        const lowest = Math.min(...candles.map(item => item[2]));
        this.q('#statHighLow').textContent = `${highest} / ${lowest}`;
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
