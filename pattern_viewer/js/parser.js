/**
 * PatternViewer Data Parser Module
 * Parses stock HTML reports (from reports/) or JSON objects into clean time-series arrays for ECharts.
 */

window.PatternParser = {
  /**
   * Parse HTML content from stock report (e.g. 2330_台積電(TW).html)
   * @param {string} htmlText 
   * @returns {Object} Extracted time series data
   */
  parseStockHtml(htmlText) {
    const parser = new DOMParser();
    const doc = parser.parseFromString(htmlText, 'text/html');

    // Extract title (e.g. "2330 台積電")
    const h1 = doc.querySelector('h1');
    const titleText = h1 ? h1.innerText.trim() : '股票數據';
    
    // Find the 50-day K-line table (identify by MA5 / RSI / MACD or column structure >= 11)
    const tables = Array.from(doc.querySelectorAll('table'));
    let klineTable = null;

    for (const table of tables) {
      const headers = Array.from(table.querySelectorAll('th')).map(th => th.innerText.trim());
      const headerStr = headers.join(',');
      if (headerStr.includes('MA5') || headerStr.includes('RSI') || (headers.length >= 11)) {
        klineTable = table;
        break;
      }
      if (headers.some(h => /開|高|低|收|MA/.test(h))) {
        klineTable = table;
        break;
      }
    }

    // Fallback: choose the table with the most rows (> 30 rows)
    if (!klineTable && tables.length > 0) {
      klineTable = tables.slice().sort((a, b) => b.querySelectorAll('tr').length - a.querySelectorAll('tr').length)[0];
    }

    if (!klineTable) {
      console.warn('No K-line table found in HTML report');
      return null;
    }

    const rows = Array.from(klineTable.querySelectorAll('tr')).slice(1); // skip header

    const dates = [];
    const candles = []; // [open, close, low, high] format for ECharts
    const volumes = []; // volume in thousands or Millions
    const ma5 = [];
    const rsi = [];
    const macdHist = [];
    const kList = [];
    const dList = [];
    const bollUpper = [];
    const bollMid = [];
    const bollLower = [];

    rows.forEach(row => {
      const cols = Array.from(row.querySelectorAll('td')).map(td => td.innerText.trim());
      if (cols.length < 11) return;

      // <tr><td>日期</td><td>開</td><td>高</td><td>低</td><td>收</td><td>量</td>
      //     <td>MA5</td><td>RSI</td><td>MACD_hist</td><td>K</td><td>D</td>
      //     <td>BOLL上</td><td>BOLL中</td><td>BOLL下</td></tr>
      const date = cols[0];
      const open = parseFloat(cols[1]);
      const high = parseFloat(cols[2]);
      const low = parseFloat(cols[3]);
      const close = parseFloat(cols[4]);
      
      // Parse volume like "25.1M", "4012K", "4012張", "4,012", "4012"
      let volStr = (cols[5] || '').replace(/,/g, '').trim();
      let vol = 0;
      if (volStr.endsWith('M') || volStr.endsWith('m')) {
        vol = Math.round(parseFloat(volStr.slice(0, -1)) * 1000);
      } else if (volStr.endsWith('K') || volStr.endsWith('k')) {
        vol = Math.round(parseFloat(volStr.slice(0, -1)));
      } else if (volStr.endsWith('張')) {
        vol = Math.round(parseFloat(volStr.replace('張', '')));
      } else {
        let rawNum = parseFloat(volStr);
        vol = isNaN(rawNum) ? 0 : rawNum;
      }

      const m5 = parseFloat(cols[6]);
      const r = parseFloat(cols[7]);
      const macd = parseFloat(cols[8]);
      const k = parseFloat(cols[9]);
      const d = parseFloat(cols[10]);

      const bUp = cols[11] ? parseFloat(cols[11]) : null;
      const bMid = cols[12] ? parseFloat(cols[12]) : null;
      const bLow = cols[13] ? parseFloat(cols[13]) : null;

      dates.push(date);
      candles.push([open, close, low, high]); // ECharts kline format: [open, close, lowest, highest]
      volumes.push(vol);
      ma5.push(m5);
      rsi.push(r);
      macdHist.push(macd);
      kList.push(k);
      dList.push(d);
      bollUpper.push(bUp);
      bollMid.push(bMid);
      bollLower.push(bLow);
    });

    const ma10 = [];
    const ma20 = [];
    const ma60 = [];
    const ma120 = [];

    // Calculate MA10, MA20, MA60, MA120
    for (let i = 0; i < candles.length; i++) {
      if (i >= 9) {
        let sum = 0;
        for (let j = i - 9; j <= i; j++) sum += candles[j][1];
        ma10.push(parseFloat((sum / 10).toFixed(2)));
      } else {
        ma10.push(null);
      }

      if (i >= 19) {
        let sum = 0;
        for (let j = i - 19; j <= i; j++) sum += candles[j][1];
        ma20.push(parseFloat((sum / 20).toFixed(2)));
      } else {
        ma20.push(null);
      }

      if (i >= 59) {
        let sum = 0;
        for (let j = i - 59; j <= i; j++) sum += candles[j][1];
        ma60.push(parseFloat((sum / 60).toFixed(2)));
      } else {
        ma60.push(null);
      }

      if (i >= 119) {
        let sum = 0;
        for (let j = i - 119; j <= i; j++) sum += candles[j][1];
        ma120.push(parseFloat((sum / 120).toFixed(2)));
      } else {
        ma120.push(null);
      }
    }

    // Calculate Dual RSI: RSI(6) and RSI(12)
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

    const closePrices = candles.map(c => c[1]);
    const rsi6 = calculateWilderRsi(closePrices, 6);
    const rsi12 = calculateWilderRsi(closePrices, 12);

    // Calculate Volume Moving Averages: VMA5 and VMA20
    const vma5 = [];
    const vma20 = [];
    for (let i = 0; i < volumes.length; i++) {
      if (i >= 4) {
        let sum = 0;
        for (let j = i - 4; j <= i; j++) sum += (volumes[j] || 0);
        vma5.push(parseFloat((sum / 5).toFixed(1)));
      } else {
        vma5.push(null);
      }

      if (i >= 19) {
        let sum = 0;
        for (let j = i - 19; j <= i; j++) sum += (volumes[j] || 0);
        vma20.push(parseFloat((sum / 20).toFixed(1)));
      } else {
        vma20.push(null);
      }
    }

    // Calculate MACD: DIF (EMA12 - EMA26), MACD Signal (EMA9 of DIF), OSC Hist ((DIF - Signal) * 2)
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
      if (!prices || prices.length === 0) return { dif: [], macdSignal: [], macdHist: [] };
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

    const { dif, macdSignal, calculatedHist } = calculateMacd(closePrices);

    return {
      title: titleText,
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
      kList,
      dList,
      bollUpper,
      bollMid,
      bollLower
    };
  }
};
