const UI_KEY = 'stockCardsUI';
const FONT_KEY = 'stockCardsFontScale';
const COLOR_FILTER_KEY = 'stockCardsColorFilter';
const WATCHLIST_KEY = 'stockCardsWatchlist';

const FONT_MIN = 0.8;
const FONT_MAX = 1.6;
const FONT_STEP = 0.1;

// --- Color Category Helper ---
function getScoreColorCat(winRate){
  const score = Number(winRate) || 0;
  if(score >= 70) return 'red';
  if(score >= 60) return 'orange';
  if(score >= 50) return 'yellow';
  return 'gray';
}

function isScoreFlashing(winRate){
  return (Number(winRate) || 0) >= 80;
}

// --- LocalStorage Helpers ---
function loadFontScale(){
  const v = parseFloat(localStorage.getItem(FONT_KEY));
  return isNaN(v) ? 1 : Math.min(FONT_MAX, Math.max(FONT_MIN, v));
}

function applyFontScale(scale){
  document.documentElement.style.setProperty('--font-scale', scale);
  const fontPctEl = document.getElementById('fontPct');
  if(fontPctEl) fontPctEl.textContent = Math.round(scale * 100) + '%';
  localStorage.setItem(FONT_KEY, scale);
}

function setupFontControl(){
  let scale = loadFontScale();
  applyFontScale(scale);

  const fontUpBtn = document.getElementById('fontUp');
  const fontDownBtn = document.getElementById('fontDown');

  if(fontUpBtn){
    fontUpBtn.addEventListener('click', () => {
      scale = Math.min(FONT_MAX, Math.round((scale + FONT_STEP) * 10) / 10);
      applyFontScale(scale);
    });
  }
  if(fontDownBtn){
    fontDownBtn.addEventListener('click', () => {
      scale = Math.max(FONT_MIN, Math.round((scale - FONT_STEP) * 10) / 10);
      applyFontScale(scale);
    });
  }
}

function loadColorFilter(){
  return localStorage.getItem(COLOR_FILTER_KEY) || 'all';
}
function saveColorFilter(filter){
  localStorage.setItem(COLOR_FILTER_KEY, filter);
}

function loadWatchlist(){
  try {
    return JSON.parse(localStorage.getItem(WATCHLIST_KEY)) || [];
  } catch (e) {
    return [];
  }
}
function saveWatchlist(list){
  localStorage.setItem(WATCHLIST_KEY, JSON.stringify(list));
}

function loadUI(){
  const raw = JSON.parse(localStorage.getItem(UI_KEY) || '{}');
  return {
    hidden: raw.hidden || []
  };
}
function saveUI(ui){
  localStorage.setItem(UI_KEY, JSON.stringify(ui));
}

