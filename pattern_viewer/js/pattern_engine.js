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
    return {
      name: '資料不足以判讀型態',
      badge: '資料不足',
      color: '#64748b',
      pivots: [],
      vectorPath: [],
      explanation: '近期價格波動幅度過小或資料天數不足，無法可靠地畫出型態轉折點，僅顯示目前收盤位置。'
    };
  },

  buildLadderUp(stockData) {
    const swings = this.zigzag(stockData, 0.03).slice(-5);
    if (swings.length < 2) return this.buildFlatFallback(stockData);
    const pivots = swings.map((p, i) => ({
      date: p.date, price: p.price,
      label: p.type === 'low' ? '波段低點支撐' : '波段高點突破',
      tag: p.type === 'low' ? '低點' : '高點'
    }));
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
    const pivots = swings.map((p, i) => ({
      date: p.date, price: p.price,
      label: p.type === 'high' ? '反彈高點受阻' : '波段低點跌破',
      tag: p.type === 'high' ? '高點' : '低點'
    }));
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
    if (valleys.length < 2) return this.buildGenericTrend(stockData);
    
    // 尋找最後兩個擺動低點作為 W 雙腳
    let p1 = null, p2 = null, p3 = null;
    for (let i = valleys.length - 1; i >= 1; i--) {
      const vRight = valleys[i];
      const vLeft = valleys[i - 1];
      // 兩腳之間必須有至少 4 天以上的時間間隔
      if (vRight.idx - vLeft.idx >= 4) {
        // 尋找兩腳之間的最高頸線反彈點
        const peak = swings.filter(p => p.type === 'high' && p.idx > vLeft.idx && p.idx < vRight.idx)
          .sort((a, b) => b.price - a.price)[0];
        if (peak && peak.price >= vLeft.price * 1.03) {
          p1 = vLeft;
          p2 = peak;
          p3 = vRight;
          break;
        }
      }
    }

    if (!p1 || !p2 || !p3) return this.buildGenericTrend(stockData);

    const cur = this.lastPoint(stockData);
    const broke = cur.price >= p2.price;

    const pivots = [
      { ...p1, label: '左腳打底低點', tag: '左腳' },
      { ...p2, label: '頸線反彈高點', tag: '頸線' },
      { ...p3, label: '右腳二次測試', tag: '右腳' }
    ];

    return {
      name: 'W底（雙重底）反轉型態',
      badge: broke ? 'W底突破確立' : 'W底雙腳成型（蓄勢突破）',
      color: '#ef4444', // 台股：偏多紅
      pivots,
      vectorPath: pivots.map(p => [p.date, p.price]),
      resistanceLine: { price: p2.price, label: `雙底頸線壓力 ($${p2.price.toFixed(1)})` },
      explanation: `【W底教學心法】：第一次打底於 ${p1.date}（$${p1.price}），反彈至頸線 ${p2.date}（$${p2.price}）後拉回二次測試 ${p3.date}（$${p3.price}）完成 W 雙腳打底，${broke ? '最新收盤價已強勢衝破頸線，W 底反轉型態正式確立！' : '目前價格正在頸線附近蓄勢整理，若帶量突破頸線則底部翻揚。'}`
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

    const pivots = [{ ...p1, label: '左頭衝高頂點', tag: '左頭' }];
    if (p2) pivots.push({ ...p2, label: '頸線回測支撐', tag: '頸線' });
    if (p3.idx !== p1.idx) pivots.push({ ...p3, label: '右頭派發高點', tag: '右頭' });

    return {
      name: 'M頭（雙重頂）',
      badge: broke ? '空頭型態已確立' : '高檔警訊型態',
      color: '#10b981', // 台股：偏空綠
      pivots,
      vectorPath: pivots.map(p => [p.date, p.price]),
      resistanceLine: p2 ? { price: p2.price, label: '雙頂頸線關鍵支撐線' } : undefined,
      explanation: `【M頭教學心法】：高檔第一次受阻於 ${p1.date}（$${p1.price}）${p2 ? `，拉回測試頸線 $${p2.price} 後` : ''}再度受阻於 ${p3.date}（$${p3.price}），${broke ? '目前已跌破頸線，空頭型態確立。' : '目前仍在頸線附近整理，尚未確認跌破。'}`
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

    const pivots = [{ ...v1, label: '起漲轉折低點', tag: '起漲' }];
    if (pk) pivots.push({ ...pk, label: '衝高波段壓力', tag: '前高' });
    if (v2) pivots.push({ ...v2, label: '洗盤回測支撐', tag: '回測' });

    return {
      name: 'N字強勢反攻',
      badge: broke ? '多頭續攻型態' : '型態醞釀中',
      color: '#f59e0b',
      pivots,
      vectorPath: pivots.map(p => [p.date, p.price]),
      resistanceLine: pk ? { price: pk.price, label: '關鍵壓力轉支撐線' } : undefined,
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
      { date: stockData.dates[boxMaxIdx], price: boxMax, label: '箱型整理上緣', tag: '箱頂' }
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
    const swings = this.zigzag(stockData, 0.025);
    const highs = swings.filter(p => p.type === 'high');
    const lows = swings.filter(p => p.type === 'low');
    if (highs.length < 2 || lows.length < 2) return this.buildFlatFallback(stockData);

    const upperFrom = highs[highs.length - 2], upperTo = highs[highs.length - 1];
    const lowerFrom = lows[lows.length - 2], lowerTo = lows[lows.length - 1];

    const cur = this.lastPoint(stockData);
    const converging = upperTo.price <= upperFrom.price && lowerTo.price >= lowerFrom.price;

    const pivots = [
      { ...upperFrom, label: '上邊界高點 1', tag: '上軌' },
      { ...upperTo, label: '上邊界高點 2', tag: '上軌' },
      { ...lowerFrom, label: '下邊界低點 1', tag: '下軌' },
      { ...lowerTo, label: '下邊界低點 2', tag: '下軌' }
    ];

    return {
      name: converging ? '三角收斂' : '擴散/震盪三角',
      badge: converging ? '末端即將表態' : '震盪加劇',
      color: '#f59e0b',
      pivots,
      boundaryLines: [
        { points: [[upperFrom.date, upperFrom.price], [upperTo.date, upperTo.price]], color: '#ef4444' },
        { points: [[lowerFrom.date, lowerFrom.price], [lowerTo.date, lowerTo.price]], color: '#10b981' }
      ],
      explanation: `【三角收斂教學心法】：高點由 ${upperFrom.date} 的 $${upperFrom.price} 下移至 ${upperTo.date} 的 $${upperTo.price}，低點由 ${lowerFrom.date} 的 $${lowerFrom.price} 墊高至 ${lowerTo.date} 的 $${lowerTo.price}，震盪幅度越來越窄，代表多空即將在末端分出勝負。`
    };
  },

  buildHeadShoulders(stockData, text) {
    const isBottom = /底/.test(text);
    const swings = this.zigzag(stockData, 0.03);
    const targetType = isBottom ? 'low' : 'high';
    const extremes = swings.filter(p => p.type === targetType);
    if (extremes.length < 3) return this.buildGenericTrend(stockData);

    const head = extremes[extremes.length - 2];
    const left = extremes[extremes.length - 3];
    const right = extremes[extremes.length - 1];

    const pivots = [
      { ...left, label: isBottom ? '左肩打底' : '左肩高點', tag: '左肩' },
      { ...head, label: isBottom ? '頭部最低點' : '頭部最高點', tag: '頭部' },
      { ...right, label: isBottom ? '右肩抬高' : '右肩受阻', tag: '右肩' }
    ];

    return {
      name: isBottom ? '頭肩底（反轉打底）' : '頭肩頂（高檔派發）',
      badge: isBottom ? '底部反轉形態' : '高檔頭部形態',
      color: isBottom ? '#ef4444' : '#10b981',
      pivots,
      vectorPath: pivots.map(p => [p.date, p.price]),
      explanation: isBottom
        ? `【頭肩底教學心法】：左肩 ${left.date} $${left.price}、頭部最低點 ${head.date} $${head.price}、右肩 ${right.date} $${right.price}，右肩未再破底，若帶量突破頸線將確立底部。`
        : `【頭肩頂教學心法】：左肩 ${left.date} $${left.price}、頭部衝最高 ${head.date} $${head.price}、右肩反彈無力於 ${right.date} $${right.price} 受阻，多頭動能耗盡。`
    };
  },

  buildVReversal(stockData) {
    const swings = this.zigzag(stockData, 0.03);
    const valleys = swings.filter(p => p.type === 'low');
    if (!valleys.length) return this.buildFlatFallback(stockData);
    const lowest = valleys.slice(-3).sort((a, b) => a.price - b.price)[0];
    const cur = this.lastPoint(stockData);
    const risePct = (cur.price - lowest.price) / lowest.price;

    const pivots = [
      { ...lowest, label: '急跌破底低點', tag: '轉折點' }
    ];

    return {
      name: 'V型反轉 / 破底翻',
      badge: risePct > 0.05 ? '急跌強彈確立' : '短線反彈觀察',
      color: '#ef4444',
      pivots,
      vectorPath: [[lowest.date, lowest.price], [cur.date, cur.price]],
      explanation: `【V轉教學心法】：於 ${lowest.date} 打出 $${lowest.price} 急速反轉點，目前已自低點強彈 +${(risePct * 100).toFixed(1)}%，屬於籌碼快速洗盤後的強勢收復訊號。`
    };
  },

  buildBreakoutSpike(stockData) {
    const cur = this.lastPoint(stockData);
    const candles = stockData.candles;
    const prevClose = candles.length >= 2 ? candles[candles.length - 2][1] : cur.price;
    const pct = ((cur.price - prevClose) / prevClose) * 100;
    const baseIdx = Math.max(0, candles.length - 6);
    const baseLow = Math.min(...candles.slice(baseIdx).map(c => c[2]));

    const pivots = [
      { date: stockData.dates[baseIdx], price: baseLow, label: '起漲平台區', tag: '起漲' }
    ];

    return {
      name: '強勢長紅 / 漲停噴出',
      badge: '強勢攻擊訊號',
      color: '#ef4444',
      pivots,
      vectorPath: [[stockData.dates[baseIdx], baseLow], [cur.date, cur.price]],
      explanation: `【長紅噴出教學心法】：最新收盤單日漲幅 ${pct.toFixed(2)}%，脫離近 5 日打底平台 $${baseLow}，主力攻擊意圖強烈。`
    };
  },

  pickTargetMa(stockData, text) {
    return this.resolveMaFromText(text, stockData);
  },

  buildMaCross(stockData, text) {
    const cur = this.lastPoint(stockData);
    const targetMa = this.pickTargetMa(stockData, text);
    const maValue = stockData[targetMa.key] ? stockData[targetMa.key][cur.idx] : null;
    const diff = maValue != null ? cur.price - maValue : 0;

    const pivots = [];
    if (maValue != null) pivots.push({ date: cur.date, price: Number(maValue.toFixed(1)), label: targetMa.label, tag: '均線' });

    return {
      name: `帶量攻克 ${targetMa.label}`,
      badge: diff >= 0 ? '均線翻揚站上' : '挑戰均線中',
      color: '#ef4444',
      pivots,
      resistanceLine: maValue != null ? { price: maValue, label: `${targetMa.label} 支撐線 $${maValue.toFixed(1)}` } : undefined,
      explanation: `【站上均線教學心法】：最新收盤價 $${cur.price} ${diff >= 0 ? '已穩居' : '正逼近'} ${targetMa.label}（$${maValue != null ? maValue.toFixed(1) : '—'}）之上，${diff >= 0 ? '均線由壓力轉為下檔防守支撐。' : '正在挑戰上方均線反壓。'}`
    };
  },

  buildPullback(stockData, text) {
    const cur = this.lastPoint(stockData);
    const targetMa = this.pickTargetMa(stockData, text);
    const maValue = stockData[targetMa.key] ? stockData[targetMa.key][cur.idx] : null;
    const swings = this.zigzag(stockData, 0.03);
    const recentHigh = swings.filter(p => p.type === 'high').slice(-1)[0];

    const pivots = [];
    if (recentHigh) pivots.push({ ...recentHigh, label: '前波衝高阻力', tag: '前高' });
    if (maValue != null) pivots.push({ date: cur.date, price: Number(maValue.toFixed(1)), label: targetMa.label, tag: '均線' });

    return {
      name: `回測 ${targetMa.label} / 縮腳整理`,
      badge: '強勢回檔整理',
      color: '#f59e0b',
      pivots,
      resistanceLine: recentHigh ? { price: recentHigh.price, label: `前波高點阻力 $${recentHigh.price}` } : undefined,
      explanation: `${recentHigh ? `衝高出現在 ${recentHigh.date}（$${recentHigh.price}），目前` : '目前'}股價拉回，${maValue != null ? `正回測 ${targetMa.label}（$${maValue.toFixed(1)}）尋求支撐，量縮守穩為健康換手。` : '正在進行整理。'}`
    };
  },

  buildCupAndHandle(stockData, text) {
    const candles = stockData.candles || [];
    const dates = stockData.dates || [];
    const n = candles.length;
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
1. **左杯口**：${p1.date} 於 $${p1.price.toFixed(1)} 形成前波波段高點。
2. **圓弧杯底**：經歷 U 型洗盤打底於 ${p2.date} $${p2.price.toFixed(1)}（杯深約 ${cupDepthPct}%，符合經典 12~35% 範圍），籌碼充分沉澱。
3. **右杯口**：回升至 ${p3.date} $${p3.price.toFixed(1)} 逼近頸線。
4. **杯柄洗盤**：在杯口高檔展開淺幅回測 $${p4.price.toFixed(1)}（幅度僅 ${handleDepthPct}%，屬於強勢量縮淺柄）。
5. **操作建議**：${isBreakout ? '最新價格已挑戰/突破頸線壓力，為經典威廉·歐尼爾（William O\'Neil）右側突破進場黃金買點！' : '目前在杯柄右側醞釀，等待帶量突破頸線後進場。'}`
    };
  },

  buildVcpSqueeze(stockData, text) {
    const swings = this.zigzag(stockData, 0.02);
    const highs = swings.filter(p => p.type === 'high').slice(-3);
    const lows = swings.filter(p => p.type === 'low').slice(-3);
    if (highs.length < 2 || lows.length < 2) return this.buildTriangle(stockData);

    const maxHigh = Math.max(...highs.map(h => h.price));
    const pivots = [];
    highs.forEach((h, i) => pivots.push({ ...h, label: `收縮高點 ${i + 1}`, tag: `高點` }));
    lows.forEach((l, i) => pivots.push({ ...l, label: `收縮低點 ${i + 1}`, tag: `低點` }));

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
