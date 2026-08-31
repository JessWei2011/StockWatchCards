(() => {
  'use strict';

  const state = {
    card: null,
    manifest: null,
    stockIndex: [],
    currentCode: '',
    chartData: null,
    chart: null,
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

  async function fetchText(path) {
    const response = await fetch(path, { cache: 'no-store' });
    if (!response.ok) throw new Error(`${path} 載入失敗 (${response.status})`);
    return response.text();
  }

  async function fetchJson(path) {
    return JSON.parse(await fetchText(path));
  }

  function showError(error) {
    const banner = $('#errorBanner');
    banner.hidden = false;
    banner.textContent = `資料載入失敗：${error.message || error}`;
    console.error(error);
  }

  function clearError() {
    const banner = $('#errorBanner');
    banner.hidden = true;
    banner.textContent = '';
  }

  function setText(selector, value) {
    const element = $(selector);
    if (element) element.textContent = value == null || value === '' ? '—' : value;
  }

  function renderCard() {
    const card = state.card;
    const summary = card.latestAnalysis || card;
    const manifest = state.manifest;
    setText('#stockCode', card.code);
    setText('#stockName', card.name);
    setText('#analysisDate', `分析 ${summary.date || manifest.analysisDate}`);
    setText('#winRate', String(summary.winRate || '—').includes('%') ? summary.winRate : `${summary.winRate}%`);
    setText('#decisionTitle', summary.decision);
    setText('#currentPrice', formatNumber(summary.current));
    setText('#entryPrice', formatNumber(summary.entry));
    setText('#stopPrice', formatNumber(summary.stop));
    setText('#resistPrice', formatNumber(summary.resist));
    setText('#actionText', summary.action);
    setText('#publishedAt', `發布時間：${formatPublishedAt(manifest.publishedAt)}`);
    renderList('#bullishList', summary.bullish, '目前無額外支持證據');
    renderList('#bearishList', summary.bearish, '目前無額外反對證據');
    setText('#pickerCurrentStock', `${card.code} ${card.name}`);
    document.title = `${card.code} ${card.name}｜手機個股分析中心`;
    $('#stockChart').setAttribute('aria-label', `${card.name} K 線、成交量與技術指標互動圖`);
  }

  function renderStockList(query = '') {
    const keyword = String(query || '').trim().toLowerCase();
    const matches = state.stockIndex.filter(item =>
      !keyword || [item.code, item.name, item.group].some(value => String(value || '').toLowerCase().includes(keyword))
    );
    setText('#stockResultCount', `${matches.length} / ${state.stockIndex.length} 檔`);
    $('#stockList').innerHTML = matches.length ? matches.map(item => {
      const rawWinRate = String(item.winRate || '—');
      const winRate = rawWinRate === '—' || rawWinRate.includes('%') ? rawWinRate : `${rawWinRate}%`;
      return `
      <button type="button" class="stock-list-button${item.code === state.currentCode ? ' active' : ''}" data-code="${escapeHtml(item.code)}">
        <span>
          <span class="stock-list-name">${item.isStarred ? '⭐ ' : ''}${escapeHtml(item.code)} ${escapeHtml(item.name)}</span>
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
    $('#stockPickerButton').focus();
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
      { key: 'macd', icon: '🌊', label: 'MACD 動能', pattern: /^MACD[：:]/i, fallback: 'MACD 動能資料正常' },
      { key: 'rsi', icon: '🎨', label: 'RSI 強弱', pattern: /^RSI[：:]/i, fallback: 'RSI 強弱資料正常' },
      { key: 'kd', icon: '⚡', label: 'KD 轉折', pattern: /^KD[：:]/i, fallback: 'KD 轉折資料正常' },
      { key: 'chip', icon: '🏛️', label: '籌碼／法人動向', pattern: /^籌碼[：:]/, fallback: '法人籌碼資料不足' }
    ];
    $('#signalGrid').innerHTML = definitions.map(definition => {
      const source = evidence.find(item => definition.pattern.test(String(item).trim()));
      const value = source ? String(source).replace(definition.pattern, '').trim() : definition.fallback;
      return `<article class="signal-card ${definition.key}">` +
        `<div class="signal-label"><span>${definition.icon}</span><span>${escapeHtml(definition.label)}</span></div>` +
        `<p class="signal-value">${escapeHtml(value)}</p></article>`;
    }).join('');
  }

  function renderInstitutionCards() {
    const institution = state.chartData.institutionLatest;
    const panel = $('#institutionPanel');
    if (!institution) {
      panel.hidden = true;
      return;
    }
    const formatSigned = value => {
      const number = Number(value);
      if (!Number.isFinite(number)) return '—';
      return `${number > 0 ? '+' : ''}${number.toLocaleString('zh-TW')}`;
    };
    const tone = value => Number(value) > 0 ? 'positive' : Number(value) < 0 ? 'negative' : 'neutral';
    const items = [
      ['外資', institution.foreign],
      ['投信', institution.trust],
      ['自營商', institution.dealer],
      ['法人合計', institution.total]
    ];
    setText('#institutionDate', institution.date.replace(/^\d{4}-/, '').replace('-', '/'));
    $('#institutionGrid').innerHTML = items.map(([label, value]) =>
      `<article class="institution-card"><span>${escapeHtml(label)}</span>` +
      `<strong class="${tone(value)}">${escapeHtml(formatSigned(value))}</strong></article>`
    ).join('');
    panel.hidden = false;
  }

  function formatPublishedAt(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return new Intl.DateTimeFormat('zh-TW', {
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hour12: false
    }).format(date);
  }

  function renderIndicatorGrid() {
    const data = state.chartData;
    setText('#latestKlineDate', last(data.dates));
    const items = [
      ['收盤', last(data.candles)?.[1]],
      ['MA5', last(data.ma5)],
      ['MA10', last(data.ma10)],
      ['MA20', last(data.ma20)],
      ['MA60', last(data.ma60)],
      ['MA120', last(data.ma120)],
      ['RSI(6)', last(data.rsi6)],
      ['RSI(12)', last(data.rsi12)],
      ['成交量', last(data.volumes)],
      ['MV5 快線', last(data.vma5)],
      ['MV20 慢線', last(data.vma20)],
      ['MACD柱體', last(data.macdHist)],
      ['DIF 快線', last(data.dif)],
      ['MACD 慢線', last(data.macdSignal)],
      ['K / D', `${formatNumber(last(data.k))} / ${formatNumber(last(data.d))}`],
      ['布林上', last(data.bollUpper)],
      ['布林中', last(data.bollMid)],
      ['布林下', last(data.bollLower)]
    ];
    $('#indicatorGrid').innerHTML = items.map(([label, value]) => `
      <div class="indicator-item"><span>${escapeHtml(label)}</span><strong>${escapeHtml(typeof value === 'string' ? value : formatNumber(value))}</strong></div>
    `).join('');
  }

  function zoomStart() {
    const total = state.chartData.dates.length;
    if (state.period === 'all' || Number(state.period) >= total) return 0;
    return Math.max(0, ((total - Number(state.period)) / total) * 100);
  }

  function lineSeries(name, values, color, width = 1.4) {
    return {
      name, type: 'line', data: values, xAxisIndex: 0, yAxisIndex: 0,
      showSymbol: false, connectNulls: false, smooth: false,
      lineStyle: { color, width }, itemStyle: { color }, emphasis: { disabled: true }
    };
  }

  function thresholdLines(values) {
    return {
      silent: true,
      symbol: 'none',
      label: { show: false },
      lineStyle: { color: '#475569', type: 'dashed', width: 1 },
      data: values.map(yAxis => ({ yAxis }))
    };
  }

  function renderChart() {
    const data = state.chartData;
    if (!state.chart) state.chart = echarts.init($('#stockChart'), 'dark', { renderer: 'canvas' });
    const previousLegendSelected = state.chart.getOption()?.legend?.[0]?.selected || null;

    const series = [
      {
        name: 'K線', type: 'candlestick', xAxisIndex: 0, yAxisIndex: 0, data: data.candles,
        itemStyle: { color: 'transparent', color0: 'transparent', borderColor: '#ef4444', borderColor0: '#10b981', borderWidth: 2 }
      }
    ];
    if (state.showMa) {
      series.push(
        lineSeries('MA5', data.ma5, '#f59e0b', 1.5),
        lineSeries('MA10', data.ma10, '#3b82f6', 1.5),
        lineSeries('MA20', data.ma20, '#ec4899', 1.5),
        lineSeries('MA60', data.ma60, '#10b981', 1.5),
        lineSeries('MA120', data.ma120, '#8b5cf6', 1.5)
      );
    }
    if (state.showBoll) {
      series.push(
        lineSeries('BOLL上軌', data.bollUpper, '#a855f7', 1),
        lineSeries('BOLL下軌', data.bollLower, '#a855f7', 1)
      );
    }
    series.push({
      name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1,
      data: data.volumes.map((value, index) => ({
        value,
        itemStyle: {
          color: 'transparent',
          borderColor: data.candles[index][1] >= data.candles[index][0] ? '#ef4444' : '#10b981',
          borderWidth: 2
        }
      }))
    });
    series.push(
      { name: 'MV5', type: 'line', xAxisIndex: 1, yAxisIndex: 1, data: data.vma5, showSymbol: false, smooth: true, lineStyle: { color: '#38bdf8', width: 1.5 } },
      { name: 'MV20', type: 'line', xAxisIndex: 1, yAxisIndex: 1, data: data.vma20, showSymbol: false, smooth: true, lineStyle: { color: '#f59e0b', width: 1.5 } },
      { name: 'RSI(6)', type: 'line', xAxisIndex: 2, yAxisIndex: 2, data: data.rsi6, showSymbol: false, smooth: true, lineStyle: { color: '#38bdf8', width: 1.7 }, markLine: thresholdLines([20, 80]) },
      { name: 'RSI(12)', type: 'line', xAxisIndex: 2, yAxisIndex: 2, data: data.rsi12, showSymbol: false, smooth: true, lineStyle: { color: '#f59e0b', width: 1.6 } },
      {
        name: 'MACD柱體', type: 'bar', xAxisIndex: 3, yAxisIndex: 3,
        data: data.macdHist.map(value => ({
          value,
          itemStyle: { color: 'transparent', borderColor: Number(value) >= 0 ? '#ef4444' : '#10b981', borderWidth: 2 }
        }))
      },
      { name: 'DIF快線', type: 'line', xAxisIndex: 3, yAxisIndex: 3, data: data.dif, showSymbol: false, smooth: true, lineStyle: { color: '#38bdf8', width: 1.7 } },
      { name: 'MACD慢線', type: 'line', xAxisIndex: 3, yAxisIndex: 3, data: data.macdSignal, showSymbol: false, smooth: true, lineStyle: { color: '#f59e0b', width: 1.7 }, markLine: thresholdLines([0]) },
      { name: 'K值', type: 'line', xAxisIndex: 4, yAxisIndex: 4, data: data.k, showSymbol: false, smooth: true, lineStyle: { color: '#38bdf8', width: 1.7 }, markLine: thresholdLines([20, 80]) },
      { name: 'D值', type: 'line', xAxisIndex: 4, yAxisIndex: 4, data: data.d, showSymbol: false, smooth: true, lineStyle: { color: '#f59e0b', width: 1.7 } }
    );

    state.chart.setOption({
      backgroundColor: '#0b1020',
      animation: false,
      textStyle: { fontFamily: '-apple-system, BlinkMacSystemFont, Segoe UI, Microsoft JhengHei, sans-serif' },
      legend: {
        top: 4, left: 42, right: 8, type: 'scroll', itemWidth: 14, itemHeight: 8,
        textStyle: { color: '#9aabc3', fontSize: 9 },
        selected: Object.assign({ MA60: false, MA120: false }, previousLegendSelected || {})
      },
      tooltip: {
        trigger: 'axis', confine: true, backgroundColor: 'rgba(8,12,23,.96)', borderColor: '#334155',
        textStyle: { color: '#e5edf8', fontSize: 11 }, axisPointer: { type: 'cross', link: [{ xAxisIndex: 'all' }] },
        formatter: params => tooltipHtml(params, data)
      },
      axisPointer: { link: [{ xAxisIndex: 'all' }], label: { backgroundColor: '#2563eb' } },
      grid: [
        { left: 45, right: 10, top: '6%', height: '34%' },
        { left: 45, right: 10, top: '43%', height: '10%' },
        { left: 45, right: 10, top: '56%', height: '10%' },
        { left: 45, right: 10, top: '69%', height: '10%' },
        { left: 45, right: 10, top: '82%', height: '10%' }
      ],
      xAxis: [0, 1, 2, 3, 4].map((_, index) => ({
        type: 'category', data: data.dates, gridIndex: index, boundaryGap: true,
        axisLabel: { show: index === 4, color: '#718198', fontSize: 9 },
        axisLine: { lineStyle: { color: '#263249' } }, axisTick: { show: false },
        splitLine: { show: false }, min: 'dataMin', max: 'dataMax'
      })),
      yAxis: [
        { scale: true, gridIndex: 0, axisLabel: { color: '#718198', fontSize: 9 }, splitLine: { lineStyle: { color: '#182236' } } },
        { scale: true, gridIndex: 1, axisLabel: { color: '#718198', fontSize: 8 }, splitLine: { show: false } },
        { min: 0, max: 100, gridIndex: 2, axisLabel: { color: '#718198', fontSize: 8 }, splitLine: { lineStyle: { color: '#182236' } } },
        { scale: true, gridIndex: 3, axisLabel: { color: '#718198', fontSize: 8 }, splitLine: { lineStyle: { color: '#182236' } } },
        { min: 0, max: 100, gridIndex: 4, axisLabel: { color: '#718198', fontSize: 8 }, splitLine: { lineStyle: { color: '#182236' } } }
      ],
      dataZoom: [
        { type: 'inside', xAxisIndex: [0, 1, 2, 3, 4], start: zoomStart(), end: 100, zoomOnMouseWheel: true, moveOnMouseMove: true, moveOnMouseWheel: false },
        {
          type: 'slider', xAxisIndex: [0, 1, 2, 3, 4], start: zoomStart(), end: 100, bottom: 5, height: 18,
          borderColor: '#263249', backgroundColor: '#0b1220', fillerColor: 'rgba(37,99,235,.25)',
          handleStyle: { color: '#60a5fa' }, textStyle: { color: '#718198', fontSize: 8 }
        }
      ],
      series
    }, true);
  }

  function tooltipHtml(params, data) {
    const index = params?.[0]?.dataIndex;
    if (!Number.isInteger(index)) return '';
    const candle = data.candles[index];
    const indicators = [
      `VOL ${formatNumber(data.volumes[index])}｜MV5 ${formatNumber(data.vma5[index])}｜MV20 ${formatNumber(data.vma20[index])}`,
      `RSI6 ${formatNumber(data.rsi6[index])}｜RSI12 ${formatNumber(data.rsi12[index])}`,
      `MACD ${formatNumber(data.macdHist[index])}｜DIF ${formatNumber(data.dif[index])}｜慢線 ${formatNumber(data.macdSignal[index])}`,
      `K ${formatNumber(data.k[index])}｜D ${formatNumber(data.d[index])}`
    ].join('<br>');
    return `<strong>${escapeHtml(data.dates[index])}</strong><br>` +
      `開 ${formatNumber(candle[0])}｜高 ${formatNumber(candle[3])}<br>` +
      `低 ${formatNumber(candle[2])}｜收 ${formatNumber(candle[1])}<br>` +
      `${indicators}`;
  }

  function setupControls() {
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
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && !$('#stockPickerOverlay').hidden) closeStockPicker();
    });
    window.addEventListener('popstate', () => {
      const code = new URLSearchParams(location.search).get('code');
      if (code && code !== state.currentCode) loadStock(code, { pushHistory: false });
    });
    window.addEventListener('resize', () => state.chart?.resize());
  }

  function setActiveButton(containerSelector, activeButton) {
    document.querySelectorAll(`${containerSelector} button`).forEach(button => button.classList.toggle('active', button === activeButton));
  }

  function toggleFullscreenChart() {
    const shell = $('#chartShell');
    const active = shell.classList.toggle('fullscreen');
    document.body.classList.toggle('chart-fullscreen', active);
    $('#fullscreenButton').textContent = active ? '✕' : '⛶';
    setTimeout(() => state.chart?.resize(), 80);
  }

  function renderMarkdown(markdown) {
    const lines = String(markdown || '').replace(/\r/g, '').split('\n');
    const output = [];
    let listType = null;
    const closeList = () => {
      if (listType) output.push(`</${listType}>`);
      listType = null;
    };
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

  async function start() {
    try {
      if (!window.echarts) throw new Error('ECharts 圖表元件未載入');
      const [indexPayload, manifest] = await Promise.all([
        fetchJson('./data/index.json'),
        fetchJson('./data/manifest.json')
      ]);
      state.stockIndex = Array.isArray(indexPayload.stocks) ? indexPayload.stocks : [];
      state.manifest = manifest;
      if (!state.stockIndex.length) throw new Error('個股索引為空');
      setupControls();
      renderStockList();
      const requestedCode = new URLSearchParams(location.search).get('code');
      const initialCode = state.stockIndex.some(item => item.code === requestedCode)
        ? requestedCode
        : indexPayload.defaultCode || state.stockIndex[0].code;
      await loadStock(initialCode, { pushHistory: false });
    } catch (error) {
      showError(error);
    }
  }

  document.addEventListener('DOMContentLoaded', start);
})();
