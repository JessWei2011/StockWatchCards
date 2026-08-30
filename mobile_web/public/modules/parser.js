/** Parse the generated stock report into ECharts-ready time-series data. */
window.PatternParser = {
  parseStockHtml(htmlText) {
    const doc = new DOMParser().parseFromString(htmlText, 'text/html');
    const tables = Array.from(doc.querySelectorAll('table'));
    const table = tables.find(candidate => {
      const headers = Array.from(candidate.querySelectorAll('th')).map(th => th.textContent.trim());
      return ['日期', '開', '高', '低', '收', '量', 'MA5', 'RSI', 'MACD'].every(label =>
        headers.some(header => header.includes(label))
      );
    });
    if (!table) throw new Error('報告內找不到 K 線資料表');

    const data = {
      dates: [], candles: [], volumes: [], ma5: [], rsi: [], macdHist: [],
      k: [], d: [], bollUpper: [], bollMid: [], bollLower: []
    };

    Array.from(table.querySelectorAll('tr')).slice(1).forEach(row => {
      const cells = Array.from(row.querySelectorAll('td')).map(td => td.textContent.trim());
      if (cells.length < 14 || !/^\d{2}\/\d{2}$/.test(cells[0])) return;
      const number = value => {
        const parsed = Number.parseFloat(String(value ?? '').replace(/,/g, '').replace(/張$/, ''));
        return Number.isFinite(parsed) ? parsed : null;
      };
      const open = number(cells[1]);
      const high = number(cells[2]);
      const low = number(cells[3]);
      const close = number(cells[4]);
      if ([open, high, low, close].some(value => value === null)) return;

      data.dates.push(cells[0]);
      data.candles.push([open, close, low, high]);
      data.volumes.push(number(cells[5]) || 0);
      data.ma5.push(number(cells[6]));
      data.rsi.push(number(cells[7]));
      data.macdHist.push(number(cells[8]));
      data.k.push(number(cells[9]));
      data.d.push(number(cells[10]));
      data.bollUpper.push(number(cells[11]));
      data.bollMid.push(number(cells[12]));
      data.bollLower.push(number(cells[13]));
    });

    if (data.dates.length < 20) throw new Error('可用 K 線資料不足 20 筆');
    const closes = data.candles.map(candle => candle[1]);
    data.ma10 = movingAverage(closes, 10);
    data.ma20 = movingAverage(closes, 20);
    data.ma60 = movingAverage(closes, 60);
    data.ma120 = movingAverage(closes, 120);
    data.rsi6 = calculateRsi(closes, 6);
    data.rsi12 = calculateRsi(closes, 12);
    data.vma5 = movingAverage(data.volumes, 5);
    data.vma20 = movingAverage(data.volumes, 20);
    const macd = calculateMacd(closes);
    data.dif = macd.dif;
    data.macdSignal = macd.signal;
    data.institutionLatest = parseLatestInstitution(tables);
    return data;
  }
};

function parseLatestInstitution(tables) {
  const table = tables.find(candidate => {
    const headers = Array.from(candidate.querySelectorAll('th')).map(th => th.textContent.trim());
    return ['日期', '外資', '投信', '合計'].every(label => headers.some(header => header.includes(label))) &&
      headers.some(header => header.includes('自營'));
  });
  if (!table) return null;

  const row = Array.from(table.querySelectorAll('tr')).slice(1).find(candidate => {
    const firstCell = candidate.querySelector('td');
    return firstCell && /^\d{4}-\d{2}-\d{2}$/.test(firstCell.textContent.trim());
  });
  if (!row) return null;

  const cells = Array.from(row.querySelectorAll('td')).map(td => td.textContent.trim());
  const signedNumber = value => {
    const parsed = Number.parseInt(String(value || '').replace(/[^\d+-]/g, ''), 10);
    return Number.isFinite(parsed) ? parsed : null;
  };
  return {
    date: cells[0],
    foreign: signedNumber(cells[1]),
    trust: signedNumber(cells[2]),
    dealer: signedNumber(cells[3]),
    total: signedNumber(cells[4])
  };
}

function movingAverage(values, period) {
  return values.map((_, index) => {
    if (index < period - 1) return null;
    const windowValues = values.slice(index - period + 1, index + 1);
    return Number((windowValues.reduce((sum, value) => sum + Number(value || 0), 0) / period).toFixed(2));
  });
}

function calculateRsi(values, period) {
  let avgGain = 0;
  let avgLoss = 0;
  return values.map((value, index) => {
    if (index === 0) return null;
    const change = value - values[index - 1];
    const gain = Math.max(change, 0);
    const loss = Math.max(-change, 0);
    if (index <= period) {
      avgGain += gain;
      avgLoss += loss;
      if (index < period) return null;
      avgGain /= period;
      avgLoss /= period;
    } else {
      avgGain = (avgGain * (period - 1) + gain) / period;
      avgLoss = (avgLoss * (period - 1) + loss) / period;
    }
    if (avgLoss === 0) return 100;
    return Number((100 - 100 / (1 + avgGain / avgLoss)).toFixed(2));
  });
}

function calculateMacd(values) {
  const ema = period => {
    const weight = 2 / (period + 1);
    const output = [];
    let previous = values[0];
    values.forEach((value, index) => {
      previous = index === 0 ? value : value * weight + previous * (1 - weight);
      output.push(Number(previous.toFixed(3)));
    });
    return output;
  };
  const ema12 = ema(12);
  const ema26 = ema(26);
  const dif = ema12.map((value, index) => Number((value - ema26[index]).toFixed(3)));
  const weight = 2 / 10;
  let previous = dif[0];
  const signal = dif.map((value, index) => {
    previous = index === 0 ? value : value * weight + previous * (1 - weight);
    return Number(previous.toFixed(3));
  });
  return { dif, signal };
}
