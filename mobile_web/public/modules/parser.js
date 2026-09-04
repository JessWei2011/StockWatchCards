/** Parse the generated stock report into ECharts-ready time-series data. */
window.PatternParser = {
  parseStockHtml(htmlText) {
    const doc = new DOMParser().parseFromString(htmlText, 'text/html');
    const tables = Array.from(doc.querySelectorAll('table'));

    // 1. Locate K-line table
    let klineTable = null;
    for (const table of tables) {
      const headers = Array.from(table.querySelectorAll('th')).map(th => th.textContent.trim());
      const headerStr = headers.join(',');
      if (['日期', '開', '高', '低', '收'].every(label => headerStr.includes(label))) {
        klineTable = table;
        break;
      }
    }
    if (!klineTable && tables.length > 0) {
      klineTable = tables.slice().sort((a, b) => b.querySelectorAll('tr').length - a.querySelectorAll('tr').length)[0];
    }
    if (!klineTable) throw new Error('報告內找不到 K 線資料表');

    const number = value => {
      if (value == null) return null;
      const cleaned = String(value).trim().replace(/,/g, '');
      if (cleaned === '' || cleaned === '—' || cleaned === '-') return null;
      if (cleaned.endsWith('M') || cleaned.endsWith('m')) {
        return Math.round(parseFloat(cleaned.slice(0, -1)) * 1000);
      }
      if (cleaned.endsWith('K') || cleaned.endsWith('k')) {
        return Math.round(parseFloat(cleaned.slice(0, -1)));
      }
      if (cleaned.endsWith('張')) {
        return Math.round(parseFloat(cleaned.slice(0, -1)));
      }
      const parsed = parseFloat(cleaned);
      return Number.isFinite(parsed) ? parsed : null;
    };

    const dates = [];
    const candles = [];
    const volumes = [];
    const ma5 = [];
    const rsi = [];
    const macdHist = [];
    const kList = [];
    const dList = [];
    const bollUpper = [];
    const bollMid = [];
    const bollLower = [];

    Array.from(klineTable.querySelectorAll('tr')).slice(1).forEach(row => {
      const cells = Array.from(row.querySelectorAll('td')).map(td => td.textContent.trim());
      if (cells.length < 11 || !/^\d{1,4}[-/]\d{1,2}/.test(cells[0])) return;
      const open = number(cells[1]);
      const high = number(cells[2]);
      const low = number(cells[3]);
      const close = number(cells[4]);
      if ([open, high, low, close].some(value => value === null)) return;

      dates.push(cells[0]);
      candles.push([open, close, low, high]);
      volumes.push(number(cells[5]) || 0);
      ma5.push(number(cells[6]));
      rsi.push(number(cells[7]));
      macdHist.push(number(cells[8]));
      kList.push(number(cells[9]));
      dList.push(number(cells[10]));
      bollUpper.push(cells[11] ? number(cells[11]) : null);
      bollMid.push(cells[12] ? number(cells[12]) : null);
      bollLower.push(cells[13] ? number(cells[13]) : null);
    });

    if (dates.length < 20) throw new Error('可用 K 線資料不足 20 筆');

    const closes = candles.map(candle => candle[1]);
    const ma10 = movingAverage(closes, 10);
    const ma20 = movingAverage(closes, 20);
    const ma60 = movingAverage(closes, 60);
    const ma120 = movingAverage(closes, 120);
    const vma5 = movingAverage(volumes, 5);
    const vma20 = movingAverage(volumes, 20);
    const rsi6 = calculateWilderRsi(closes, 6);
    const rsi12 = calculateWilderRsi(closes, 12);
    const { dif, macdSignal, calculatedHist } = calculateMacd(closes);

    // Build row objects for app.js (renderChart, renderIndicatorGrid, zoomStart)
    const rows = dates.map((date, i) => ({
      date,
      open: candles[i][0],
      close: candles[i][1],
      low: candles[i][2],
      high: candles[i][3],
      volume: volumes[i],
      ma5: ma5[i] ?? null,
      ma10: ma10[i] ?? null,
      ma20: ma20[i] ?? null,
      ma60: ma60[i] ?? null,
      ma120: ma120[i] ?? null,
      rsi6: (rsi[i] != null && !isNaN(rsi[i])) ? rsi[i] : (rsi6[i] ?? null),
      rsi12: rsi12[i] ?? null,
      dif: dif[i] ?? null,
      macd: macdSignal[i] ?? null,
      osc: (macdHist[i] != null && !isNaN(macdHist[i])) ? macdHist[i] : (calculatedHist[i] ?? null),
      k: kList[i] ?? null,
      d: dList[i] ?? null,
      bollUpper: bollUpper[i] ?? null,
      bollMid: bollMid[i] ?? null,
      bollLower: bollLower[i] ?? null
    }));

    // 2. Parse 三大法人 table
    const institutions = parseInstitutions(tables);

    return {
      dates,
      candles,
      volumes,
      vma5,
      vma20,
      ma5,
      ma10,
      ma20,
      ma60,
      ma120,
      rsi: (rsi && rsi.some(v => v !== null && !isNaN(v))) ? rsi : rsi6,
      rsi6,
      rsi12,
      dif,
      macdSignal,
      macdHist: (macdHist && macdHist.some(v => v !== null && !isNaN(v))) ? macdHist : calculatedHist,
      k: kList,
      d: dList,
      bollUpper,
      bollMid,
      bollLower,
      rows,
      institutions,
      institutionLatest: institutions.length ? institutions[institutions.length - 1] : null
    };
  }
};

