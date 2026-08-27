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
      
      // Parse volume like "25.1M" or "5000"
      let volStr = cols[5];
      let vol = parseFloat(volStr);
      if (volStr.endsWith('M')) {
        vol = parseFloat(volStr.replace('M', '')) * 1000;
      } else if (volStr.endsWith('K')) {
        vol = parseFloat(volStr.replace('K', ''));
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

    // Calculate MA10 and MA20
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
    }

    return {
      title: titleText,
      dates,
      candles,
      volumes,
      ma5,
      ma10,
      ma20,
      rsi,
      macdHist,
      kList,
      dList,
      bollUpper,
      bollMid,
      bollLower
    };
  }
};
