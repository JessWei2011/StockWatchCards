const UI_KEY = 'stockCardsUI';
const FONT_KEY = 'stockCardsFontScale';
const FONT_MIN = 0.8;
const FONT_MAX = 1.6;
const FONT_STEP = 0.1;

function loadFontScale(){
  const v = parseFloat(localStorage.getItem(FONT_KEY));
  return isNaN(v) ? 1 : Math.min(FONT_MAX, Math.max(FONT_MIN, v));
}

function applyFontScale(scale){
  document.documentElement.style.setProperty('--font-scale', scale);
  document.getElementById('fontPct').textContent = Math.round(scale * 100) + '%';
  localStorage.setItem(FONT_KEY, scale);
}

function setupFontControl(){
  let scale = loadFontScale();
  applyFontScale(scale);

  document.getElementById('fontUp').addEventListener('click', () => {
    scale = Math.min(FONT_MAX, Math.round((scale + FONT_STEP) * 10) / 10);
    applyFontScale(scale);
  });
  document.getElementById('fontDown').addEventListener('click', () => {
    scale = Math.max(FONT_MIN, Math.round((scale - FONT_STEP) * 10) / 10);
    applyFontScale(scale);
  });
}

function loadUI(){
  const raw = JSON.parse(localStorage.getItem(UI_KEY) || '{}');
  return {
    order: raw.order || [],
    groups: raw.groups || {},
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
  if(/加碼|買進|買入/.test(decision)) return 'buy';
  if(/減碼|賣出|出場/.test(decision)) return 'sell';
  return 'hold';
}

function decisionEmoji(cls){
  return { buy: '🚀', sell: '📉', hold: '🤝' }[cls];
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
  const lis = items.slice(0, 3).map(i => `<li>${escapeHtml(i)}</li>`).join('');
  return `<div class="evidence ${cls}"><div class="ev-title">${title}</div><ul>${lis}</ul></div>`;
}

function renderCard(c, groupName){
  const dCls = decisionClass(c.decision);
  return `
  <div class="flip-card" draggable="true" data-code="${escapeHtml(c.code)}" data-group="${escapeHtml(groupName)}">
    <button class="tag-btn" title="設定分組">🏷️</button>
    <button class="del-btn" title="移除（本機）">✕</button>
    <div class="flip-inner">
      <div class="flip-face flip-front deco-${dCls}">
        <div class="card-title-row">
          <span class="card-code">${escapeHtml(c.code)}</span>
          <span class="card-name">${escapeHtml(c.name)}</span>
          <span class="card-date">${escapeHtml(c.date)}</span>
        </div>
        <div class="badge-row">
          <span class="decision ${dCls}">${decisionEmoji(dCls)} ${escapeHtml(c.decision)}</span>
          <span class="winrate">🎯 勝率 ${escapeHtml(String(c.winRate))}%</span>
          <span class="confidence">✦ 信心度 ${escapeHtml(c.confidence)}</span>
        </div>
        <div class="pattern">📐 ${escapeHtml(c.pattern)}</div>
        <div class="levels">
          ${levelBlock('壓力', c.resist, 'resist')}
          ${levelBlock('現價', c.current, '')}
          ${levelBlock('買進', c.entry, 'entry')}
          ${levelBlock('停損', c.stop, 'stop')}
        </div>
        ${evidenceBlock('✓ 支持', c.bullish, 'bull')}
        ${evidenceBlock('✕ 反對', c.bearish, 'bear')}
        <div class="action">${escapeHtml(c.action)}</div>
        <div class="flip-hint">點擊卡片查看完整分析 →</div>
      </div>
      <div class="flip-face flip-back">
        <div class="flip-back-header">
          <span class="card-code">${escapeHtml(c.code)}</span>
          <span class="card-name">${escapeHtml(c.name)}</span>
          <span class="card-date">${escapeHtml(c.date)}</span>
        </div>
        <div class="flip-back-body">${renderRaw(c.raw)}</div>
        <div class="flip-back-hint">← 點擊卡片返回重點</div>
      </div>
    </div>
  </div>`;
}

function groupAndSort(cards, ui){
  const visible = cards.filter(c => !ui.hidden.includes(c.code));

  const byGroup = {};
  visible.forEach(c => {
    const g = ui.groups[c.code] || '未分組';
    (byGroup[g] = byGroup[g] || []).push(c);
  });

  const orderIndex = code => {
    const i = ui.order.indexOf(code);
    return i === -1 ? Number.MAX_SAFE_INTEGER : i;
  };
  Object.values(byGroup).forEach(list => {
    list.sort((a, b) => orderIndex(a.code) - orderIndex(b.code));
  });

  const groupNames = Object.keys(byGroup).sort((a, b) => {
    if(a === '未分組') return 1;
    if(b === '未分組') return -1;
    return a.localeCompare(b, 'zh-Hant');
  });

  return { byGroup, groupNames };
}

function render(){
  const grid = document.getElementById('grid');
  const searchInput = document.getElementById('search');
  const countEl = document.getElementById('count');
  const ui = loadUI();

  const q = searchInput.value.trim().toLowerCase();
  const filtered = STOCK_CARDS.filter(c =>
    c.code.toLowerCase().includes(q) || c.name.toLowerCase().includes(q)
  );
  const visibleCount = filtered.filter(c => !ui.hidden.includes(c.code)).length;
  countEl.textContent = STOCK_CARDS.length ? `共 ${visibleCount} / ${STOCK_CARDS.length} 檔` : '';

  const { byGroup, groupNames } = groupAndSort(filtered, ui);

  if(groupNames.length === 0){
    grid.innerHTML = `<div class="empty-state">${STOCK_CARDS.length ? '找不到符合的個股' : '尚無分析卡片'}</div>`;
    return;
  }

  grid.innerHTML = groupNames.map(g => `
    <section class="group-section" data-group="${escapeHtml(g)}">
      <h2 class="group-title">${g === '未分組' ? '📂' : '🗂️'} ${escapeHtml(g)}<span class="group-count">${byGroup[g].length}</span></h2>
      <div class="group-grid" data-group="${escapeHtml(g)}">
        ${byGroup[g].map(c => renderCard(c, g)).join('')}
      </div>
    </section>
  `).join('');
}

let dragCode = null;

function setupDragAndDrop(){
  const grid = document.getElementById('grid');

  grid.addEventListener('dragstart', e => {
    const card = e.target.closest('.flip-card');
    if(!card) return;
    dragCode = card.dataset.code;
    card.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
  });

  grid.addEventListener('dragend', e => {
    const card = e.target.closest('.flip-card');
    if(card) card.classList.remove('dragging');
    dragCode = null;
  });

  grid.addEventListener('dragover', e => {
    e.preventDefault();
    const overCard = e.target.closest('.flip-card');
    const container = e.target.closest('.group-grid');
    if(!container) return;
    if(overCard && overCard.dataset.code !== dragCode){
      const rect = overCard.getBoundingClientRect();
      const before = (e.clientX - rect.left) < rect.width / 2;
      container.insertBefore(
        document.querySelector(`.flip-card[data-code="${dragCode}"]`),
        before ? overCard : overCard.nextSibling
      );
    } else if(!overCard){
      container.appendChild(document.querySelector(`.flip-card[data-code="${dragCode}"]`));
    }
  });

  grid.addEventListener('drop', e => {
    e.preventDefault();
    if(!dragCode) return;
    const container = e.target.closest('.group-grid');
    if(!container) return;
    const newGroup = container.dataset.group;

    const ui = loadUI();
    ui.groups[dragCode] = newGroup === '未分組' ? undefined : newGroup;
    if(ui.groups[dragCode] === undefined) delete ui.groups[dragCode];

    ui.order = Array.from(document.querySelectorAll('.flip-card')).map(el => el.dataset.code);

    saveUI(ui);
    render();
  });
}

// ---- group modal ----
const groupModal = () => document.getElementById('groupModal');
const groupInput = () => document.getElementById('groupInput');
let groupModalCode = null;

function allGroupNames(){
  const ui = loadUI();
  return Array.from(new Set(Object.values(ui.groups).filter(Boolean))).sort((a, b) => a.localeCompare(b, 'zh-Hant'));
}

function openGroupModal(code){
  groupModalCode = code;
  const ui = loadUI();
  const list = document.getElementById('groupList');
  list.innerHTML = allGroupNames().map(g => `<option value="${escapeHtml(g)}"></option>`).join('');
  groupInput().value = ui.groups[code] || '';
  groupModal().classList.remove('hidden');
  groupInput().focus();
}
function closeGroupModal(){
  groupModal().classList.add('hidden');
  groupModalCode = null;
}
function commitGroup(name){
  if(!groupModalCode) return;
  const ui = loadUI();
  const trimmed = (name || '').trim();
  if(trimmed) ui.groups[groupModalCode] = trimmed;
  else delete ui.groups[groupModalCode];
  saveUI(ui);
  closeGroupModal();
  render();
}

// ---- confirm modal ----
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
  document.getElementById('groupCancel').addEventListener('click', closeGroupModal);
  document.getElementById('groupClear').addEventListener('click', () => commitGroup(''));
  document.getElementById('groupSave').addEventListener('click', () => commitGroup(groupInput().value));
  groupInput().addEventListener('keydown', e => {
    if(e.key === 'Enter') commitGroup(groupInput().value);
    if(e.key === 'Escape') closeGroupModal();
  });
  groupModal().addEventListener('click', e => { if(e.target === groupModal()) closeGroupModal(); });

  document.getElementById('confirmCancel').addEventListener('click', closeConfirmModal);
  document.getElementById('confirmOk').addEventListener('click', () => {
    const action = confirmAction;
    closeConfirmModal();
    if(action) action();
  });
  confirmModal().addEventListener('click', e => { if(e.target === confirmModal()) closeConfirmModal(); });
}