function parseInstitutions(tables) {
  const table = tables.find(candidate => {
    const headers = Array.from(candidate.querySelectorAll('th')).map(th => th.textContent.trim());
    return headers.some(h => h.includes('外資')) && headers.some(h => h.includes('投信'));
  });
  if (!table) return [];

  const signedNumber = value => {
    const cleaned = String(value || '').replace(/,/g, '').trim();
    const match = cleaned.match(/[+-]?\d+/);
    if (!match) return 0;
    const parsed = parseInt(match[0], 10);
    return Number.isFinite(parsed) ? parsed : 0;
  };

  const rows = Array.from(table.querySelectorAll('tr')).slice(1);
  const items = [];
  rows.forEach(tr => {
    const cells = Array.from(tr.querySelectorAll('td')).map(td => td.textContent.trim());
    if (cells.length < 5) return;
    const date = cells[0];
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) && !/^\d{2}\/\d{2}$/.test(date)) return;
    items.push({
      date,
      foreign: signedNumber(cells[1]),
      trust: signedNumber(cells[2]),
      dealer: signedNumber(cells[3]),
      total: signedNumber(cells[4])
    });
  });

  // Reverse so oldest is index 0 and latest day is at the end (items[items.length - 1])
  items.reverse();
  return items;
}

function movingAverage(values, period) {
  return values.map((_, index) => {
    if (index < period - 1) return null;
    const windowValues = values.slice(index - period + 1, index + 1);
    return Number((windowValues.reduce((sum, value) => sum + Number(value || 0), 0) / period).toFixed(2));
  });
}

function calculateWilderRsi(closePrices, period) {
  const result = [];
  let avgGain = 0;
  let avgLoss = 0;
  for (let i = 0; i < closePrices.length; i++) {
    if (i === 0) {
      result.push(null);
      continue;
    }
    const change = closePrices[i] - closePrices[i - 1];
    const gain = change > 0 ? change : 0;
    const loss = change < 0 ? -change : 0;

    if (i <= period) {
      avgGain += gain;
      avgLoss += loss;
      if (i === period) {
        avgGain /= period;
        avgLoss /= period;
        const rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
        const val = 100 - (100 / (1 + rs));
        result.push(parseFloat(val.toFixed(2)));
      } else {
        result.push(null);
      }
    } else {
      avgGain = (avgGain * (period - 1) + gain) / period;
      avgLoss = (avgLoss * (period - 1) + loss) / period;
      const rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
      const val = 100 - (100 / (1 + rs));
      result.push(parseFloat(val.toFixed(2)));
    }
  }
  return result;
}

function calculateEma(values, period) {
  if (!values || !values.length) return [];
  const k = 2 / (period + 1);
  const emaArray = [];
  let prevEma = values[0];
  emaArray.push(parseFloat(prevEma.toFixed(2)));
  for (let i = 1; i < values.length; i++) {
    const curEma = values[i] * k + prevEma * (1 - k);
    emaArray.push(parseFloat(curEma.toFixed(2)));
    prevEma = curEma;
  }
  return emaArray;
}

function calculateMacd(prices) {
  if (!prices || prices.length === 0) return { dif: [], macdSignal: [], calculatedHist: [] };
  const ema12 = calculateEma(prices, 12);
  const ema26 = calculateEma(prices, 26);
  const dif = [];
  for (let i = 0; i < prices.length; i++) {
    dif.push(parseFloat((ema12[i] - ema26[i]).toFixed(2)));
  }
  const macdSignal = calculateEma(dif, 9);
  const calculatedHist = [];
  for (let i = 0; i < prices.length; i++) {
    calculatedHist.push(parseFloat(((dif[i] - macdSignal[i]) * 2).toFixed(2)));
  }
  return { dif, macdSignal, calculatedHist };
}
