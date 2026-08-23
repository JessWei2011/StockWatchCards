// 從 render.js 抽出的純渲染邏輯，給 index.html(StockWatchCards) 跟 reports_manager.html
// (檔案系統) 共用──兩邊都要把同一張 AI 分析卡片畫成一樣的正/反面樣式，抽成共用檔才不用
// 兩邊各維護一份，改規則時也只要改一個地方。這裡的函式全部是純函式(只回傳 HTML 字串或
// 操作固定 id 的彈窗 DOM)，不依賴 STOCK_CARDS 這個全域變數，呼叫端自己傳卡片物件進來。

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

function getSectionInfo(title){
  if(/基本面/.test(title)) return { cls: 'sec-fund', icon: '🏢', name: '基本面觀察' };
  if(/技術/.test(title)) return { cls: 'sec-tech', icon: '📈', name: '技術數據面' };
  if(/籌碼/.test(title)) return { cls: 'sec-chip', icon: '👥', name: '籌碼面解析' };
  if(/訊號/.test(title)) return { cls: 'sec-signal', icon: '⚡', name: '訊號面判定' };
  if(/支持/.test(title)) return { cls: 'sec-bull', icon: '✅', name: '支持進場證據' };
  if(/反對/.test(title)) return { cls: 'sec-bear', icon: '❌', name: '反對進場證據' };
  if(/建議|行動/.test(title)) return { cls: 'sec-action', icon: '🎯', name: '最終持股建議' };
  return { cls: 'sec-default', icon: '📌', name: title };
}

function renderRaw(raw){
  if(!raw) return '';
  const lines = raw.split('\n');

  let html = '';
  let topHeaderLines = [];
  let currentBlock = null;
  let blocks = [];

  const closeCurrentBlock = () => {
    if(currentBlock){
      blocks.push(currentBlock);
      currentBlock = null;
    }
  };

  lines.forEach(rawLine => {
    const line = rawLine.trim();
    if(!line) return;

    if(line.startsWith('【')){
      closeCurrentBlock();
      topHeaderLines.push(line);
      return;
    }

    const cleanLine = line.replace(/^[\*\-\•]\s*/, '');
    const colonIdx = cleanLine.indexOf('：');
    const hasSectionKeyword = /基本面|技術|籌碼|訊號|支持|反對|建議|行動/.test(cleanLine);

    if(hasSectionKeyword && (colonIdx > 0 || line.startsWith('-') || line.startsWith('*'))){
      closeCurrentBlock();

      let titlePart = cleanLine;
      let bodyPart = '';
      if(colonIdx > 0){
        titlePart = cleanLine.substring(0, colonIdx);
        bodyPart = cleanLine.substring(colonIdx + 1).trim();
      }

      const secInfo = getSectionInfo(titlePart);
      currentBlock = {
        cls: secInfo.cls,
        icon: secInfo.icon,
        title: secInfo.name,
        lines: []
      };
      if(bodyPart){
        currentBlock.lines.push(bodyPart);
      }
      return;
    }

    if(currentBlock){
      currentBlock.lines.push(line);
    } else {
      topHeaderLines.push(line);
    }
  });
  closeCurrentBlock();

  if(topHeaderLines.length > 0){
    html += `<div class="raw-top-banner">`;
    topHeaderLines.forEach(h => {
      html += `<div class="raw-header-tag">${escapeHtml(h)}</div>`;
    });
    html += `</div>`;
  }

  blocks.forEach(b => {
    html += `<div class="raw-block ${b.cls}">`;
    html += `<div class="raw-block-title"><span class="raw-block-icon">${b.icon}</span> ${escapeHtml(b.title)}</div>`;
    html += `<div class="raw-block-body">`;

    let inList = false;
    b.lines.forEach(l => {
      const trimmed = l.replace(/^[\*\-\•]\s*/, '');
      if(/^\d+\.\s/.test(trimmed) || l.startsWith('-') || l.startsWith('*') || l.startsWith('•')){
        if(!inList){ html += '<ul class="raw-block-list">'; inList = true; }
        const itemText = trimmed.replace(/^\d+\.\s*/, '');
        html += `<li>${escapeHtml(itemText)}</li>`;
      } else {
        if(inList){ html += '</ul>'; inList = false; }
        html += `<p class="raw-block-text">${escapeHtml(l)}</p>`;
      }
    });
    if(inList) html += '</ul>';

    html += `</div></div>`;
  });

  return html;
}

