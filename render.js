// escapeHtml / decisionClass / decisionEmoji / levelBlock / getSectionInfo / renderRaw /
// evidenceBlock / getScoreColorCat / isScoreFlashing / frontFaceHtml / backFaceHtml / ZoomModal
// 都搬到 render-shared.js 了(reports_manager.html 的檔案系統也要畫同一張卡片，抽成共用檔)，
// index.html 記得要在這支 script 之前先載入 render-shared.js。

const UI_KEY = 'stockCardsUI';
const FONT_KEY = 'stockCardsFontScale';
const COLOR_FILTER_KEY = 'stockCardsColorFilter';
const WATCHLIST_KEY = 'stockCardsWatchlist';

const FONT_MIN = 0.8;
const FONT_MAX = 1.6;
const FONT_STEP = 0.1;

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
  fetch('/api/watchlist', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ starred: list })
  }).catch(() => {});
}

async function syncWatchlistFile(){
  try {
    let res = await fetch('/api/watchlist').catch(() => null);
    if (!res || !res.ok) {
      res = await fetch('watchlist.json').catch(() => null);
    }
    if (res && res.ok) {
      const data = await res.json();
      const starred = Array.isArray(data.starred) ? data.starred : (Array.isArray(data) ? data : null);
      if (starred && starred.length > 0) {
        localStorage.setItem(WATCHLIST_KEY, JSON.stringify(starred));
        if (typeof render === 'function') render();
      }
    }
  } catch (e) {}
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
    <button class="zoom-btn" title="放大閱讀完整量化分析報表">🔍</button>

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
// 彈窗本身的邏輯搬到 render-shared.js 的 ZoomModal 共用了，這裡只留「用股票代號查卡片」
// 這個 index.html 專屬的查找方式(reports_manager.html 沒有 STOCK_CARDS 全域變數，
// 它是直接把卡片物件傳給 ZoomModal.open()，不會用到這個 wrapper)。
function openZoomModal(code){
  const c = getLatestCardByCode(code);
  if(c) ZoomModal.open(c);
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
  ZoomModal.setup();
  render();
  syncWatchlistFile();
});