function setupCardActions(){
  const grid = document.getElementById('grid');

  grid.addEventListener('click', e => {
    if(e.target.closest('.del-btn') || e.target.closest('.tag-btn')) return;
    const card = e.target.closest('.flip-card');
    if(card) card.classList.toggle('flipped');
  });

  grid.addEventListener('click', e => {
    const delBtn = e.target.closest('.del-btn');
    if(delBtn){
      e.stopPropagation();
      const code = delBtn.closest('.flip-card').dataset.code;
      const c = STOCK_CARDS.find(x => x.code === code);
      openConfirmModal(
        `從畫面移除「${c ? c.name : code}」？（僅本機隱藏，不會刪除來源資料；如需真正不再追蹤，請告知 Claude 移除）`,
        () => {
          const ui = loadUI();
          if(!ui.hidden.includes(code)) ui.hidden.push(code);
          saveUI(ui);
          render();
        }
      );
      return;
    }

    const tagBtn = e.target.closest('.tag-btn');
    if(tagBtn){
      e.stopPropagation();
      const code = tagBtn.closest('.flip-card').dataset.code;
      openGroupModal(code);
    }
  });
}

document.addEventListener('DOMContentLoaded', () => {
  const searchInput = document.getElementById('search');
  searchInput.addEventListener('input', render);
  setupDragAndDrop();
  setupCardActions();
  setupModals();
  setupFontControl();
  render();
});
