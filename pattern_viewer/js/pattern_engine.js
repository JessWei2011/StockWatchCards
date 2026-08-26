/**
 * PatternEngine Module
 * Given the pattern text a stock's real AI card already concluded (data.js STOCK_CARDS[].pattern),
 * find the geometric evidence for THAT specific pattern in the candle data and build a chart overlay.
 * This does NOT independently guess/scan for every known pattern shape — the card's own conclusion
 * drives which visualization gets built. Unmatched pattern text falls back to a generic trend overlay.
 */

window.PatternEngine = {
  /**
   * Coarse swing-point detector (zigzag): only records a reversal once price has moved at least
   * thresholdPct from the last extreme, so minor daily noise doesn't create spurious swing points.
   */
  zigzag(stockData, thresholdPct = 0.03) {
    const { candles, dates } = stockData;
    const points = [];
    if (!candles || candles.length < 3) return points;

    let dir = 0; // 0 = undecided, 1 = tracking up-swing, -1 = tracking down-swing
    let curHigh = candles[0][3], curHighIdx = 0;
    let curLow = candles[0][2], curLowIdx = 0;

    for (let i = 1; i < candles.length; i++) {
      const hi = candles[i][3], lo = candles[i][2];

      if (dir <= 0) {
        if (lo < curLow) { curLow = lo; curLowIdx = i; }
        if (hi >= curLow * (1 + thresholdPct)) {
          points.push({ idx: curLowIdx, date: dates[curLowIdx], price: curLow, type: 'low' });
          dir = 1;
          curHigh = hi; curHighIdx = i;
        }
      }
      if (dir >= 0) {
        if (hi > curHigh) { curHigh = hi; curHighIdx = i; }
        if (lo <= curHigh * (1 - thresholdPct)) {
          points.push({ idx: curHighIdx, date: dates[curHighIdx], price: curHigh, type: 'high' });
          dir = -1;
          curLow = lo; curLowIdx = i;
        }
      }
    }

    if (dir === 1) points.push({ idx: curHighIdx, date: dates[curHighIdx], price: curHigh, type: 'high' });
    else if (dir <= 0) points.push({ idx: curLowIdx, date: dates[curLowIdx], price: curLow, type: 'low' });

    return points;
  },

  lastPoint(stockData) {
    const idx = stockData.candles.length - 1;
    return { idx, date: stockData.dates[idx], price: stockData.candles[idx][1] };
  },

  nearestMaLabel(stockData, idx) {
    const close = stockData.candles[idx][1];
    const candidates = [
      { key: 'ma5', label: '5日線(MA5)' },
      { key: 'ma10', label: '10日線(MA10)' },
      { key: 'ma20', label: '月線(MA20)' },
      { key: 'bollMid', label: '布林中軌' }
    ];
    let best = null;
    candidates.forEach(c => {
      const arr = stockData[c.key];
      const v = arr && arr[idx];
      if (v == null || isNaN(v)) return;
      const diff = Math.abs(close - v) / close;
      if (!best || diff < best.diff) best = { ...c, value: v, diff };
    });
    return best;
  },

  resolveMaFromText(text, stockData) {
    const map = [
      { re: /月線|生命線/, key: 'ma20', label: '月線(MA20)' },
      { re: /季線|60日線/, key: 'ma60', label: '季線(MA60)' },
      { re: /10日線/, key: 'ma10', label: '10日線(MA10)' },
      { re: /5日線/, key: 'ma5', label: '5日線(MA5)' },
      { re: /布林上軌|上軌/, key: 'bollUpper', label: '布林上軌' },
      { re: /布林中軌|中軌/, key: 'bollMid', label: '布林中軌' }
    ];
    for (const m of map) {
      if (m.re.test(text) && stockData[m.key]) return m;
    }
    return { key: 'ma20', label: '月線(MA20)' };
  },

  // Minimal placeholder overlay when there isn't enough swing data to visualize anything specific.
  buildFlatFallback(stockData) {
    const cur = this.lastPoint(stockData);
    return {
      name: '資料不足以判讀型態',
      badge: '資料不足',
      color: '#64748b',
      pivots: [{ date: cur.date, price: cur.price, label: '目前收盤', tag: '現在' }],
      vectorPath: [],
      explanation: '近期價格波動幅度過小或資料天數不足，無法可靠地畫出型態轉折點，僅顯示目前收盤位置。'
    };
  },

  buildLadderUp(stockData) {
    const swings = this.zigzag(stockData, 0.03).slice(-5);
    if (swings.length < 2) return this.buildFlatFallback(stockData);
    const cur = this.lastPoint(stockData);
    const pivots = swings.map((p, i) => ({
      date: p.date, price: p.price,
      label: p.type === 'low' ? '低點墊高' : '高點墊高',
      tag: `P${i + 1}`
    }));
    pivots.push({ date: cur.date, price: cur.price, label: '目前收盤', tag: '現在' });
    return {
      name: '頭頭高／底底高（上升階梯結構）',
      badge: '上升趨勢結構',
      color: '#ef4444', // 台股：上漲紅
      pivots,
      vectorPath: pivots.map(p => [p.date, p.price]),
      explanation: `【底底高教學心法】：依近期高低點回推 ${swings.map(p => `${p.date} ${p.type === 'low' ? '低點' : '高點'} $${p.price}`).join('、')}，每一段的低點都比前一段高，屬於連續墊高的階梯上升結構；只要低點不被跌破，多方架構就沒有被破壞。`
    };
  },

  buildLadderDown(stockData) {
    const swings = this.zigzag(stockData, 0.03).slice(-5);
    if (swings.length < 2) return this.buildFlatFallback(stockData);
    const cur = this.lastPoint(stockData);
    const pivots = swings.map((p, i) => ({
      date: p.date, price: p.price,
      label: p.type === 'high' ? '高點下滑' : '低點破底',
      tag: `P${i + 1}`
    }));
    pivots.push({ date: cur.date, price: cur.price, label: '目前收盤', tag: '現在' });
    return {
      name: '頭頭低／底底低（下降階梯結構）',
      badge: '下降趨勢結構',
      color: '#10b981', // 台股：下跌綠
      pivots,
      vectorPath: pivots.map(p => [p.date, p.price]),
      explanation: `【頭頭低教學心法】：依近期高低點回推 ${swings.map(p => `${p.date} ${p.type === 'high' ? '高點' : '低點'} $${p.price}`).join('、')}，每一段的高點都比前一段低，屬於連續破底的階梯下降結構，反彈只要碰到前一個高點就再度受阻。`
    };
  },

  buildWBottom(stockData) {
    const swings = this.zigzag(stockData, 0.03);
    const valleys = swings.filter(p => p.type === 'low');
    if (valleys.length < 1) return this.buildFlatFallback(stockData);
    const p3 = valleys[valleys.length - 1];
    const p1 = valleys.length >= 2 ? valleys[valleys.length - 2] : p3;
    const p2 = swings.filter(p => p.type === 'high' && p.idx > p1.idx && p.idx < p3.idx)
      .sort((a, b) => b.price - a.price)[0];
    const cur = this.lastPoint(stockData);
    const broke = p2 ? cur.price > p2.price : null;

    const pivots = [{ ...p1, label: 'P1 左腳低點', tag: 'W1' }];
    if (p2) pivots.push({ ...p2, label: 'P2 頸線高點', tag: '頸線' });
    if (p3.idx !== p1.idx) pivots.push({ ...p3, label: 'P3 右腳打底', tag: 'W2' });
    pivots.push({ date: cur.date, price: cur.price, label: broke ? 'P4 衝破頸線' : 'P4 尚在整理', tag: broke ? '突破' : '整理中' });

    return {
      name: 'W底（雙重底）',
      badge: broke ? '底部型態已確立' : '底部型態醞釀中',
      color: '#ef4444', // 台股：偏多紅
      pivots,
      vectorPath: pivots.map(p => [p.date, p.price]),
      resistanceLine: p2 ? { price: p2.price, label: 'P2 雙底頸線壓力' } : undefined,
      explanation: `【W底教學心法】：第一次打底於 ${p1.date}（$${p1.price}）${p2 ? `，拉回測試 P2 頸線 $${p2.price} 後` : ''}二次測試 ${p3.date}（$${p3.price}），${broke ? '目前已收復頸線，底型確立。' : '目前仍在頸線附近整理，尚未確認突破。'}`
    };
  },

  buildMHead(stockData) {
    const swings = this.zigzag(stockData, 0.03);
    const peaks = swings.filter(p => p.type === 'high');
    if (peaks.length < 1) return this.buildFlatFallback(stockData);
    const p3 = peaks[peaks.length - 1];
    const p1 = peaks.length >= 2 ? peaks[peaks.length - 2] : p3;
    const p2 = swings.filter(p => p.type === 'low' && p.idx > p1.idx && p.idx < p3.idx)
      .sort((a, b) => a.price - b.price)[0];
    const cur = this.lastPoint(stockData);
    const broke = p2 ? cur.price < p2.price : null;

    const pivots = [{ ...p1, label: 'P1 左頭高點', tag: 'M1' }];
    if (p2) pivots.push({ ...p2, label: 'P2 頸線支撐', tag: '頸線' });
    if (p3.idx !== p1.idx) pivots.push({ ...p3, label: 'P3 右頭派發', tag: 'M2' });
    pivots.push({ date: cur.date, price: cur.price, label: broke ? 'P4 跌破頸線' : 'P4 尚在整理', tag: broke ? '跌破' : '整理中' });

    return {
      name: 'M頭（雙重頂）',
      badge: broke ? '空頭型態已確立' : '高檔警訊型態',
      color: '#10b981', // 台股：偏空綠
      pivots,
      vectorPath: pivots.map(p => [p.date, p.price]),
      resistanceLine: p2 ? { price: p2.price, label: 'P2 頸線防守線' } : undefined,
      explanation: `【M頭教學心法】：高檔第一次受阻於 ${p1.date}（$${p1.price}）${p2 ? `，拉回測試 P2 頸線 $${p2.price} 後` : ''}再度受阻於 ${p3.date}（$${p3.price}），${broke ? '目前已跌破頸線，空頭型態確立。' : '目前仍在頸線附近整理，尚未確認跌破。'}`
    };
  },

  buildNShape(stockData) {
    const swings = this.zigzag(stockData, 0.03);
    const valleys = swings.filter(p => p.type === 'low');
    if (valleys.length < 1) return this.buildFlatFallback(stockData);
    const v1 = valleys.length >= 2 ? valleys[valleys.length - 2] : valleys[valleys.length - 1];
    const pk = swings.find(p => p.type === 'high' && p.idx > v1.idx);
    const v2 = pk ? valleys.find(v => v.idx > pk.idx) : null;
    const cur = this.lastPoint(stockData);
    const broke = pk ? cur.price > pk.price : null;

    const pivots = [{ ...v1, label: 'P1 起漲低點', tag: 'P1' }];
    if (pk) pivots.push({ ...pk, label: 'P2 衝高壓力', tag: 'P2' });
    if (v2) pivots.push({ ...v2, label: 'P3 洗盤支撐', tag: 'P3' });
    pivots.push({ date: cur.date, price: cur.price, label: broke ? 'P4 帶量突破' : 'P4 尚未突破', tag: broke ? '突破' : '整理中' });

    return {
      name: 'N字強勢反攻',
      badge: broke ? '多頭續攻型態' : '型態醞釀中',
      color: '#f59e0b',
      pivots,
      vectorPath: pivots.map(p => [p.date, p.price]),
      resistanceLine: pk ? { price: pk.price, label: 'P2 關鍵壓力轉支撐線' } : undefined,
      explanation: `【N字教學心法】：${v1.date} 起漲低點 $${v1.price}${pk ? `，衝高至 ${pk.date} 遇壓 $${pk.price}` : ''}${v2 ? `，拉回 ${v2.date} 洗盤守穩 $${v2.price}` : ''}，${broke ? '目前已收復前高，發動二階段強攻。' : '目前尚未收復前高，型態尚未確認。'}`
    };
  },

  buildBox(stockData) {
    const total = stockData.dates.length;
    const boxStart = Math.max(0, total - 20);
    let boxMax = -Infinity, boxMaxIdx = boxStart, boxMin = Infinity, boxMinIdx = boxStart;
    for (let i = boxStart; i < total; i++) {
      const hi = stockData.candles[i][3], lo = stockData.candles[i][2];
      if (hi > boxMax) { boxMax = hi; boxMaxIdx = i; }
      if (lo < boxMin) { boxMin = lo; boxMinIdx = i; }
    }
    const cur = this.lastPoint(stockData);
    const state = cur.price > boxMax ? 'up' : (cur.price < boxMin ? 'down' : 'inside');

    const pivots = [
      { date: stockData.dates[boxMinIdx], price: boxMin, label: '箱型整理下緣', tag: '箱底' },
      { date: stockData.dates[boxMaxIdx], price: boxMax, label: '箱型整理上緣', tag: '箱頂' },
      { date: cur.date, price: cur.price, label: state === 'up' ? '帶量向上突破箱頂' : (state === 'down' ? '跌破箱底' : '箱內震盪'), tag: state === 'up' ? '突破' : (state === 'down' ? '跌破' : '整理中') }
    ];

    return {
      name: '箱型整理',
      badge: state === 'up' ? '籌碼沉澱後攻擊' : (state === 'down' ? '箱型跌破' : '箱型整理中'),
      color: '#8b5cf6',
      pivots,
      vectorPath: [
        [stockData.dates[boxStart], boxMin], [stockData.dates[Math.max(boxStart, total - 4)], boxMin],
        [stockData.dates[Math.max(boxStart, total - 4)], boxMax], [stockData.dates[boxStart], boxMax], [stockData.dates[boxStart], boxMin]
      ],
      resistanceLine: { price: boxMax, label: '箱型上緣關鍵壓力線' },
      explanation: `【箱型整理教學心法】：近 20 個交易日區間介於 $${boxMin.toFixed(1)}（箱底）~ $${boxMax.toFixed(1)}（箱頂）之間，${state === 'up' ? '最新收盤已強勢突破箱頂，代表賣壓消化完畢。' : (state === 'down' ? '最新收盤已跌破箱底，區間下緣支撐失守。' : '目前仍在區間內震盪，尚未方向選擇。')}`
    };
  },

  buildTriangle(stockData) {
    const swings = this.zigzag(stockData, 0.02);
    const highs = swings.filter(p => p.type === 'high').slice(-3);
    const lows = swings.filter(p => p.type === 'low').slice(-3);
    if (highs.length < 2 || lows.length < 2) return this.buildFlatFallback(stockData);

    const upperFrom = highs[0], upperTo = highs[highs.length - 1];
    const lowerFrom = lows[0], lowerTo = lows[lows.length - 1];
    const converging = upperTo.price < upperFrom.price && lowerTo.price > lowerFrom.price;
    const cur = this.lastPoint(stockData);

    return {
      name: '三角收斂',
      badge: converging ? '收斂三角整理' : '波動區間變化中',
      color: '#38bdf8',
      pivots: [
        { ...upperFrom, label: '壓力高點(較早)', tag: '上緣1' },
        { ...upperTo, label: '壓力高點(較新)', tag: '上緣2' },
        { ...lowerFrom, label: '支撐低點(較早)', tag: '下緣1' },
        { ...lowerTo, label: '支撐低點(較新)', tag: '下緣2' },
        { date: cur.date, price: cur.price, label: '目前收盤', tag: '現在' }
      ],
      vectorPath: [],
      boundaryLines: [
        { points: [[upperFrom.date, upperFrom.price], [upperTo.date, upperTo.price]], dashed: true, label: '上緣壓力線' },
        { points: [[lowerFrom.date, lowerFrom.price], [lowerTo.date, lowerTo.price]], dashed: true, label: '下緣支撐線' }
      ],
      explanation: `【三角收斂教學心法】：高點從 ${upperFrom.date} 的 $${upperFrom.price} 到 ${upperTo.date} 的 $${upperTo.price}${converging ? '逐步遞減' : '尚未明顯遞減'}，低點從 ${lowerFrom.date} 的 $${lowerFrom.price} 到 ${lowerTo.date} 的 $${lowerTo.price}${converging ? '逐步墊高' : '尚未明顯墊高'}，${converging ? '上下邊界正在收斂，變盤點通常出現在收斂到頂點附近。' : '目前收斂特徵還不夠明顯，僅供參考。'}`
    };
  },

  buildHeadShoulders(stockData, text) {
    const isBottom = /頭肩底/.test(text);
    const swings = this.zigzag(stockData, 0.03);
    const shoulderSrc = isBottom ? swings.filter(p => p.type === 'low') : swings.filter(p => p.type === 'high');
    if (shoulderSrc.length < 3) return this.buildFlatFallback(stockData);

    const [leftShoulder, head, rightShoulder] = shoulderSrc.slice(-3);
    const necklineSrc = isBottom ? swings.filter(p => p.type === 'high') : swings.filter(p => p.type === 'low');
    const neckPts = necklineSrc.filter(p => p.idx > leftShoulder.idx && p.idx < rightShoulder.idx);
    const necklineAvg = neckPts.length
      ? neckPts.reduce((s, p) => s + p.price, 0) / neckPts.length
      : (leftShoulder.price + rightShoulder.price) / 2;
    const cur = this.lastPoint(stockData);
    const broke = isBottom ? cur.price > necklineAvg : cur.price < necklineAvg;

    const pivots = [
      { ...leftShoulder, label: isBottom ? '左肩' : '左肩', tag: 'L' },
      { ...head, label: isBottom ? '頭部(最低)' : '頭部(最高)', tag: '頭' },
      { ...rightShoulder, label: '右肩', tag: 'R' },
      { date: cur.date, price: cur.price, label: broke ? (isBottom ? '突破頸線' : '跌破頸線') : '尚未突破頸線', tag: broke ? '確立' : '整理中' }
    ];

    const necklinePts = neckPts.length >= 2
      ? [neckPts[0], neckPts[neckPts.length - 1]]
      : [{ date: leftShoulder.date, price: necklineAvg }, { date: rightShoulder.date, price: necklineAvg }];

    return {
      name: isBottom ? '頭肩底' : '頭肩頂',
      badge: broke ? '型態已確立' : '型態醞釀中（兩肩對稱度僅供參考）',
      color: isBottom ? '#ef4444' : '#10b981',
      pivots,
      vectorPath: [[leftShoulder.date, leftShoulder.price], [head.date, head.price], [rightShoulder.date, rightShoulder.price]],
      boundaryLines: [
        { points: [[necklinePts[0].date, necklinePts[0].price], [necklinePts[1].date, necklinePts[1].price]], dashed: true, label: '頸線' }
      ],
      explanation: `【${isBottom ? '頭肩底' : '頭肩頂'}教學心法】：左肩 ${leftShoulder.date}（$${leftShoulder.price}）、頭部 ${head.date}（$${head.price}）、右肩 ${rightShoulder.date}（$${rightShoulder.price}），頸線約在 $${necklineAvg.toFixed(1)}，${broke ? `目前已${isBottom ? '突破' : '跌破'}頸線，型態確立。` : '目前尚未確認突破頸線，兩肩高度是否對稱請自行核對圖形。'}`
    };
  },

  buildVReversal(stockData) {
    const swings = this.zigzag(stockData, 0.03);
    const lows = swings.filter(p => p.type === 'low');
    if (lows.length < 1) return this.buildFlatFallback(stockData);
    const bottom = lows[lows.length - 1];
    const cur = this.lastPoint(stockData);

    let crossPoint = null;
    if (stockData.ma20) {
      for (let i = bottom.idx + 1; i < stockData.candles.length; i++) {
        const c = stockData.candles[i][1], ma = stockData.ma20[i];
        if (ma != null && c > ma) { crossPoint = { date: stockData.dates[i], price: c }; break; }
      }
    }

    const pivots = [{ ...bottom, label: '波段最低點(破底)', tag: '破底' }];
    if (crossPoint) pivots.push({ ...crossPoint, label: '站上月線(MA20)', tag: '攻克月線' });
    pivots.push({ date: cur.date, price: cur.price, label: '目前收盤', tag: '現在' });

    return {
      name: '破底翻（V型反轉）',
      badge: crossPoint ? '打底反彈已攻克月線' : '打底反彈進行中',
      color: '#ef4444', // 台股：反轉向上紅
      pivots,
      vectorPath: pivots.map(p => [p.date, p.price]),
      explanation: `【破底翻教學心法】：${bottom.date} 出現波段最低點 $${bottom.price}，${crossPoint ? `${crossPoint.date} 帶量收復月線(MA20) $${crossPoint.price.toFixed(1)}，反彈格局確立。` : '目前反彈力道仍在驗證中，尚未站穩月線(MA20)。'}`
    };
  },

  buildBreakoutSpike(stockData) {
    const total = stockData.dates.length;
    const lookStart = Math.max(0, total - 10);
    let bestIdx = lookStart, bestChangePct = -Infinity;
    for (let i = lookStart; i < total; i++) {
      const o = stockData.candles[i][0], c = stockData.candles[i][1];
      const chg = o ? (c - o) / o : 0;
      if (chg > bestChangePct) { bestChangePct = chg; bestIdx = i; }
    }
    const cur = this.lastPoint(stockData);
    const spike = { date: stockData.dates[bestIdx], price: stockData.candles[bestIdx][1] };

    return {
      name: '強勢噴發',
      badge: '單日噴出攻擊訊號',
      color: '#f59e0b',
      pivots: [
        { ...spike, label: `噴出日 (單日漲幅${(bestChangePct * 100).toFixed(1)}%)`, tag: '噴發' },
        { date: cur.date, price: cur.price, label: '目前收盤', tag: '現在' }
      ],
      vectorPath: [[spike.date, spike.price], [cur.date, cur.price]],
      explanation: `【強勢噴發教學心法】：${spike.date} 單日開高走高，漲幅達 ${(bestChangePct * 100).toFixed(1)}%，屬於單日噴出的強攻訊號，後續走勢是否延續要觀察量能能否持續配合。`
    };
  },

  buildMaCross(stockData, text) {
    const ref = this.resolveMaFromText(text, stockData);
    const arr = stockData[ref.key];
    const cur = this.lastPoint(stockData);
    let crossPoint = null;

    if (arr) {
      for (let i = 1; i < stockData.candles.length; i++) {
        const prevC = stockData.candles[i - 1][1], prevMa = arr[i - 1];
        const c = stockData.candles[i][1], ma = arr[i];
        if (prevMa != null && ma != null && prevC <= prevMa && c > ma) {
          crossPoint = { date: stockData.dates[i], price: c };
        }
      }
    }

    const pivots = [];
    if (crossPoint) pivots.push({ ...crossPoint, label: `站上${ref.label}`, tag: '突破' });
    pivots.push({ date: cur.date, price: cur.price, label: '目前收盤', tag: '現在' });

    return {
      name: `站上${ref.label}`,
      badge: crossPoint ? `攻克${ref.label}` : `對照${ref.label}`,
      color: '#3b82f6',
      pivots,
      vectorPath: pivots.map(p => [p.date, p.price]),
      resistanceLine: arr && arr[cur.idx] != null ? { price: arr[cur.idx], label: ref.label } : undefined,
      explanation: crossPoint
        ? `【均線攻克教學心法】：${crossPoint.date} 收盤 $${crossPoint.price} 帶量站上${ref.label}，代表短中期成本區易攻難守，該均線轉為支撐。`
        : `目前收盤價與${ref.label}的相對位置如圖所示，近期沒有找到明確的站上瞬間，僅標示目前對照位置。`
    };
  },

  buildPullback(stockData) {
    const swings = this.zigzag(stockData, 0.02);
    const highs = swings.filter(p => p.type === 'high');
    const recentHigh = highs.length ? highs[highs.length - 1] : null;
    const cur = this.lastPoint(stockData);
    const nearest = this.nearestMaLabel(stockData, cur.idx);

    const pivots = [];
    if (recentHigh) pivots.push({ ...recentHigh, label: '近期高點', tag: '高點' });
    pivots.push({ date: cur.date, price: cur.price, label: nearest ? `回測${nearest.label}` : '目前收盤', tag: '拉回' });

    return {
      name: '拉回測試支撐',
      badge: '短線攻擊受阻整理',
      color: '#f59e0b',
      pivots,
      vectorPath: pivots.map(p => [p.date, p.price]),
      resistanceLine: nearest ? { price: nearest.value, label: `拉回測試 ${nearest.label}` } : undefined,
      explanation: `${recentHigh ? `近期高點出現在 ${recentHigh.date}（$${recentHigh.price}），目前` : '目前'}股價拉回，${nearest ? `最貼近${nearest.label}（$${nearest.value.toFixed(1)}），為短線防守關鍵。` : '正在整理中。'}`
    };
  },

  buildCupAndHandle(stockData, text) {
    const candles = stockData.candles || [];
    const dates = stockData.dates || [];
    const n = candles.length;
    if (n < 15) return this.buildFlatFallback(stockData);

    // 1. Find Left Rim (左杯口): highest high in the first part
    const startSearch = Math.max(0, n - 45);
    const midSearch = Math.max(startSearch + 5, n - 15);
    let p1Idx = startSearch, p1Price = -Infinity;
    for (let i = startSearch; i < midSearch; i++) {
      if (candles[i][3] > p1Price) {
        p1Price = candles[i][3];
        p1Idx = i;
      }
    }

    // 2. Find Cup Bottom (杯底低點): lowest low after p1Idx
    let p2Idx = p1Idx, p2Price = Infinity;
    const bottomEnd = Math.max(p1Idx + 2, n - 5);
    for (let i = p1Idx; i < bottomEnd; i++) {
      if (candles[i][2] < p2Price) {
        p2Price = candles[i][2];
        p2Idx = i;
      }
    }

    // 3. Find Right Rim (右杯口 / 杯柄起點): rally peak after cup bottom
    let p3Idx = p2Idx, p3Price = -Infinity;
    const rightRimEnd = Math.max(p2Idx + 2, n - 2);
    for (let i = p2Idx; i < rightRimEnd; i++) {
      if (candles[i][3] > p3Price) {
        p3Price = candles[i][3];
        p3Idx = i;
      }
    }
    if (p3Price === -Infinity || p3Idx === p2Idx) {
      p3Idx = Math.min(n - 2, p2Idx + Math.max(1, Math.floor((n - 1 - p2Idx) / 2)));
      p3Price = candles[p3Idx][3];
    }

    // 4. Find Handle Low (杯柄拉回低點): shallow pullback between p3 and current
    let p4Idx = p3Idx, p4Price = Infinity;
    for (let i = p3Idx; i < n; i++) {
      if (candles[i][2] < p4Price) {
        p4Price = candles[i][2];
        p4Idx = i;
      }
    }
    if (p4Idx === p3Idx && p3Idx < n - 1) {
      p4Idx = n - 1;
      p4Price = candles[p4Idx][2];
    }

    const cur = this.lastPoint(stockData);
    const necklinePrice = Math.max(p1Price, p3Price);
    const isBreakout = cur.price >= necklinePrice * 0.98;

    const p1 = { date: dates[p1Idx], price: p1Price, label: 'P1 左杯口高點', tag: '左杯口' };
    const p2 = { date: dates[p2Idx], price: p2Price, label: 'P2 杯底洗盤支撐', tag: '杯底' };
    const p3 = { date: dates[p3Idx], price: p3Price, label: 'P3 右杯口/杯柄起點', tag: '右杯口' };
    const p4 = { date: dates[p4Idx], price: p4Price, label: 'P4 杯柄回測守穩', tag: '杯柄' };
    const p5 = { date: cur.date, price: cur.price, label: isBreakout ? 'P5 帶量突破頸線' : 'P5 杯柄右側推進', tag: isBreakout ? '突破' : '進行中' };

    const pivots = [p1, p2, p3, p4, p5];

    // Generate smooth U-curve between P1 -> P2 -> P3, and Handle P3 -> P4 -> P5
    const vectorPath = [];
    vectorPath.push([p1.date, p1.price]);
    if (p2Idx - p1Idx > 3) {
      const mid1Idx = Math.floor((p1Idx + p2Idx) / 2);
      vectorPath.push([dates[mid1Idx], candles[mid1Idx][1]]);
    }
    vectorPath.push([p2.date, p2.price]);
    if (p3Idx - p2Idx > 3) {
      const mid2Idx = Math.floor((p2Idx + p3Idx) / 2);
      vectorPath.push([dates[mid2Idx], candles[mid2Idx][1]]);
    }
    vectorPath.push([p3.date, p3.price]);
    if (p4.date !== p3.date) vectorPath.push([p4.date, p4.price]);
    if (p5.date !== p4.date) vectorPath.push([p5.date, p5.price]);

    const cupDepthPct = ((p1Price - p2Price) / p1Price * 100).toFixed(1);
    const handleDepthPct = ((p3Price - p4Price) / p3Price * 100).toFixed(1);

    return {
      name: isBreakout ? '大層級杯柄型態 (測試/突破頸線)' : '杯柄型態 (Cup & Handle)',
      badge: isBreakout ? '經典多頭突破攻擊' : '杯柄整理成型中',
      color: '#ef4444', // 台股多方紅
      pivots,
      vectorPath,
      resistanceLine: { price: necklinePrice, label: `杯柄頸線關鍵壓力位 $${necklinePrice.toFixed(1)}` },
      explanation: `【杯柄型態（Cup & Handle）教學心法】：
1. **左杯口 (P1)**：${p1.date} 於 $${p1.price.toFixed(1)} 形成前波波段高點。
2. **圓弧杯底 (P2)**：經歷 U 型洗盤打底於 ${p2.date} $${p2.price.toFixed(1)}（杯深約 ${cupDepthPct}%，符合經典 12~35% 範圍），籌碼充分沉澱。
3. **右杯口 (P3)**：回升至 ${p3.date} $${p3.price.toFixed(1)} 逼近頸線。
4. **杯柄洗盤 (P4)**：在杯口高檔展開淺幅回測 $${p4.price.toFixed(1)}（幅度僅 ${handleDepthPct}%，屬於強勢量縮淺柄）。
5. **操作建議**：${isBreakout ? '最新價格已挑戰/突破頸線壓力，為經典威廉·歐尼爾（William O\'Neil）右側突破進場黃金買點！' : '目前在杯柄右側醞釀，等待帶量突破頸線後進場。'}`
    };
  },

  buildVcpSqueeze(stockData, text) {
    const swings = this.zigzag(stockData, 0.02);
    const highs = swings.filter(p => p.type === 'high').slice(-3);
    const lows = swings.filter(p => p.type === 'low').slice(-3);
    if (highs.length < 2 || lows.length < 2) return this.buildTriangle(stockData);

    const cur = this.lastPoint(stockData);
    const maxHigh = Math.max(...highs.map(h => h.price));
    const pivots = [];
    highs.forEach((h, i) => pivots.push({ ...h, label: `T${i + 1} 波動收縮高點`, tag: `T${i + 1}H` }));
    lows.forEach((l, i) => pivots.push({ ...l, label: `T${i + 1} 波動收縮低點`, tag: `T${i + 1}L` }));
    pivots.push({ date: cur.date, price: cur.price, label: '目前收盤', tag: '現在' });

    return {
      name: 'VCP 波動收縮整理 (Minervini VCP)',
      badge: '量縮收斂即將噴發',
      color: '#38bdf8',
      pivots,
      vectorPath: pivots.slice().sort((a, b) => (stockData.dates.indexOf(a.date) - stockData.dates.indexOf(b.date))).map(p => [p.date, p.price]),
      resistanceLine: { price: maxHigh, label: `VCP 突破臨界點 $${maxHigh.toFixed(1)}` },
      explanation: `【VCP 波動收縮教學心法】：每次回檔幅度逐步遞減（波浪振幅由寬變窄），代表市場浮額已遭主力鎖碼吸收，最新收盤價逼近臨界壓力線 $${maxHigh.toFixed(1)}，一旦帶量突破即為絕佳右側買點。`
    };
  },

  buildBreakoutHigh(stockData, text) {
    const candles = stockData.candles || [];
    const dates = stockData.dates || [];
    const n = candles.length;
    if (n < 10) return this.buildFlatFallback(stockData);

    let priorMaxHigh = -Infinity, priorMaxIdx = 0;
    for (let i = 0; i < n - 1; i++) {
      if (candles[i][3] > priorMaxHigh) {
        priorMaxHigh = candles[i][3];
        priorMaxIdx = i;
      }
    }
    const cur = this.lastPoint(stockData);
    const isNewHigh = cur.price >= priorMaxHigh;

    const pivots = [
      { date: dates[priorMaxIdx], price: priorMaxHigh, label: '前波歷史/波段最高點', tag: '前高' },
      { date: cur.date, price: cur.price, label: isNewHigh ? '歷史新高突破確認' : '最新收盤價', tag: isNewHigh ? '創天價' : '挑戰新高' }
    ];

    return {
      name: '歷史/波段新高爆量突破 (Breakout)',
      badge: '強勢無套牢壓力',
      color: '#f59e0b',
      pivots,
      vectorPath: [[dates[priorMaxIdx], priorMaxHigh], [cur.date, cur.price]],
      resistanceLine: { price: priorMaxHigh, label: `前波歷史高點壓力線 $${priorMaxHigh.toFixed(1)}` },
      explanation: `【新高突破教學心法】：${dates[priorMaxIdx]} 創下前波高點 $${priorMaxHigh.toFixed(1)}，目前最新收盤價 $${cur.price.toFixed(1)} ${isNewHigh ? '已強勢越過歷史高點，上方無任何套牢賣壓，多頭籌碼乾淨！' : '正在挑戰前波最高點阻力。'}`
    };
  },

  buildGenericTrend(stockData) {
    const swings = this.zigzag(stockData, 0.03);
    if (swings.length < 2) return this.buildFlatFallback(stockData);
    const dirUp = swings[swings.length - 1].price >= swings[0].price;
    const overlay = dirUp ? this.buildLadderUp(stockData) : this.buildLadderDown(stockData);
    overlay.name = dirUp ? '上升趨勢結構（依價格自動判讀）' : '下降趨勢結構（依價格自動判讀）';
    overlay.badge = '（卡片型態文字無對應規則，改用趨勢結構顯示）';
    return overlay;
  },

  MATCHERS: [
    { re: /杯柄|Cup\s*&\s*Handle|杯狀|杯形/i, fn: 'buildCupAndHandle' },
    { re: /VCP|收縮|旗形|窄幅/i, fn: 'buildVcpSqueeze' },
    { re: /歷史新高|波段新高|新高|爆量突破|Breakout/i, fn: 'buildBreakoutHigh' },
    { re: /底底高|頭頭高|墊高|階梯式上升|打底拉升|拉升墊高|多頭排列/i, fn: 'buildLadderUp' },
    { re: /底底低|頭頭低|階梯式下降|節節敗退/, fn: 'buildLadderDown' },
    { re: /雙重底|W底|Double Bottom/i, fn: 'buildWBottom' },
    { re: /雙重頂|M頭/, fn: 'buildMHead' },
    { re: /三角收斂|收斂三角|三角形/, fn: 'buildTriangle' },
    { re: /頭肩頂|頭肩底|頭肩型/, fn: 'buildHeadShoulders' },
    { re: /N字/, fn: 'buildNShape' },
    { re: /箱型|箱體|區間整理|區間震盪|高檔盤整/, fn: 'buildBox' },
    { re: /破底翻|V轉|V型反轉|打底翻揚/, fn: 'buildVReversal' },
    { re: /漲停|噴發|噴出|強勢攻擊/, fn: 'buildBreakoutSpike' },
    { re: /攻克|站上|突破/, fn: 'buildMaCross' },
    { re: /受阻|回測|拉回|縮腳|派發/, fn: 'buildPullback' }
  ],

  /**
   * Main entry point: build the overlay that visualizes the pattern text a real STOCK_CARDS
   * entry already concluded, instead of independently scanning for every known shape.
   */
  buildOverlayForCard(patternText, stockData) {
    const text = (patternText || '').trim();
    if (!stockData || !stockData.dates || stockData.dates.length < 10) return null;

    for (const m of this.MATCHERS) {
      if (m.re.test(text)) {
        const overlay = this[m.fn](stockData, text);
        if (overlay) {
          overlay.sourceText = text;
          return overlay;
        }
      }
    }
    const overlay = this.buildGenericTrend(stockData, text);
    overlay.sourceText = text;
    return overlay;
  },

  /**
   * Secondary, independent view: annotate the raw indicator readings (BOLL/MA/MACD/KD) directly,
   * unrelated to whichever pattern name the card used.
   */
  buildDataGuideOverlay(stockData) {
    const total = stockData.dates.length;
    const lastIdx = total - 1;
    const cur = this.lastPoint(stockData);

    const close = cur.price;
    const upper = stockData.bollUpper && stockData.bollUpper[lastIdx];
    const mid = stockData.bollMid && stockData.bollMid[lastIdx];
    const lower = stockData.bollLower && stockData.bollLower[lastIdx];
    const previousClose = lastIdx > 0 ? stockData.candles[lastIdx - 1][1] : null;
    const previousUpper = lastIdx > 0 && stockData.bollUpper ? stockData.bollUpper[lastIdx - 1] : null;
    const previousMid = lastIdx > 0 && stockData.bollMid ? stockData.bollMid[lastIdx - 1] : null;
    const previousLower = lastIdx > 0 && stockData.bollLower ? stockData.bollLower[lastIdx - 1] : null;
    const fmt = value => Number.isFinite(value) ? Number(value.toFixed(2)) : '—';

    let bollLabel = '布林通道資料不足';
    let bollTag = 'BOLL';
    let bollBadge = '布林位置待確認';
    let color = '#64748b';
    if ([upper, mid, lower].every(Number.isFinite)) {
      if (close > upper) {
        const crossedToday = Number.isFinite(previousClose) && Number.isFinite(previousUpper)
          && previousClose <= previousUpper;
        bollLabel = crossedToday ? '收盤突破布林上軌' : '收盤位於布林上軌之上';
        bollTag = crossedToday ? '突破' : '上軌外';
        bollBadge = crossedToday ? '布林上軌突破' : '布林上軌之上';
        color = '#f43f5e';
      } else if (close >= mid) {
        const crossedMidToday = Number.isFinite(previousClose) && Number.isFinite(previousMid)
          && previousClose < previousMid;
        bollLabel = crossedMidToday
          ? '收盤站上布林中軌，尚未突破上軌'
          : '收盤位於布林中軌之上，尚未突破上軌';
        bollTag = crossedMidToday ? '站上中軌' : '中軌上';
        bollBadge = '布林中上區間';
        color = '#ef4444';
      } else if (close >= lower) {
        const fellBelowMidToday = Number.isFinite(previousClose) && Number.isFinite(previousMid)
          && previousClose >= previousMid;
        bollLabel = fellBelowMidToday
          ? '收盤跌破布林中軌，仍在下軌之上'
          : '收盤位於布林中軌下方、下軌上方';
        bollTag = fellBelowMidToday ? '跌破中軌' : '中軌下';
        bollBadge = '布林中下區間';
        color = '#f59e0b';
      } else {
        const fellBelowLowerToday = Number.isFinite(previousClose) && Number.isFinite(previousLower)
          && previousClose >= previousLower;
        bollLabel = fellBelowLowerToday ? '收盤跌破布林下軌' : '收盤位於布林下軌之下';
        bollTag = fellBelowLowerToday ? '跌破' : '下軌外';
        bollBadge = fellBelowLowerToday ? '布林下軌跌破' : '布林下軌之下';
        color = '#22c55e';
      }
    }

    const ma5 = stockData.ma5 && stockData.ma5[lastIdx];
    const ma10 = stockData.ma10 && stockData.ma10[lastIdx];
    const ma20 = stockData.ma20 && stockData.ma20[lastIdx];
    let maLabel = '均線資料不足';
    let maTag = '均線';
    if ([ma5, ma10, ma20].every(Number.isFinite)) {
      if (ma5 > ma10 && ma10 > ma20) {
        maLabel = 'MA5 > MA10 > MA20，多頭排列';
        maTag = '多頭';
      } else if (ma5 < ma10 && ma10 < ma20) {
        maLabel = 'MA5 < MA10 < MA20，空頭排列';
        maTag = '空頭';
      } else {
        maLabel = '短中期均線交錯，尚未形成標準排列';
        maTag = '交錯';
      }
    }

    const bollDetail = [upper, mid, lower].every(Number.isFinite)
      ? `收盤 ${fmt(close)}；上軌 ${fmt(upper)}、中軌 ${fmt(mid)}、下軌 ${fmt(lower)}。`
      : `收盤 ${fmt(close)}；布林通道資料不足。`;

    return {
      name: '技術數據視覺解構',
      badge: bollBadge,
      color,
      pivots: [
        { date: cur.date, price: close, label: bollLabel, tag: bollTag },
        { date: cur.date, price: Number.isFinite(ma10) ? ma10 : close, label: maLabel, tag: maTag }
      ],
      vectorPath: [],
      explanation: `【技術數據視覺化導讀】：\n1. 【K線與布林】：${bollDetail}${bollLabel}。\n2. 【均線排列】：MA5=${fmt(ma5)}、MA10=${fmt(ma10)}、MA20=${fmt(ma20)}；${maLabel}。\n3. 【MACD】：觀察下方 MACD 柱狀圖近 1～3 日是擴大或縮減。\n4. 【KD指標】：觀察 K、D 的交叉方向與邊際變化。`,
      indicatorAnnotations: [
        { seriesName: 'K線', type: 'markPoint', coord: [cur.date, close], label: `${bollLabel}\n收盤 ${fmt(close)}／上軌 ${fmt(upper)}`, color, yOffset: -30 },
        { seriesName: 'MA10', type: 'markPoint', coord: [cur.date, Number.isFinite(ma10) ? ma10 : close], label: maLabel, color: '#3b82f6', yOffset: 20 },
        { seriesName: 'MACD', type: 'markPoint', coord: [cur.date, 0], label: '柱狀圖加速度\n觀察點', color: '#f59e0b', yOffset: -25 },
        { seriesName: 'K值', type: 'markArea', yAxisStart: 80, yAxisEnd: 100, label: 'KD 高檔區', color: 'rgba(244, 63, 94, 0.15)' }
      ]
    };
  }
};