function escapeHtml(s){
  return (s || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function decisionClass(decision){
  if(/加碼|買進|買入|進場/.test(decision)) return 'buy';
  if(/減碼|賣出|出場/.test(decision)) return 'sell';
  return 'hold';
}

function decisionEmoji(decision){
  if(/進場|買進/.test(decision)) return '🚀';
  if(/加碼/.test(decision)) return '🔥';
  if(/減碼|賣出|出場/.test(decision)) return '📉';
  return '🤝';
}

function levelBlock(label, val, cls){
  return `<div class="level ${cls}"><div class="l-label">${label}</div><div class="l-val">${escapeHtml(val || '—')}</div></div>`;
}

function sectionColorClass(title){
  if(/支持/.test(title)) return 'sec-bull';
  if(/反對/.test(title)) return 'sec-bear';
  if(/籌碼/.test(title)) return 'sec-chip';
  if(/技術/.test(title)) return 'sec-tech';
  if(/基本面/.test(title)) return 'sec-fund';
  if(/訊號/.test(title)) return 'sec-signal';
  if(/建議|行動/.test(title)) return 'sec-action';
  return 'sec-default';
}

function renderRaw(raw){
  const lines = (raw || '').split('\n');
  let html = '';
  let inList = false;
  let currentColor = 'sec-default';

  const closeList = () => { if(inList){ html += '</ul>'; inList = false; } };

  lines.forEach(rawLine => {
    const line = rawLine.replace(/\r$/, '');
    if(!line.trim()) return;
    const leading = line.match(/^\s*/)[0].length;
    const trimmed = line.trim();

    if(trimmed.startsWith('【')){
      closeList();
      html += `<div class="raw-header">${escapeHtml(trimmed)}</div>`;
      return;
    }

    if(/^\d+\.\s/.test(trimmed)){
      if(!inList){ html += `<ul class="raw-list ${currentColor} raw-sub">`; inList = true; }
      html += `<li>${escapeHtml(trimmed.replace(/^\d+\.\s*/, ''))}</li>`;
      return;
    }

    if(trimmed.startsWith('*')){
      const content = trimmed.replace(/^\*\s*/, '');
      if(leading === 0){
        closeList();
        currentColor = sectionColorClass(content);
        html += `<div class="raw-section-title ${currentColor}">${escapeHtml(content)}</div>`;
      } else {
        if(!inList){ html += `<ul class="raw-list ${currentColor}">`; inList = true; }
        html += `<li>${escapeHtml(content)}</li>`;
      }
      return;
    }

    closeList();
    html += `<div class="raw-line">${escapeHtml(trimmed)}</div>`;
  });
  closeList();
  return html;
}

function evidenceBlock(title, items, cls){
  if(!items || !items.length) return '';
  const lis = items.map(i => `<li>${escapeHtml(i)}</li>`).join('');
  return `<div class="evidence-block ${cls}"><div class="ev-title">${title}</div><ul>${lis}</ul></div>`;
}

// --- Card Face Builders (shared by grid cards & zoom modal) ---
function frontFaceHtml(c){
  const colorCat = getScoreColorCat(c.winRate);
  const flashing = isScoreFlashing(c.winRate);
  return `
      <div class="flip-face flip-front color-${colorCat}">

        <!-- Top bar: Date -->
        <div class="card-date-bar">
          <span class="date-label">📅 內容更新日期：</span>
          <span class="date-val">${escapeHtml(c.date)}</span>
        </div>

        <!-- Upper section: Stock Info & Score -->
        <div class="card-header-main">
          <div class="stock-title">
            <span class="card-code">${escapeHtml(c.code)}</span>
            <span class="card-name">${escapeHtml(c.name)}</span>
          </div>
          <div class="score-badge badge-${colorCat} ${flashing ? 'flash-score' : ''}">
            <span class="score-num">${c.winRate}</span>
            <span class="score-unit">分</span>
          </div>
        </div>

        <!-- Decision & Pattern -->
        <div class="decision-pattern-row">
          <span class="decision-badge tag-${colorCat}">${decisionEmoji(c.decision)} ${escapeHtml(c.decision)}</span>
          <span class="pattern-badge">📐 ${escapeHtml(c.pattern)} <small>(信心度:${escapeHtml(c.confidence)})</small></span>
        </div>

        <!-- Price Levels -->
        <div class="levels">
          ${levelBlock('壓力', c.resist, 'resist')}
          ${levelBlock('現價', c.current, '')}
          ${levelBlock('買進', c.entry, 'entry')}
          ${levelBlock('停損', c.stop, 'stop')}
        </div>

        <!-- Lower Section: Evidence -->
        <div class="evidence-container">
          ${evidenceBlock('✅ [支持進場證據]', c.bullish, 'bull')}
          ${evidenceBlock('❌ [反對進場證據]', c.bearish, 'bear')}
        </div>

        <!-- Action Summary -->
        ${c.action ? `<div class="action-summary">${escapeHtml(c.action)}</div>` : ''}
      </div>`;
}

function backFaceHtml(c){
  return `
      <div class="flip-face flip-back">
        <div class="flip-back-header">
          <span class="card-code">${escapeHtml(c.code)}</span>
          <span class="card-name">${escapeHtml(c.name)}</span>
          <span class="card-date">更新：${escapeHtml(c.date)}</span>
        </div>
        <div class="flip-back-body">${renderRaw(c.raw)}</div>
      </div>`;
}

function getLatestCardByCode(code){
  const matches = STOCK_CARDS.filter(c => c.code === code);
  if(!matches.length) return null;
  return matches.reduce((a, b) => new Date(b.date) > new Date(a.date) ? b : a);
}

// --- Render Card Function ---
function renderCard(c, isStarred){
  const flashing = isScoreFlashing(c.winRate);
  const isDisp = c.isDisposition || Boolean(c.dispositionDate);

  return `
  <div class="flip-card ${flashing ? 'flashing' : ''}" data-code="${escapeHtml(c.code)}" data-score="${c.winRate}">

    <!-- Watchlist Star Toggle Button -->
    <button class="star-btn ${isStarred ? 'starred' : ''}" title="${isStarred ? '從重點觀察區移除' : '移至重點觀察區'}">
      ${isStarred ? '⭐' : '☆'}
    </button>

    <!-- Zoom / Enlarge Card Button -->
    <button class="zoom-btn" title="放大檢視完整量化分析報表">🔍</button>

    <!-- Delete/Hide Card Button -->
    <button class="del-btn" title="隱藏本機卡片">✕</button>

    <!-- Disposition Badge (關緊閉) -->
    ${isDisp ? `
      <div class="disposition-badge" title="處置股票（關緊閉期間）">
        <span class="x-mark">✕</span> 關緊閉 ${escapeHtml(c.dispositionDate || '處置中')}
      </div>
    ` : ''}

    <div class="flip-inner">
${frontFaceHtml(c)}
    </div>
  </div>`;
}

// --- Core Render Logic ---
let colorFilter = loadColorFilter();

function render(){
  const watchlistGrid = document.getElementById('watchlistGrid');
  const allGrid = document.getElementById('allGrid');
  const watchlistCountEl = document.getElementById('watchlistCount');
  const allStocksCountEl = document.getElementById('allStocksCount');
  const searchInput = document.getElementById('search');
  const countEl = document.getElementById('count');

  const ui = loadUI();
  const watchlist = loadWatchlist();

  const q = searchInput.value.trim().toLowerCase();

  // Deduplicate by stock code (keep latest date entry)
  const latestByCode = {};
  STOCK_CARDS.forEach(c => {
    if(!latestByCode[c.code] || new Date(c.date) > new Date(latestByCode[c.code].date)){
      latestByCode[c.code] = c;
    }
  });
  const uniqueCards = Object.values(latestByCode);

  // Filter cards by search & color
  const filtered = uniqueCards.filter(c => {
    if(ui.hidden.includes(c.code)) return false;
    const matchesSearch = c.code.toLowerCase().includes(q) || c.name.toLowerCase().includes(q);
    const cat = getScoreColorCat(c.winRate);
    const matchesColor = colorFilter === 'all' || cat === colorFilter;
    return matchesSearch && matchesColor;
  });

  // Split into Watchlist vs All Stocks
  const watchlistCards = filtered.filter(c => watchlist.includes(c.code));
  const otherCards = filtered.filter(c => !watchlist.includes(c.code));

  // Sort both arrays in descending order by score (winRate)
  watchlistCards.sort((a, b) => (b.winRate || 0) - (a.winRate || 0));
  otherCards.sort((a, b) => (b.winRate || 0) - (a.winRate || 0));

  countEl.textContent = uniqueCards.length ? `共 ${filtered.length} / ${uniqueCards.length} 檔` : '';
  watchlistCountEl.textContent = `${watchlistCards.length} 檔`;
  allStocksCountEl.textContent = `${otherCards.length} 檔`;

  // Render Watchlist Zone
  if(watchlistCards.length === 0){
    watchlistGrid.innerHTML = `
      <div class="empty-watchlist">
        ⭐ 尚無重點觀察個股。點擊下方個股卡片右上角的「☆」按鈕即可移至重點觀察區！
      </div>`;
  } else {
    watchlistGrid.innerHTML = watchlistCards.map(c => renderCard(c, true)).join('');
  }

  // Render All Stocks Zone
  if(otherCards.length === 0){
    allGrid.innerHTML = `
      <div class="empty-state">
        ${watchlistCards.length > 0 ? '其餘個股皆已移至重點觀察區' : '尚無符合條件的個股'}
      </div>`;
  } else {
    allGrid.innerHTML = otherCards.map(c => renderCard(c, false)).join('');
  }
}

// --- Confirm Modal for Hidden Cards ---
const confirmModal = () => document.getElementById('confirmModal');
let confirmAction = null;

function openConfirmModal(text, onOk){
  document.getElementById('confirmText').textContent = text;
  confirmAction = onOk;
  confirmModal().classList.remove('hidden');
}
function closeConfirmModal(){
  confirmModal().classList.add('hidden');
  confirmAction = null;
}

function setupModals(){
  document.getElementById('confirmCancel').addEventListener('click', closeConfirmModal);
  document.getElementById('confirmOk').addEventListener('click', () => {
    const action = confirmAction;
    closeConfirmModal();
    if(action) action();
  });
  confirmModal().addEventListener('click', e => { if(e.target === confirmModal()) closeConfirmModal(); });
}

// --- Card Interactions ---
function setupCardActions(){
  const main = document.querySelector('main');

  // Open Zoom Modal
  main.addEventListener('click', e => {
    const zoomBtn = e.target.closest('.zoom-btn');
    if(!zoomBtn) return;
    e.stopPropagation();
    const card = zoomBtn.closest('.flip-card');
    if(card) openZoomModal(card.dataset.code);
  });

  // Toggle Star / Watchlist
  main.addEventListener('click', e => {
    const starBtn = e.target.closest('.star-btn');
    if(!starBtn) return;
    e.stopPropagation();
    const card = starBtn.closest('.flip-card');
    const code = card.dataset.code;

    const watchlist = loadWatchlist();
    const idx = watchlist.indexOf(code);
    if(idx >= 0){
      watchlist.splice(idx, 1);
    } else {
      watchlist.push(code);
    }
    saveWatchlist(watchlist);
    render();
  });

  // Hide Card
  main.addEventListener('click', e => {
    const delBtn = e.target.closest('.del-btn');
    if(!delBtn) return;
    e.stopPropagation();
    const code = delBtn.closest('.flip-card').dataset.code;
    const c = STOCK_CARDS.find(x => x.code === code);
    openConfirmModal(
      `確定在畫面上隱藏「${c ? c.name : code}」？（本機隱藏設定會同步儲存）`,
      () => {
        const ui = loadUI();
        if(!ui.hidden.includes(code)) ui.hidden.push(code);
        saveUI(ui);
        render();
      }
    );
  });
}

// --- Zoom Modal (enlarged card view) ---
let zoomState = { code: null, face: 'front' };

function renderZoomFace(){
  const c = getLatestCardByCode(zoomState.code);
  const stage = document.getElementById('zoomStageInner');
  const flipBtn = document.getElementById('zoomFlipBtn');
  if(!c || !stage) return;
  stage.innerHTML = zoomState.face === 'front' ? frontFaceHtml(c) : backFaceHtml(c);
  if(flipBtn) flipBtn.textContent = zoomState.face === 'front' ? '🔄 翻面看完整報表' : '🔄 翻回正面';
}

function openZoomModal(code){
  zoomState = { code, face: 'front' };
  renderZoomFace();
  document.getElementById('zoomModal').classList.remove('hidden');
}

function closeZoomModal(){
  document.getElementById('zoomModal').classList.add('hidden');
  zoomState = { code: null, face: 'front' };
}

function setupZoomModal(){
  const modal = document.getElementById('zoomModal');
  if(!modal) return;

  document.getElementById('zoomCloseBtn').addEventListener('click', closeZoomModal);

  document.getElementById('zoomFlipBtn').addEventListener('click', () => {
    zoomState.face = zoomState.face === 'front' ? 'back' : 'front';
    renderZoomFace();
  });

  modal.addEventListener('click', e => {
    if(e.target === modal) closeZoomModal();
  });

  document.addEventListener('keydown', e => {
    if(e.key === 'Escape' && !modal.classList.contains('hidden')) closeZoomModal();
  });
}

function setupColorFilter(){
  const bar = document.getElementById('colorFilter');
  if(!bar) return;

  bar.querySelectorAll('button[data-filter]').forEach(b => {
    b.classList.toggle('active', b.dataset.filter === colorFilter);
  });

  bar.addEventListener('click', e => {
    const btn = e.target.closest('button[data-filter]');
    if(!btn) return;
    colorFilter = btn.dataset.filter;
    saveColorFilter(colorFilter);
    bar.querySelectorAll('button[data-filter]').forEach(b => b.classList.toggle('active', b === btn));
    render();
  });
}

document.addEventListener('DOMContentLoaded', () => {
  const searchInput = document.getElementById('search');
  if(searchInput) searchInput.addEventListener('input', render);
  setupCardActions();
  setupModals();
  setupFontControl();
  setupColorFilter();
  setupZoomModal();
  render();
});