function evidenceBlock(title, items, cls){
  if(!items || !items.length) return '';
  const lis = items.map(i => `<li>${escapeHtml(i)}</li>`).join('');
  return `<div class="evidence-block ${cls}"><div class="ev-title">${title}</div><ul>${lis}</ul></div>`;
}

function frontFaceHtml(c){
  const colorCat = getScoreColorCat(c.winRate);
  const flashing = isScoreFlashing(c.winRate);
  return `
      <div class="flip-face flip-front color-${colorCat}">

        <div class="card-date-bar">
          <span class="date-label">📅 內容更新日期：</span>
          <span class="date-val">${escapeHtml(c.date)}</span>
        </div>

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

        <div class="decision-pattern-row">
          <span class="decision-badge tag-${colorCat}">${decisionEmoji(c.decision)} ${escapeHtml(c.decision)}</span>
          <span class="pattern-badge">📐 ${escapeHtml(c.pattern)} <small>(信心度:${escapeHtml(c.confidence)})</small></span>
        </div>

        <div class="levels">
          ${levelBlock('壓力', c.resist, 'resist')}
          ${levelBlock('現價', c.current, '')}
          ${levelBlock('買進', c.entry, 'entry')}
          ${levelBlock('停損', c.stop, 'stop')}
        </div>

        <div class="evidence-container">
          ${evidenceBlock('✅ [支持進場證據]', c.bullish, 'bull')}
          ${evidenceBlock('❌ [反對進場證據]', c.bearish, 'bear')}
        </div>

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

// --- 通用放大檢視彈窗：兩個頁面共用同一套邏輯，只要頁面裡有對應 id 的元素即可 ---
// (#zoomModal / #zoomStageInner / #zoomFlipBtn / #zoomCloseBtn，見 index.html 或
// reports_manager.html 裡的 markup)
const ZoomModal = (() => {
  let state = { card: null, face: 'front' };

  function renderFace(){
    const stage = document.getElementById('zoomStageInner');
    const flipBtn = document.getElementById('zoomFlipBtn');
    if(!state.card || !stage) return;
    stage.innerHTML = state.face === 'front' ? frontFaceHtml(state.card) : backFaceHtml(state.card);
    if(flipBtn) flipBtn.textContent = state.face === 'front' ? '🔄 翻面看完整報表' : '🔄 翻回正面';
  }

  function open(card, face){
    if(!card) return;
    state = { card, face: face || 'back' };
    renderFace();
    const modal = document.getElementById('zoomModal');
    if(modal) modal.classList.remove('hidden');
  }

  function close(){
    const modal = document.getElementById('zoomModal');
    if(modal) modal.classList.add('hidden');
    state = { card: null, face: 'back' };
  }

  function setup(){
    const modal = document.getElementById('zoomModal');
    if(!modal) return;
    const closeBtn = document.getElementById('zoomCloseBtn');
    const flipBtn = document.getElementById('zoomFlipBtn');
    if(closeBtn) closeBtn.addEventListener('click', close);
    if(flipBtn) flipBtn.addEventListener('click', () => {
      state.face = state.face === 'front' ? 'back' : 'front';
      renderFace();
    });
    modal.addEventListener('click', e => { if(e.target === modal) close(); });
    document.addEventListener('keydown', e => {
      if(e.key === 'Escape' && !modal.classList.contains('hidden')) close();
    });
  }

  return { open, close, setup };
})();
