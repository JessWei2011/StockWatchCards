(() => {
  'use strict';

  const state = {
    card: null,
    manifest: null,
    rankings: null,
    sectors: null,
    stockIndex: [],
    currentCode: '',
    chartData: null,
    chart: null,
    activeTab: 'ranking', // 'ranking' | 'stock' | 'sectors' | 'watchlist'
    rankingSubTab: 'evolution', // 'evolution' | 'auxiliary' | 'log'
    period: 20,
    showMa: true,
    showBoll: true
  };

  const $ = selector => document.querySelector(selector);
  const formatNumber = value => {
    if (value == null || String(value).trim() === '') return '—';
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    return number.toLocaleString('zh-TW', { maximumFractionDigits: 2 });
  };
  const last = values => values && values.length ? values[values.length - 1] : null;
  const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[char]);

  // ==================== 離線快取儲存 ====================
  const offlineStorage = {
    db: null,
    async init() {
      if (!window.indexedDB) return false;
      return new Promise((resolve) => {
        try {
          const req = indexedDB.open('Stock2MobileOfflineDB', 2);
          req.onupgradeneeded = (e) => {
            const db = e.target.result;
            if (!db.objectStoreNames.contains('files')) {
              db.createObjectStore('files');
            }
          };
          req.onsuccess = (e) => {
            this.db = e.target.result;
            resolve(true);
          };
          req.onerror = () => resolve(false);
        } catch(_e) { resolve(false); }
      });
    },
    async get(key) {
      if (!this.db) {
        try { return localStorage.getItem('stock2_' + key); } catch(_e) { return null; }
      }
      return new Promise((resolve) => {
        try {
          const tx = this.db.transaction('files', 'readonly');
          const store = tx.objectStore('files');
          const req = store.get(key);
          req.onsuccess = () => resolve(req.result || null);
          req.onerror = () => resolve(null);
        } catch(_e) { resolve(null); }
      });
    },
    async set(key, value) {
      if (!this.db) {
        try { localStorage.setItem('stock2_' + key, value); } catch(_e) {}
        return;
      }
      return new Promise((resolve) => {
        try {
          const tx = this.db.transaction('files', 'readwrite');
          const store = tx.objectStore('files');
          const req = store.put(value, key);
          req.onsuccess = () => resolve(true);
          req.onerror = () => resolve(false);
        } catch(_e) { resolve(false); }
      });
    }
  };

  // ==================== 方案 A：手機本地自選管理 (純 LocalStorage) ====================
  const WATCHLIST_STORAGE_KEY = 'stock2_mobile_local_watchlist';

  function getLocalWatchlist() {
    try {
      const raw = localStorage.getItem(WATCHLIST_STORAGE_KEY);
      const parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed : [];
    } catch (_e) {
      return [];
    }
  }

  function isLocallyStarred(code) {
    if (!code) return false;
    return getLocalWatchlist().includes(String(code));
  }

  function toggleLocalStar(code) {
    if (!code) return;
    const strCode = String(code);
    const list = getLocalWatchlist();
    const idx = list.indexOf(strCode);
    if (idx >= 0) {
      list.splice(idx, 1);
    } else {
      list.unshift(strCode);
    }
    try {
      localStorage.setItem(WATCHLIST_STORAGE_KEY, JSON.stringify(list));
    } catch (_e) {}

    updateWatchlistBadge();
    syncAllStarButtons(strCode);
    if (state.activeTab === 'watchlist') {
      renderWatchlistView();
    }
  }

  function updateWatchlistBadge() {
    const list = getLocalWatchlist();
    const badge = $('#watchlistBadge');
    if (badge) {
      if (list.length > 0) {
        badge.hidden = false;
        badge.textContent = list.length;
      } else {
        badge.hidden = true;
      }
    }
  }

  function syncAllStarButtons(code) {
    const isStarred = isLocallyStarred(code);
    document.querySelectorAll(`button.evo-star-btn[data-code="${code}"]`).forEach(btn => {
      btn.classList.toggle('active', isStarred);
    });
    if (state.currentCode === String(code)) {
      const heroBtn = $('#heroStarButton');
      if (heroBtn) heroBtn.classList.toggle('active', isStarred);
    }
  }

  async function fetchText(path) {
    try {
      const response = await fetch(path, { cache: 'no-cache' });
      if (response.ok) {
        const text = await response.text();
        offlineStorage.set(path, text);
        return text;
      }
    } catch (_netErr) {
      console.warn(`[Offline] 嘗試從本機快取載入: ${path}`);
    }
    const cached = await offlineStorage.get(path);
    if (cached !== null) return cached;
    throw new Error(`${path} 載入失敗`);
  }

  async function fetchJson(path) {
    return JSON.parse(await fetchText(path));
  }

  function showError(error) {
    const banner = $('#errorBanner');
    if (banner) {
      banner.hidden = false;
      banner.textContent = `資料載入失敗：${error.message || error}`;
    }
    console.error(error);
  }

  function clearError() {
    const banner = $('#errorBanner');
    if (banner) {
      banner.hidden = true;
      banner.textContent = '';
    }
  }

  function setText(selector, value) {
    const element = $(selector);
    if (element) element.textContent = value == null || value === '' ? '—' : value;
  }

  // ==================== 分頁切換 (Bottom Navigation Tabs) ====================
  function switchTab(tabName) {
    state.activeTab = tabName;

    // 1. 切換 View 顯示
    $('#viewRanking').hidden = (tabName !== 'ranking');
    $('#viewStock').hidden = (tabName !== 'stock');
    $('#viewSectors').hidden = (tabName !== 'sectors');
    $('#viewWatchlist').hidden = (tabName !== 'watchlist');

    // 2. 更新底部按鈕 active 樣式
    document.querySelectorAll('#bottomNav .nav-item').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.tab === tabName);
    });

    // 3. 更新頂部 Topbar 標題
    if (tabName === 'ranking') {
      setText('#topbarTitle', 'AI 實戰勝率榜');
    } else if (tabName === 'stock') {
      setText('#topbarTitle', state.card ? `${state.card.code} ${state.card.name}` : '個股看盤');
      if (state.chart) {
        setTimeout(() => state.chart.resize(), 50);
      }
    } else if (tabName === 'sectors') {
      setText('#topbarTitle', '今日產業風口');
      renderSectorsView();
    } else if (tabName === 'watchlist') {
      setText('#topbarTitle', '自選追蹤清單');
      renderWatchlistView();
    }

    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  // ==================== 榜單頁次級切換 (Sub-tabs) ====================
  function switchRankingSubTab(subTab) {
    state.rankingSubTab = subTab;
    document.querySelectorAll('#rankingSubTabBar .sub-tab-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.subtab === subTab);
    });
    $('#subViewEvolution').hidden = (subTab !== 'evolution');
    $('#subViewAuxiliary').hidden = (subTab !== 'auxiliary');
    $('#subViewLog').hidden = (subTab !== 'log');

    if (subTab === 'log') {
      loadEvolutionLog();
    }
  }

  // ==================== 個股看盤渲染 (Stock Detail) ====================
  function renderCard() {
    const card = state.card;
    const summary = card.latestAnalysis || card;
    const manifest = state.manifest;
    setText('#topbarTitle', `${card.code} ${card.name}`);
    setText('#analysisDate', `分析 ${summary.date || manifest?.analysisDate || '—'}`);
    setText('#winRate', String(summary.winRate || '—').includes('%') ? summary.winRate : `${summary.winRate}%`);
    setText('#decisionTitle', summary.decision);
    setText('#currentPrice', formatNumber(summary.current));
    setText('#entryPrice', formatNumber(summary.entry));
    setText('#stopPrice', formatNumber(summary.stop));
    setText('#resistPrice', formatNumber(summary.resist));
    setText('#actionText', summary.action);
    setText('#publishedAt', `發布時間：${formatPublishedAt(manifest?.publishedAt || '')}`);
    renderList('#bullishList', summary.bullish, '目前無額外支持證據');
    renderList('#bearishList', summary.bearish, '目前無額外反對證據');
    setText('#pickerCurrentStock', `${card.code} ${card.name}`);
    document.title = `${card.code} ${card.name}｜手機個股分析中心`;
    $('#stockChart').setAttribute('aria-label', `${card.name} K 線、成交量與技術指標互動圖`);

    const heroStar = $('#heroStarButton');
    if (heroStar) {
      heroStar.classList.toggle('active', isLocallyStarred(card.code));
    }
  }

  // ==================== 榜單渲染 (Evolution Rich Cards & Auxiliary) ====================
  function renderRankings() {
    const rankings = state.rankings;
    if (!rankings?.boards?.length) return;

    setText('#analysisDate', `截止 ${rankings.scanDate || '—'}`);

    // 1. 主推：👑 AI 獨有實戰勝率榜
    const evoBoard = rankings.boards.find(b => b.id === 'evolution-master') || rankings.boards[0];
    if (evoBoard && Array.isArray(evoBoard.items)) {
      if (rankings.marketOverview) {
        $('#marketOverviewBanner').hidden = false;
        $('#marketOverviewText').textContent = rankings.marketOverview;
      } else {
        $('#marketOverviewBanner').hidden = true;
      }

      $('#evolutionList').innerHTML = evoBoard.items.map((item, idx) => {
        const rankNum = parseInt(item.rank) || (idx + 1);
        let badgeClass = 'normal';
        if (rankNum === 1) badgeClass = 'top1';
        else if (rankNum === 2) badgeClass = 'top2';
        else if (rankNum === 3) badgeClass = 'top3';

        const pctVal = parseFloat(item.changePct) || 0;
        const pctColor = pctVal > 0 ? '#ef4444' : (pctVal < 0 ? '#22c55e' : '#e2e8f0');

        // 月營收換行
        let revHtml = escapeHtml(item.monthlyRev || '—');
        if (item.monthlyRev && item.monthlyRev.includes('(')) {
          const m = item.monthlyRev.match(/^(.*?)\((.*)\)$/);
          if (m) {
            revHtml = `<div style="font-weight:750;">${escapeHtml(m[1])}</div><div style="font-size:12px; color:#fde047; opacity:0.9; margin-top:2px;">(${escapeHtml(m[2])})</div>`;
          }
        }

        // EPS 獲利換行
        let epsHtml = escapeHtml(item.earnings || '—');
        if (item.earnings) {
          const parts = item.earnings.split(/，(?=累計|上半年)/);
          if (parts.length > 1) {
            epsHtml = `<div style="font-weight:750;">${escapeHtml(parts[0])}</div><div style="font-size:12px; color:#4ade80; opacity:0.9; margin-top:2px;">累計${escapeHtml(parts[1].replace(/^累計/, ''))}</div>`;
          }
        }

        // 目標價分行
        let tpHtml = escapeHtml(item.targetPrice || '—');
        if (item.targetPrice && (item.targetPrice.includes('，') || item.targetPrice.includes('；') || item.targetPrice.includes('。'))) {
          const tpParts = item.targetPrice.split(/(?:[，；]|(?<=[)）元])。)/).map(s => s.trim().replace(/^。/, '')).filter(Boolean);
          if (tpParts.length > 1) {
            tpHtml = tpParts.map(p => `<div>${escapeHtml(p)}</div>`).join('');
          }
        }

        // 技術起漲特徵 Chips
        let chipsHtml = '';
        if (item.feature) {
          const chips = item.feature.split(/[；;]/).map(s => s.trim()).filter(Boolean);
          chipsHtml = `<div class="evo-chips">${chips.map(c => `<span class="evo-chip">${escapeHtml(c)}</span>`).join('')}</div>`;
        }

        return `
          <article class="evolution-card" data-code="${escapeHtml(item.code)}">
            <div class="evolution-card-header">
              <div class="evolution-card-title">
                <span class="evo-rank-badge ${badgeClass}">${escapeHtml(item.rank)}</span>
                <span class="evo-stock-code">${escapeHtml(item.code)}</span>
                <span class="evo-stock-name">${escapeHtml(item.name)}</span>
                <span class="evo-category">${escapeHtml(item.category || '精選')}</span>
              </div>
              <button type="button" class="evo-star-btn ${isLocallyStarred(item.code) ? 'active' : ''}" data-code="${escapeHtml(item.code)}" title="加入/移除手機自選" aria-label="收藏">★</button>
            </div>
            <div class="evolution-card-numbers">
              <div>
                <span style="font-size:11px; color:#94a3b8; display:block;">收盤價</span>
                <span class="evo-price">${escapeHtml(formatNumber(item.price))}</span>
              </div>
              <div>
                <span style="font-size:11px; color:#94a3b8; display:block;">今日漲跌</span>
                <span class="evo-change" style="color:${pctColor};">${escapeHtml(item.changePct || '—')}</span>
              </div>
              <div>
                <span style="font-size:11px; color:#94a3b8; display:block;">實戰評分</span>
                <span class="evo-score-pill">${escapeHtml(item.score || '—')}</span>
              </div>
            </div>
            <div class="evolution-card-grid">
              <div class="evo-metric-row"><span class="evo-metric-label">📊 月盈年盈</span><div class="evo-metric-val rev">${revHtml}</div></div>
              <div class="evo-metric-row"><span class="evo-metric-label">💰 季報獲利</span><div class="evo-metric-val earn">${epsHtml}</div></div>
              <div class="evo-metric-row"><span class="evo-metric-label">📢 法說重點</span><div class="evo-metric-val cat">${escapeHtml(item.catalyst || '—')}</div></div>
              <div class="evo-metric-row"><span class="evo-metric-label">🎯 法人目標</span><div class="evo-metric-val target">${tpHtml}</div></div>
              <div class="evo-metric-row"><span class="evo-metric-label">🚀 起漲特徵</span><div class="evo-metric-val">${chipsHtml}</div></div>
            </div>
          </article>
        `;
      }).join('');
    }

    // 2. 輔助：ChatGPT & Gemini 榜單
    const auxBoards = rankings.boards.filter(b => b.id !== 'evolution-master');
    $('#rankingList').innerHTML = auxBoards.map(board => `
      <section class="ranking-board ${escapeHtml(board.tone || '')}" aria-labelledby="board-${escapeHtml(board.id)}">
        <h3 id="board-${escapeHtml(board.id)}">${escapeHtml(board.title)}</h3>
        <div class="ranking-board-items">
          ${(board.items || []).slice(0, 10).map(item => `
            <button type="button" class="ranking-item" data-code="${escapeHtml(item.code)}" aria-label="查看 ${escapeHtml(item.code)} ${escapeHtml(item.name)} 的個股快照">
              <span class="ranking-position">${escapeHtml(item.rank)}</span>
              <span class="ranking-name">${escapeHtml(item.code)} ${escapeHtml(item.name)}</span>
              <span class="ranking-price">${escapeHtml(formatNumber(item.price))}</span>
            </button>
          `).join('')}
        </div>
      </section>
    `).join('');
  }

  // ==================== 產業風口視圖 (Sectors View) ====================
  async function renderSectorsView() {
    const container = $('#sectorsList');
    if (!container) return;

    try {
      if (!state.sectors) {
        state.sectors = await fetchJson('./data/sectors.json');
      }
      const sectorsData = state.sectors;
      const hotSectors = sectorsData.hot_sectors || [];

      let html = '';
      if (sectorsData.overview) {
        html += `
          <div class="market-overview-banner" style="margin-bottom:14px;">
            <div class="banner-title"><span>🌐</span><span>市場全景資金焦點</span></div>
            <p class="banner-desc">${escapeHtml(sectorsData.overview)}</p>
          </div>
        `;
      }

      html += hotSectors.map((sec, idx) => {
        const stars = '★'.repeat(sec.heat_level || 5) + '☆'.repeat(Math.max(0, 5 - (sec.heat_level || 5)));
        const tags = Array.isArray(sec.related_tags) ? sec.related_tags : [];
        return `
          <article class="sector-card">
            <div class="sector-card-header">
              <span class="sector-name">#${idx + 1} ${escapeHtml(sec.sector_name)}</span>
              <span class="sector-stars" title="熱度 ${sec.heat_level} 星">${stars}</span>
            </div>
            <div class="sector-catalysts">${escapeHtml(sec.catalysts || '—')}</div>
            <div class="sector-tags">
              ${tags.map(t => `<span class="sector-tag">${escapeHtml(t)}</span>`).join('')}
            </div>
          </article>
        `;
      }).join('');

      container.innerHTML = html || '<div style="text-align:center; padding:30px; color:#64748b;">暫無產業風口資料</div>';
    } catch (e) {
      container.innerHTML = `<div style="text-align:center; padding:30px; color:#ef4444;">產業風口載入失敗：${escapeHtml(e.message)}</div>`;
    }
  }

  // ==================== 覆盤日記載入 (Evolution Log) ====================
  async function loadEvolutionLog() {
    const container = $('#evolutionLogContent');
    if (!container) return;
    try {
      const data = await fetchJson('./data/evolution_log.json');
      container.innerHTML = renderMarkdown(data.content || '暫無覆盤日記');
    } catch (e) {
      container.innerHTML = `<p style="color:#ef4444;">覆盤日記讀取失敗：${escapeHtml(e.message)}</p>`;
    }
  }

  // ==================== 自選追蹤視圖 (Local Watchlist View) ====================
  function renderWatchlistView() {
    const container = $('#watchlistCards');
    if (!container) return;

    const starredCodes = getLocalWatchlist();
    if (!starredCodes.length) {
      container.innerHTML = `
        <div class="watchlist-empty">
          <div class="empty-icon">⭐</div>
          <p><strong>尚未加入任何自選股</strong><br>在「👑 AI 榜單」或「📈 個股看盤」點選 ★ 星號，即可將心儀股票加入此處隨時監控！</p>
        </div>
      `;
      return;
    }

    const matchedStocks = starredCodes.map(code => {
      const found = state.stockIndex.find(s => s.code === code);
      return found || { code, name: code, current: '—', decision: '自選追蹤', winRate: '—', group: '未分類' };
    });

    container.innerHTML = matchedStocks.map(stock => {
      const rawWinRate = String(stock.winRate || '—');
      const winRate = rawWinRate === '—' || rawWinRate.includes('%') ? rawWinRate : `${rawWinRate}%`;
      return `
        <article class="watchlist-card" data-code="${escapeHtml(stock.code)}">
          <div class="watchlist-card-left">
            <div class="watchlist-stock-title">
              <span class="watchlist-stock-code">${escapeHtml(stock.code)}</span>
              <span class="watchlist-stock-name">${escapeHtml(stock.name)}</span>
            </div>
            <div class="watchlist-stock-sub">${escapeHtml(stock.group || '一般')} ｜ ${escapeHtml(stock.decision || '持有/觀察')}</div>
          </div>
          <div class="watchlist-card-right">
            <div>
              <div class="watchlist-price">${escapeHtml(formatNumber(stock.current))}</div>
              <div class="watchlist-winrate">勝率 ${escapeHtml(winRate)}</div>
            </div>
            <button type="button" class="evo-star-btn active" data-code="${escapeHtml(stock.code)}" title="取消自選" aria-label="取消自選">★</button>
          </div>
        </article>
      `;
    }).join('');
  }

  // ==================== 個股選擇抽屜與搜尋 ====================
  function renderStockList(query = '') {
    const keyword = String(query || '').trim().toLowerCase();
    const matches = state.stockIndex.filter(item =>
      !keyword || [item.code, item.name, item.group].some(value => String(value || '').toLowerCase().includes(keyword))
    );
    setText('#stockResultCount', `${matches.length} / ${state.stockIndex.length} 檔`);
    $('#stockList').innerHTML = matches.length ? matches.map(item => {
      const rawWinRate = String(item.winRate || '—');
      const winRate = rawWinRate === '—' || rawWinRate.includes('%') ? rawWinRate : `${rawWinRate}%`;
      const starred = isLocallyStarred(item.code);
      return `
      <button type="button" class="stock-list-button${item.code === state.currentCode ? ' active' : ''}" data-code="${escapeHtml(item.code)}">
        <span>
          <span class="stock-list-name">${starred ? '⭐ ' : ''}${escapeHtml(item.code)} ${escapeHtml(item.name)}</span>
          <span class="stock-list-detail">${escapeHtml(item.group || '未分類')}｜${escapeHtml(item.decision || '最新分析')}</span>
        </span>
        <span class="stock-list-numbers">
          <span class="stock-list-winrate">勝率 ${escapeHtml(winRate)}</span>
          <span class="stock-list-price">收 ${escapeHtml(formatNumber(item.current))}</span>
        </span>
      </button>
    `; }).join('') : '<div class="stock-list-empty">找不到符合的個股</div>';
  }

  function openStockPicker() {
    $('#stockPickerOverlay').hidden = false;
    document.body.classList.add('stock-picker-open');
    $('#stockSearch').value = '';
    renderStockList();
    setTimeout(() => $('#stockSearch').focus(), 60);
  }

  function closeStockPicker() {
    $('#stockPickerOverlay').hidden = true;
    document.body.classList.remove('stock-picker-open');
  }

  function renderList(selector, items, emptyText) {
    const list = $(selector);
    const values = Array.isArray(items) && items.length ? items : [emptyText];
    list.innerHTML = values.map(item => `<li>${escapeHtml(item)}</li>`).join('');
  }

  function renderSignalGrid() {
    const summary = state.card.latestAnalysis || state.card;
    const evidence = [...(summary.bullish || []), ...(summary.bearish || [])];
    const definitions = [
      { key: 'kline', icon: '📈', label: 'K線指標', pattern: /^K線[：:]/, fallback: '均線與 K 線狀態請配合下方圖表判讀' },
      { key: 'vol', icon: '📊', label: 'VOL 量能', pattern: /^(?:成交量|VOL)[：:]/i, fallback: '量價與均量線請配合下方圖表判讀' },
      { key: 'rsi', icon: '⚡', label: 'RSI 動能', pattern: /^RSI[：:]/i, fallback: 'RSI 指標請配合下方圖表判讀' },
      { key: 'macd', icon: '🌊', label: 'MACD 趨勢', pattern: /^MACD[：:]/i, fallback: 'MACD 柱狀體與快慢線請配合下方圖表判讀' },
      { key: 'kd', icon: '🎯', label: 'KD 擺盪', pattern: /^KD[：:]/i, fallback: 'KD 指標狀態請配合下方圖表判讀' },
      { key: 'chip', icon: '🏦', label: '籌碼結構', pattern: /^籌碼[：:]/, fallback: '法人動向請配合下方三大法人卡片判讀' }
    ];

    const badges = definitions.map(def => {
      const match = evidence.find(item => def.pattern.test(item));
      const rawText = match ? match.replace(def.pattern, '').trim() : def.fallback;
      const isBearish = /⚡|🚨|⚠️|❄️|📉|🔒|死亡交叉|死叉|頂背離|倒貨|出貨|退潮|警戒|空方|了結|弱勢|賣超|處置/.test(rawText);
      const isNeutral = !match;
      return `
        <article class="signal-card ${isNeutral ? 'neutral' : (isBearish ? 'bear' : 'bull')}">
          <span class="signal-icon">${def.icon}</span>
          <div class="signal-copy">
            <span class="signal-label">${def.label}</span>
            <strong class="signal-text">${escapeHtml(rawText)}</strong>
          </div>
        </article>
      `;
    });
    $('#signalGrid').innerHTML = badges.join('');
  }

  function renderInstitutionCards() {
    const panel = $('#institutionPanel');
    const chartData = state.chartData;
    if (!chartData?.institutions?.length) {
      panel.hidden = true;
      return;
    }
    const latest = chartData.institutions[chartData.institutions.length - 1];
    setText('#institutionDate', `資料日 ${latest.date}`);
    const items = [
      { label: '外資買賣超', value: latest.foreign },
      { label: '投信買賣超', value: latest.trust },
      { label: '自營商買賣超', value: latest.dealer },
      { label: '三大法人合計', value: latest.total }
    ];
    $('#institutionGrid').innerHTML = items.map(item => {
      const cls = item.value > 0 ? 'positive' : (item.value < 0 ? 'negative' : 'neutral');
      return `
        <article class="institution-card ${cls}">
          <span>${item.label}</span>
          <strong class="${cls}">${formatSignedNumber(item.value)}</strong>
        </article>
      `;
    }).join('');
    panel.hidden = false;
  }

  function renderIndicatorGrid() {
    const chartData = state.chartData;
    if (!chartData?.rows?.length) return;
    const latest = chartData.rows[chartData.rows.length - 1];
    setText('#latestKlineDate', latest.date);
    const metrics = [
      { label: 'MA5', value: latest.ma5 },
      { label: 'MA10', value: latest.ma10 },
      { label: 'MA20', value: latest.ma20 },
      { label: 'RSI(6)', value: latest.rsi6 },
      { label: 'RSI(12)', value: latest.rsi12 },
      { label: 'K(9,3)', value: latest.k },
      { label: 'D(9,3)', value: latest.d },
      { label: 'DIF(12,26)', value: latest.dif },
      { label: 'MACD(9)', value: latest.macd },
      { label: 'OSC柱狀體', value: latest.osc },
      { label: '布林上軌', value: latest.bollUpper },
      { label: '布林中軌', value: latest.bollMid },
      { label: '布林下軌', value: latest.bollLower }
    ];
    $('#indicatorGrid').innerHTML = metrics.map(item => `
      <article class="indicator-item">
        <span>${item.label}</span>
        <strong>${formatMetric(item.value)}</strong>
      </article>
    `).join('');
  }

  function formatMetric(value) {
    if (value == null || !Number.isFinite(Number(value))) return '—';
    return Number(value).toFixed(2);
  }

  function formatSignedNumber(value) {
    if (value == null || !Number.isFinite(Number(value))) return '—';
    const num = Number(value);
    const formatted = num.toLocaleString('zh-TW');
    return num > 0 ? `+${formatted}` : formatted;
  }

  function formatPublishedAt(iso) {
    if (!iso) return '—';
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return iso;
    return date.toLocaleString('zh-TW', { hour12: false });
  }

  function renderChart() {
    if (!window.echarts || !state.chartData) return;
    const dom = $('#stockChart');
    if (!dom) return;
    state.chart = state.chart || echarts.init(dom, 'dark', { renderer: 'canvas' });
    const rows = state.chartData.rows || [];
    const dates = rows.map(r => r.date);
    const ohlc = rows.map(r => [r.open, r.close, r.low, r.high]);
    const volumes = rows.map((r, i) => [i, r.volume, r.close >= r.open ? 1 : -1]);

    const option = {
      animation: false,
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' }
      },
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      grid: [
        { left: 42, right: 12, top: '4%', height: '36%' },   // Pane 0: K-Line
        { left: 42, right: 12, top: '43%', height: '10%' },  // Pane 1: VOL
        { left: 42, right: 12, top: '56%', height: '10%' },  // Pane 2: RSI
        { left: 42, right: 12, top: '69%', height: '10%' },  // Pane 3: MACD
        { left: 42, right: 12, top: '82%', height: '10%' }   // Pane 4: KD
      ],
      xAxis: [
        { type: 'category', data: dates, gridIndex: 0, axisLine: { lineStyle: { color: '#334155' } }, axisLabel: { show: false } },
        { type: 'category', data: dates, gridIndex: 1, axisLine: { lineStyle: { color: '#334155' } }, axisLabel: { show: false } },
        { type: 'category', data: dates, gridIndex: 2, axisLine: { lineStyle: { color: '#334155' } }, axisLabel: { show: false } },
        { type: 'category', data: dates, gridIndex: 3, axisLine: { lineStyle: { color: '#334155' } }, axisLabel: { show: false } },
        { type: 'category', data: dates, gridIndex: 4, axisLine: { lineStyle: { color: '#334155' } }, axisLabel: { color: '#94a3b8', fontSize: 10 } }
      ],
      yAxis: [
        { scale: true, gridIndex: 0, splitLine: { lineStyle: { color: '#1e293b' } } },
        { scale: true, gridIndex: 1, splitLine: { lineStyle: { color: '#1e293b' } } },
        { min: 0, max: 100, gridIndex: 2, splitLine: { lineStyle: { color: '#1e293b' } } },
        { scale: true, gridIndex: 3, splitLine: { lineStyle: { color: '#1e293b' } } },
        { min: 0, max: 100, gridIndex: 4, splitLine: { lineStyle: { color: '#1e293b' } } }
      ],
      dataZoom: [
        { type: 'inside', xAxisIndex: [0, 1, 2, 3, 4], start: zoomStart(), end: 100 },
        { type: 'slider', xAxisIndex: [0, 1, 2, 3, 4], start: zoomStart(), end: 100, bottom: 4, height: 16 }
      ],
      series: [
        {
          name: 'K線',
          type: 'candlestick',
          data: ohlc,
          xAxisIndex: 0,
          yAxisIndex: 0,
          itemStyle: {
            color: '#ef4444',
            color0: '#22c55e',
            borderColor: '#ef4444',
            borderColor0: '#22c55e'
          }
        },
        ...(state.showMa ? [
          { name: 'MA5', type: 'line', data: rows.map(r => r.ma5), smooth: true, showSymbol: false, lineStyle: { width: 1.2, color: '#facc15' }, xAxisIndex: 0, yAxisIndex: 0 },
          { name: 'MA10', type: 'line', data: rows.map(r => r.ma10), smooth: true, showSymbol: false, lineStyle: { width: 1.2, color: '#38bdf8' }, xAxisIndex: 0, yAxisIndex: 0 },
          { name: 'MA20', type: 'line', data: rows.map(r => r.ma20), smooth: true, showSymbol: false, lineStyle: { width: 1.4, color: '#ec4899' }, xAxisIndex: 0, yAxisIndex: 0 }
        ] : []),
        ...(state.showBoll ? [
          { name: '上軌', type: 'line', data: rows.map(r => r.bollUpper), smooth: true, showSymbol: false, lineStyle: { width: 1, type: 'dashed', color: 'rgba(148,163,184,0.6)' }, xAxisIndex: 0, yAxisIndex: 0 },
          { name: '下軌', type: 'line', data: rows.map(r => r.bollLower), smooth: true, showSymbol: false, lineStyle: { width: 1, type: 'dashed', color: 'rgba(148,163,184,0.6)' }, xAxisIndex: 0, yAxisIndex: 0 }
        ] : []),
        {
          name: '成交量',
          type: 'bar',
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: volumes.map(v => ({
            value: v[1],
            itemStyle: { color: v[2] > 0 ? '#ef4444' : '#22c55e' }
          }))
        },
        { name: 'RSI(6)', type: 'line', data: rows.map(r => r.rsi6), smooth: true, showSymbol: false, lineStyle: { width: 1, color: '#f59e0b' }, xAxisIndex: 2, yAxisIndex: 2 },
        { name: 'RSI(12)', type: 'line', data: rows.map(r => r.rsi12), smooth: true, showSymbol: false, lineStyle: { width: 1, color: '#38bdf8' }, xAxisIndex: 2, yAxisIndex: 2 },
        {
          name: 'MACD柱',
          type: 'bar',
          xAxisIndex: 3,
          yAxisIndex: 3,
          data: rows.map(r => ({
            value: r.osc,
            itemStyle: { color: r.osc >= 0 ? '#ef4444' : '#22c55e' }
          }))
        },
        { name: 'DIF', type: 'line', data: rows.map(r => r.dif), smooth: true, showSymbol: false, lineStyle: { width: 1, color: '#facc15' }, xAxisIndex: 3, yAxisIndex: 3 },
        { name: 'MACD', type: 'line', data: rows.map(r => r.macd), smooth: true, showSymbol: false, lineStyle: { width: 1, color: '#38bdf8' }, xAxisIndex: 3, yAxisIndex: 3 },
        { name: 'K', type: 'line', data: rows.map(r => r.k), smooth: true, showSymbol: false, lineStyle: { width: 1, color: '#fbbf24' }, xAxisIndex: 4, yAxisIndex: 4 },
        { name: 'D', type: 'line', data: rows.map(r => r.d), smooth: true, showSymbol: false, lineStyle: { width: 1, color: '#38bdf8' }, xAxisIndex: 4, yAxisIndex: 4 }
      ]
    };

    state.chart.setOption(option, true);
  }

  function zoomStart() {
    if (state.period === 'all') return 0;
    const len = state.chartData?.rows?.length || 60;
    const count = Number(state.period) || 20;
    return Math.max(0, Math.floor((1 - count / len) * 100));
  }

  function toggleFullscreenChart() {
    const shell = $('#chartShell');
    shell.classList.toggle('fullscreen');
    if (shell.classList.contains('fullscreen')) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }
    setTimeout(() => state.chart?.resize(), 100);
  }

  function renderMarkdown(markdown = '') {
    const lines = String(markdown || '').split(/\r?\n/);
    const output = [];
    let listType = null;
    const closeList = () => { if (listType) { output.push(`</${listType}>`); listType = null; } };
    const inline = text => escapeHtml(text)
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/`(.+?)`/g, '<code>$1</code>');

    for (let index = 0; index < lines.length; index += 1) {
      const line = lines[index].trim();
      if (!line) { closeList(); continue; }

      if (/^\|.*\|$/.test(line) && /^\|?[\s:|-]+\|?$/.test((lines[index + 1] || '').trim())) {
        closeList();
        const rows = [];
        const header = line.split('|').slice(1, -1).map(cell => cell.trim());
        index += 2;
        while (index < lines.length && /^\|.*\|$/.test(lines[index].trim())) {
          rows.push(lines[index].trim().split('|').slice(1, -1).map(cell => cell.trim()));
          index += 1;
        }
        index -= 1;
        output.push('<div class="md-table-scroll"><table><thead><tr>');
        output.push(header.map(cell => `<th>${inline(cell)}</th>`).join(''));
        output.push('</tr></thead><tbody>');
        rows.forEach(row => output.push(`<tr>${row.map(cell => `<td>${inline(cell)}</td>`).join('')}</tr>`));
        output.push('</tbody></table></div>');
        continue;
      }

      const heading = line.match(/^(#{1,4})\s+(.+)$/);
      if (heading) {
        closeList();
        const level = Math.min(4, heading[1].length + 1);
        output.push(`<h${level}>${inline(heading[2])}</h${level}>`);
        continue;
      }
      if (/^[-*_]{3,}$/.test(line)) { closeList(); output.push('<hr>'); continue; }

      const unordered = line.match(/^[-*•]\s+(.+)$/);
      const ordered = line.match(/^\d+[.)]\s+(.+)$/);
      if (unordered || ordered) {
        const requestedType = unordered ? 'ul' : 'ol';
        if (listType !== requestedType) { closeList(); listType = requestedType; output.push(`<${listType}>`); }
        output.push(`<li>${inline((unordered || ordered)[1])}</li>`);
        continue;
      }

      closeList();
      output.push(`<p>${inline(line)}</p>`);
    }
    closeList();
    return output.join('');
  }

  async function loadStock(code, options = {}) {
    const selected = state.stockIndex.find(item => item.code === String(code));
    if (!selected) throw new Error(`找不到個股 ${code}`);

    clearError();
    $('#stockPickerButton').disabled = true;
    setText('#pickerCurrentStock', `載入 ${selected.code} ${selected.name}…`);
    try {
      const basePath = `./data/stocks/${selected.code}`;
      const [stockPayload, reportHtml, analysisMarkdown] = await Promise.all([
        fetchJson(`${basePath}/stock.json`),
        fetchText(`${basePath}/report.html`),
        fetchText(`${basePath}/analysis.md`)
      ]);
      state.card = { ...stockPayload.stock, latestAnalysis: stockPayload.latestAnalysis || null };
      state.currentCode = selected.code;
      state.chartData = window.PatternParser.parseStockHtml(reportHtml);
      if (state.chart) {
        state.chart.dispose();
        state.chart = null;
      }
      renderCard();
      renderSignalGrid();
      renderInstitutionCards();
      renderIndicatorGrid();
      $('#analysisContent').innerHTML = renderMarkdown(analysisMarkdown);
      document.querySelectorAll('details').forEach(detail => { detail.open = false; });
      renderChart();
      renderStockList($('#stockSearch').value);

      if (options.pushHistory) {
        const url = new URL(location.href);
        url.searchParams.set('code', selected.code);
        history.pushState({ code: selected.code }, '', url);
      } else if (new URLSearchParams(location.search).get('code') !== selected.code) {
        const url = new URL(location.href);
        url.searchParams.set('code', selected.code);
        history.replaceState({ code: selected.code }, '', url);
      }
    } catch (error) {
      showError(error);
      setText('#pickerCurrentStock', `${selected.code} ${selected.name}`);
    } finally {
      $('#stockPickerButton').disabled = false;
    }
  }

  function setupControls() {
    // 1. 底部導航列切換
    $('#bottomNav').addEventListener('click', event => {
      const btn = event.target.closest('.nav-item[data-tab]');
      if (btn) switchTab(btn.dataset.tab);
    });

    // 2. 榜單次級頁籤切換
    $('#rankingSubTabBar').addEventListener('click', event => {
      const btn = event.target.closest('.sub-tab-btn[data-subtab]');
      if (btn) switchRankingSubTab(btn.dataset.subtab);
    });

    // 3. AI 進化榜大卡片點選 (點擊進入看盤，點星號加自選)
    $('#evolutionList').addEventListener('click', event => {
      const starBtn = event.target.closest('.evo-star-btn');
      if (starBtn) {
        event.stopPropagation();
        toggleLocalStar(starBtn.dataset.code);
        return;
      }
      const card = event.target.closest('.evolution-card[data-code]');
      if (card) {
        loadStock(card.dataset.code, { pushHistory: true });
        switchTab('stock');
      }
    });

    // 4. 自選追蹤卡片點選 (點擊進入看盤，點星號取消)
    $('#watchlistCards').addEventListener('click', event => {
      const starBtn = event.target.closest('.evo-star-btn');
      if (starBtn) {
        event.stopPropagation();
        toggleLocalStar(starBtn.dataset.code);
        return;
      }
      const card = event.target.closest('.watchlist-card[data-code]');
      if (card) {
        loadStock(card.dataset.code, { pushHistory: true });
        switchTab('stock');
      }
    });

    // 5. 看盤頁頂部星號按鈕
    $('#heroStarButton').addEventListener('click', () => {
      if (state.currentCode) {
        toggleLocalStar(state.currentCode);
      }
    });

    // 6. K 線期間控制
    $('#periodControls').addEventListener('click', event => {
      const button = event.target.closest('button[data-period]');
      if (!button) return;
      state.period = button.dataset.period === 'all' ? 'all' : Number(button.dataset.period);
      setActiveButton('#periodControls', button);
      renderChart();
    });
    $('#maToggle').addEventListener('change', event => {
      state.showMa = event.target.checked;
      renderChart();
    });
    $('#bollToggle').addEventListener('change', event => {
      state.showBoll = event.target.checked;
      renderChart();
    });
    $('#fullscreenButton').addEventListener('click', toggleFullscreenChart);
    $('#fullscreenCloseButton').addEventListener('click', toggleFullscreenChart);

    // 7. 選股彈窗
    $('#stockPickerButton').addEventListener('click', openStockPicker);
    $('#stockPickerClose').addEventListener('click', closeStockPicker);
    $('#stockPickerOverlay').addEventListener('click', event => {
      if (event.target === $('#stockPickerOverlay')) closeStockPicker();
    });
    $('#stockSearch').addEventListener('input', event => renderStockList(event.target.value));
    $('#stockList').addEventListener('click', event => {
      const button = event.target.closest('button[data-code]');
      if (!button) return;
      closeStockPicker();
      loadStock(button.dataset.code, { pushHistory: true });
      switchTab('stock');
    });

    // 8. 輔助四大榜單點選
    $('#rankingList').addEventListener('click', event => {
      const button = event.target.closest('button[data-code]');
      if (button) {
        loadStock(button.dataset.code, { pushHistory: true });
        switchTab('stock');
      }
    });

    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && !$('#stockPickerOverlay').hidden) closeStockPicker();
    });

    window.addEventListener('popstate', () => {
      const code = new URLSearchParams(location.search).get('code');
      if (code && code !== state.currentCode) {
        loadStock(code, { pushHistory: false });
        switchTab('stock');
      }
    });

    window.addEventListener('resize', () => state.chart?.resize());
  }

  function setActiveButton(containerSelector, activeButton) {
    document.querySelectorAll(`${containerSelector} button`).forEach(button => button.classList.toggle('active', button === activeButton));
  }

  async function start() {
    try {
      if (!window.echarts) throw new Error('ECharts 圖表元件未載入');
      const [indexPayload, manifest, rankings] = await Promise.all([
        fetchJson('./data/index.json'),
        fetchJson('./data/manifest.json'),
        fetchJson('./data/rankings.json')
      ]);
      state.stockIndex = Array.isArray(indexPayload.stocks) ? indexPayload.stocks : [];
      state.manifest = manifest;
      state.rankings = rankings;
      if (!state.stockIndex.length) throw new Error('個股索引為空');

      setupControls();
      updateWatchlistBadge();
      renderStockList();
      renderRankings();

      const requestedCode = new URLSearchParams(location.search).get('code');
      const initialCode = state.stockIndex.some(item => item.code === requestedCode)
        ? requestedCode
        : indexPayload.defaultCode || state.stockIndex[0].code;

      await loadStock(initialCode, { pushHistory: false });

      if (requestedCode) {
        switchTab('stock');
      } else {
        switchTab('ranking');
      }

      // 背景預載快取
      prefetchAllStocks();
    } catch (error) {
      showError(error);
    }
  }

  async function prefetchAllStocks() {
    if (!state.stockIndex || !state.stockIndex.length) return;
    try {
      await offlineStorage.init();
      const statusDot = document.querySelector('.status-dot');
      let cachedCount = 0;
      for (const item of state.stockIndex) {
        const code = item.code;
        const basePath = `./data/stocks/${code}`;
        try {
          await Promise.all([
            fetchText(`${basePath}/stock.json`),
            fetchText(`${basePath}/report.html`),
            fetchText(`${basePath}/analysis.md`)
          ]);
          cachedCount++;
        } catch (_e) {}
      }
      if (statusDot) {
        statusDot.title = `已離線快取 ${cachedCount}/${state.stockIndex.length} 檔個股`;
        statusDot.style.background = '#10b981';
        statusDot.style.boxShadow = '0 0 8px rgba(16,185,129,0.8)';
      }
    } catch (_err) {}
  }

  document.addEventListener('DOMContentLoaded', start);
})();
