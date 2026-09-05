/**
 * ChartEngine Module
 * Renders 5-pane synchronized interactive charts (K-line, Volume, RSI, MACD, KD)
 * using Apache ECharts with AI pattern visual vector overlay and teaching badges.
 */

window.ChartEngine = {
  instances: new Map(),
  activeDom: null,
  _globalResizeBound: false,

  get chartInstance() {
    if (this.activeDom && this.instances.has(this.activeDom)) {
      const state = this.instances.get(this.activeDom);
      if (state && state.chartInstance && !state.chartInstance.isDisposed()) {
        return state.chartInstance;
      }
    }
    for (const state of this.instances.values()) {
      if (state.chartInstance && !state.chartInstance.isDisposed()) {
        return state.chartInstance;
      }
    }
    return null;
  },

  /**
   * Initialize or update the 5-pane chart
   * @param {string|HTMLElement} container 
   * @param {Object} stockData 
   * @param {Object} overlayData 
   * @param {Object} displayToggles { showBoll: true, showMa: true }
   */
  render(container, stockData, overlayData, displayToggles = { showBoll: true, showMa: true }) {
    if (typeof echarts === 'undefined') {
      const errMsg = '❌ ECharts 圖表庫尚未載入完成 (echarts is undefined)。請重新整理頁面。';
      console.error('[ChartEngine Error]', errMsg);
      const targetDom = typeof container === 'string' ? document.querySelector(container) : container;
      if (targetDom) {
        targetDom.hidden = false;
        targetDom.innerHTML = `<div style="padding:40px 20px; color:#f87171; text-align:center; font-size:16px; font-weight:800; background:rgba(239,68,68,0.1); border:1px solid rgba(239,68,68,0.3); border-radius:8px;">${errMsg}</div>`;
      }
      return;
    }

    if (!stockData || !stockData.dates || stockData.dates.length === 0) {
      console.warn('ChartEngine: stockData is empty');
      return;
    }

    const dom = typeof container === 'string' ? document.querySelector(container) : container;
    if (!dom) {
      console.error('[ChartEngine Error] 找不到圖表容器 DOM:', container);
      return;
    }

    // 強制移除 hidden 屬性與確保 display: block
    dom.hidden = false;
    dom.style.display = 'block';

    const parentW = dom.parentElement ? dom.parentElement.clientWidth : 0;
    const domWidth = dom.clientWidth || dom.offsetWidth || parentW || 900;
    const domHeight = Math.max(dom.clientHeight || dom.offsetHeight || 0, 850);

    console.log('[ChartEngine Debug] 🚀 ChartEngine.render() 開始執行', {
      stock: stockData.title,
      datesCount: stockData.dates ? stockData.dates.length : 0,
      domWidth,
      domHeight,
      rawClientWidth: dom.clientWidth,
      rawClientHeight: dom.clientHeight,
      parentWidth: parentW
    });

    this.activeDom = dom;
    const isLight = document.documentElement.getAttribute('data-theme') === 'light';
    const targetTheme = isLight ? 'light' : 'dark';

    let state = this.instances.get(dom);
    if (!state || !state.chartInstance || state.chartInstance.isDisposed() || state.theme !== targetTheme) {
      if (state && state.chartInstance && !state.chartInstance.isDisposed()) {
        try {
          if (typeof state.zoomCleanup === 'function') state.zoomCleanup();
          state.chartInstance.dispose();
        } catch (e) {}
      }
      const chartInstance = echarts.init(dom, targetTheme, {
        width: domWidth,
        height: domHeight
      });
      const zoomCleanup = this._attachZoomFix(dom);
      state = {
        chartInstance,
        theme: targetTheme,
        zoomCleanup,
        lastStockKey: null,
        lastStockData: null,
        lastOverlayData: null,
        lastDisplayToggles: null,
        lastShowMa: null,
        lastShowBoll: null,
        axisPointerHandler: null,
        hudMouseLeaveBound: false
      };
      this.instances.set(dom, state);
      if (!this._globalResizeBound) {
        this._globalResizeBound = true;
        window.addEventListener('resize', () => this.resize());
      }
    } else {
      state.chartInstance.resize({
        width: domWidth,
        height: domHeight
      });
    }

    // Default to the most recent 20 trading days so candles are clear and not squished.
    // Only reset the zoom when the stock actually changes — re-renders for the same stock (toggling
    // BOLL/MA, etc.) keep whatever zoom range the user has manually set.
    const DEFAULT_ZOOM_DAYS = 20;
    const total = stockData.dates.length;
    const stockKey = `${stockData.title}|${total}`;
    const isSameStock = state.lastStockKey === stockKey;
    const showShortMa = displayToggles.showMa !== false;
    const showBoll = displayToggles.showBoll !== false;
    const maToggleChanged = isSameStock && state.lastShowMa !== null && state.lastShowMa !== showShortMa;
    const bollToggleChanged = isSameStock && state.lastShowBoll !== null && state.lastShowBoll !== showBoll;
    let zoomStart, zoomEnd;
    let prevLegendSelected = null;
    if (isSameStock) {
      const prevOption = state.chartInstance.getOption();
      const prevZoom = prevOption && prevOption.dataZoom && prevOption.dataZoom[0];
      if (prevZoom) { zoomStart = prevZoom.start; zoomEnd = prevZoom.end; }
      if (prevOption && prevOption.legend && prevOption.legend[0] && prevOption.legend[0].selected) {
        prevLegendSelected = prevOption.legend[0].selected;
      }
    }
    if (zoomStart == null) {
      zoomEnd = 100;
      zoomStart = total > DEFAULT_ZOOM_DAYS
        ? Math.max(0, Math.round(((total - DEFAULT_ZOOM_DAYS) / total) * 100))
        : 0;
    }
    state.lastStockKey = stockKey;
    state.lastStockData = stockData;
    state.lastOverlayData = overlayData;
    state.lastDisplayToggles = displayToggles;
    state.lastShowMa = showShortMa;
    state.lastShowBoll = showBoll;

    const {
      dates,
      candles,
      volumes,
      vma5,
      vma20,
      ma5,
      rsi,
      rsi6,
      rsi12,
      dif,
      macdSignal,
      macdHist,
      kList,
      dList,
      bollUpper,
      bollMid,
      bollLower
    } = stockData;

    // Build MarkPoints and MarkLines for Pattern Teaching Overlay
    const markPoints = [];
    const markLines = [];

    if (overlayData) {
      // 1. MarkPoints (P1, P2, P3, P4, 現在) 已依使用者指示全面移除，保持 K 線乾淨不被氣泡圖釘遮擋。

      // 2. Pattern Vector Trendline Segments (P1 -> P2 -> P3 -> P4)
      if (overlayData.vectorPath && overlayData.vectorPath.length > 1) {
        for (let i = 0; i < overlayData.vectorPath.length - 1; i++) {
          const pt1 = overlayData.vectorPath[i];
          const pt2 = overlayData.vectorPath[i + 1];
          markLines.push([
            {
              coord: pt1,
              lineStyle: {
                color: overlayData.color || '#f59e0b',
                width: 4,
                type: 'solid',
                shadowColor: overlayData.color || '#f59e0b',
                shadowBlur: 12
              }
            },
            {
              coord: pt2
            }
          ]);
        }
      }

      // 2.1 Boundary Lines (e.g. triangle convergence edges, sloped neckline) — independent 2-point lines
      if (overlayData.boundaryLines) {
        overlayData.boundaryLines.forEach(line => {
          if (!line.points || line.points.length < 2) return;
          markLines.push([
            {
              coord: line.points[0],
              lineStyle: {
                color: line.color || overlayData.color || '#f59e0b',
                width: 2,
                type: line.dashed ? 'dashed' : 'solid'
              }
            },
            {
              coord: line.points[1],
              label: {
                show: !!line.label,
                formatter: line.label || '',
                position: 'end',
                fontSize: 10,
                color: line.color || overlayData.color || '#f59e0b'
              }
            }
          ]);
        });
      }

      // 3. Resistance / Key Level Horizontal Line
      if (overlayData.resistanceLine) {
        markLines.push({
          name: overlayData.resistanceLine.label,
          yAxis: overlayData.resistanceLine.price,
          lineStyle: {
            color: overlayData.color || '#f59e0b',
            type: 'dashed',
            width: 2
          },
          label: {
            formatter: `${overlayData.resistanceLine.label}: $${overlayData.resistanceLine.price}`,
            position: 'end',
            fontSize: 11,
            color: overlayData.color || '#f59e0b'
          }
        });
      }
    }

    // Grid Layouts for 5 Panes (Height allocation)
    // 0: K-line (38%), 1: VOL (10%), 2: RSI (10%), 3: MACD (10%), 4: KD (10%)
    const chartBg = isLight ? '#ffffff' : '#0b0f19';
    const axisLineColor = isLight ? '#94a3b8' : '#222c3f';
    const splitLineColor = isLight ? '#f1f5f9' : '#151b28';
    const legendTextColor = isLight ? '#1e293b' : '#cbd5e1';
    const zoomBorderColor = isLight ? '#cbd5e1' : '#222c3f';

    const option = {
      backgroundColor: chartBg,
      animation: true,
      tooltip: {
        trigger: 'axis',
        showContent: false,
        axisPointer: {
          type: 'cross',
          link: [{ xAxisIndex: 'all' }] // Crosshair sync across all 5 panes!
        }
      },
      axisPointer: {
        link: [{ xAxisIndex: 'all' }],
        label: { backgroundColor: '#3b82f6' }
      },
      legend: {
        data: ['K線', 'MA5', 'MA10', 'MA20', 'MA60', 'MA120', 'BOLL上軌', 'BOLL下軌', '成交量', 'MV5', 'MV20', 'RSI(6)', 'RSI(12)', 'MACD柱體', 'DIF快線', 'MACD慢線', 'K值', 'D值'],
        top: 6,
        textStyle: { color: legendTextColor, fontSize: 11 },
        selected: (() => {
          const selected = Object.assign({
          'K線': true,
          'MA5': true,
          'MA10': true,
          'MA20': true,
          'MA60': false,
          'MA120': false,
          'BOLL上軌': displayToggles.showBoll !== false,
          'BOLL下軌': displayToggles.showBoll !== false,
          '成交量': true,
          'MV5': true,
          'MV20': true,
          'RSI(6)': true,
          'RSI(12)': true,
          'MACD柱體': true,
          'DIF快線': true,
          'MACD慢線': true,
          'K值': true,
          'D值': true
          }, isSameStock ? (prevLegendSelected || {}) : {});

          // 換股一律套用統一預設；同股只有按下「均線」或「BOLL」開關時覆寫。
          if (!isSameStock || maToggleChanged) {
            selected['K線'] = true;
            selected['MA5'] = showShortMa;
            selected['MA10'] = showShortMa;
            selected['MA20'] = showShortMa;
            if (!isSameStock) {
              selected['MA60'] = false;
              selected['MA120'] = false;
            }
          }
          if (!isSameStock || bollToggleChanged) {
            selected['BOLL上軌'] = showBoll;
            selected['BOLL下軌'] = showBoll;
          }
          return selected;
        })()
      },
      grid: [
        // ⬜ 白色框：K線主圖（依指示加大 3 倍 pitch：top: 8.5% HUD -> 11.0% Grid）
        { left: '6%', right: '4%', top: '11.0%', height: '19.8%' }, // bottom: 30.8% (留白 4.2% 至 VOL)
        // 🟨 黃色框：成交量 VOL（top: 35.0% HUD -> 37.0% Grid，僅 2.0% 間距，文字緊貼圖表頂部）
        { left: '6%', right: '4%', top: '37.0%', height: '8.5%' }, // bottom: 45.5% (留白 4.5% 至 RSI)
        // 🟧 橘色框：RSI 指標（top: 50.0% HUD -> 52.0% Grid，僅 2.0% 間距，文字緊貼圖表頂部）
        { left: '6%', right: '4%', top: '52.0%', height: '8.5%' }, // bottom: 60.5% (留白 4.5% 至 MACD)
        // 🟧 橘色框：MACD 指標（top: 65.0% HUD -> 67.0% Grid，僅 2.0% 間距，徹底消除原 4.png 標示的黃色留白框）
        { left: '6%', right: '4%', top: '67.0%', height: '8.5%' }, // bottom: 75.5% (留白 4.5% 至 KD)
        // 🟩 綠色框：KD 指標（top: 80.0% HUD -> 82.0% Grid，僅 2.0% 間距，徹底消除原 4.png 標示的黃色留白框）
        { left: '6%', right: '4%', top: '82.0%', height: '8.5%' }  // bottom: 90.5% (底部 9.5% 留給日期時間軸與 dataZoom)
      ],
      xAxis: [
        { type: 'category', data: dates, gridIndex: 0, axisLabel: { show: false }, axisLine: { lineStyle: { color: axisLineColor } } },
        { type: 'category', data: dates, gridIndex: 1, axisLabel: { show: false }, axisLine: { lineStyle: { color: axisLineColor } } },
        { type: 'category', data: dates, gridIndex: 2, axisLabel: { show: false }, axisLine: { lineStyle: { color: axisLineColor } } },
        { type: 'category', data: dates, gridIndex: 3, axisLabel: { show: false }, axisLine: { lineStyle: { color: axisLineColor } } },
        { type: 'category', data: dates, gridIndex: 4, axisLine: { lineStyle: { color: axisLineColor } } }
      ],
      yAxis: [
        // Pane 0: K-line Price
        { scale: true, gridIndex: 0, axisLine: { lineStyle: { color: axisLineColor } }, splitLine: { lineStyle: { color: splitLineColor } } },
        // Pane 1: Volume
        { scale: true, gridIndex: 1, axisLabel: { show: false }, splitLine: { show: false } },
        // Pane 2: RSI (0-100)
        { min: 0, max: 100, gridIndex: 2, splitLine: { lineStyle: { color: splitLineColor } } },
        // Pane 3: MACD
        { scale: true, gridIndex: 3, splitLine: { lineStyle: { color: splitLineColor } } },
        // Pane 4: KD (0-100)
        { min: 0, max: 100, gridIndex: 4, splitLine: { lineStyle: { color: splitLineColor } } }
      ],
      dataZoom: [
        { type: 'inside', xAxisIndex: [0, 1, 2, 3, 4], start: zoomStart, end: zoomEnd },
        { type: 'slider', xAxisIndex: [0, 1, 2, 3, 4], bottom: '0.8%', height: 16, borderColor: zoomBorderColor, fillerColor: 'rgba(59, 130, 246, 0.2)', start: zoomStart, end: zoomEnd }
      ],
      series: [
        // 0. K-line Candlestick (Grid 0)
        {
          name: 'K線',
          type: 'candlestick',
          xAxisIndex: 0,
          yAxisIndex: 0,
          data: candles,
          itemStyle: {
            color: 'transparent',        // 紅漲：空心
            color0: 'transparent',       // 綠跌：空心
            borderColor: '#ef4444',      // 亮紅粗外框
            borderColor0: '#10b981',     // 翠綠粗外框
            borderWidth: 2               // 現代感粗外框 (2px)
          },
          markPoint: {
            data: markPoints
          },
          markLine: {
            data: markLines,
            symbol: ['none', 'none']
          }
        },
        // 1. MA5 (Grid 0)
        {
          name: 'MA5',
          type: 'line',
          xAxisIndex: 0,
          yAxisIndex: 0,
          data: ma5,
          smooth: true,
          showSymbol: false,
          lineStyle: { color: '#f59e0b', width: 1.5 }
        },
        // 1.1 MA10 (Grid 0)
        {
          name: 'MA10',
          type: 'line',
          xAxisIndex: 0,
          yAxisIndex: 0,
          data: stockData.ma10 || [],
          smooth: true,
          showSymbol: false,
          lineStyle: { color: '#3b82f6', width: 1.5 }
        },
        // 1.2 MA20 (Grid 0)
        {
          name: 'MA20',
          type: 'line',
          xAxisIndex: 0,
          yAxisIndex: 0,
          data: stockData.ma20 || [],
          smooth: true,
          showSymbol: false,
          lineStyle: { color: '#ec4899', width: 1.5 }
        },
        // 1.3 MA60 (Grid 0)
        {
          name: 'MA60',
          type: 'line',
          xAxisIndex: 0,
          yAxisIndex: 0,
          data: stockData.ma60 || [],
          smooth: true,
          showSymbol: false,
          lineStyle: { color: '#10b981', width: 1.5 }
        },
        // 1.4 MA120 (Grid 0)
        {
          name: 'MA120',
          type: 'line',
          xAxisIndex: 0,
          yAxisIndex: 0,
          data: stockData.ma120 || [],
          smooth: true,
          showSymbol: false,
          lineStyle: { color: '#8b5cf6', width: 1.5 }
        },
        // 2. BOLL Upper (Grid 0)
        {
          name: 'BOLL上軌',
          type: 'line',
          xAxisIndex: 0,
          yAxisIndex: 0,
          data: bollUpper,
          smooth: true,
          showSymbol: false,
          lineStyle: { color: '#a855f7', width: 1, type: 'dashed' }
        },
        // 3. BOLL Lower (Grid 0)
        {
          name: 'BOLL下軌',
          type: 'line',
          xAxisIndex: 0,
          yAxisIndex: 0,
          data: bollLower,
          smooth: true,
          showSymbol: false,
          lineStyle: { color: '#a855f7', width: 1, type: 'dashed' }
        },

        // 5. Pane 1: Volume (Grid 1)
        {
          name: '成交量',
          type: 'bar',
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: volumes.map((v, i) => {
            const isUp = candles[i][1] >= candles[i][0];
            return {
              value: v,
              itemStyle: {
                color: 'transparent',                       // 中空透明
                borderColor: isUp ? '#ef4444' : '#10b981', // 亮紅 / 翠綠外框
                borderWidth: 2                              // 粗外框 (2px)
              }
            };
          })
        },
        // 5.1 MV5 (5日均量線 - 快線科技藍)
        {
          name: 'MV5',
          type: 'line',
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: vma5,
          smooth: true,
          showSymbol: false,
          lineStyle: { color: '#38bdf8', width: 1.5 }
        },
        // 5.2 MV20 (20日均量線 - 慢線琥珀黃)
        {
          name: 'MV20',
          type: 'line',
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: vma20,
          smooth: true,
          showSymbol: false,
          lineStyle: { color: '#f59e0b', width: 1.5 }
        },

        // 6. Pane 2: RSI 雙線 (Grid 2) - RSI(6) 短線科技藍 + RSI(12) 長線琥珀黃
        {
          name: 'RSI(6)',
          type: 'line',
          xAxisIndex: 2,
          yAxisIndex: 2,
          data: (rsi6 && rsi6.length) ? rsi6 : rsi,
          smooth: true,
          showSymbol: false,
          lineStyle: { color: '#38bdf8', width: 2 }, // 短線科技藍
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: 'rgba(56, 189, 248, 0.12)' },
              { offset: 1, color: 'rgba(56, 189, 248, 0.0)' }
            ])
          }
        },
        {
          name: 'RSI(12)',
          type: 'line',
          xAxisIndex: 2,
          yAxisIndex: 2,
          data: (rsi12 && rsi12.length) ? rsi12 : rsi,
          smooth: true,
          showSymbol: false,
          lineStyle: { color: '#f59e0b', width: 1.8 }, // 長線琥珀黃
          markLine: {
            symbol: 'none',
            data: [
              { yAxis: 80, lineStyle: { color: '#ef4444', type: 'dashed', width: 1 }, label: { show: false } },
              { yAxis: 70, lineStyle: { color: 'rgba(239, 68, 68, 0.5)', type: 'dotted', width: 1 }, label: { show: false } },
              { yAxis: 50, lineStyle: { color: 'rgba(148, 163, 184, 0.35)', type: 'dashed', width: 1 }, label: { show: false } },
              { yAxis: 30, lineStyle: { color: 'rgba(16, 185, 129, 0.5)', type: 'dotted', width: 1 }, label: { show: false } },
              { yAxis: 20, lineStyle: { color: '#10b981', type: 'dashed', width: 1 }, label: { show: false } }
            ]
          }
        },

        // 7. Pane 3: MACD (Grid 3) - 柱體 (中空粗框) + DIF快線(藍) + MACD慢線(黃)
        {
          name: 'MACD柱體',
          type: 'bar',
          xAxisIndex: 3,
          yAxisIndex: 3,
          data: (macdHist || []).map(m => {
            const isUp = m >= 0;
            return {
              value: m,
              itemStyle: {
                color: 'transparent',
                borderColor: isUp ? '#ef4444' : '#10b981',
                borderWidth: 2
              }
            };
          })
        },
        {
          name: 'DIF快線',
          type: 'line',
          xAxisIndex: 3,
          yAxisIndex: 3,
          data: dif || [],
          smooth: true,
          showSymbol: false,
          lineStyle: { color: '#38bdf8', width: 2 }
        },
        {
          name: 'MACD慢線',
          type: 'line',
          xAxisIndex: 3,
          yAxisIndex: 3,
          data: macdSignal || [],
          smooth: true,
          showSymbol: false,
          lineStyle: { color: '#f59e0b', width: 2 },
          markLine: {
            symbol: 'none',
            data: [
              { yAxis: 0, lineStyle: { color: 'rgba(255,255,255,0.22)', type: 'dashed', width: 1 } }
            ]
          }
        },

        // 8. Pane 4: KD (Grid 4) - K快線(藍) + D慢線(黃)
        {
          name: 'K值',
          type: 'line',
          xAxisIndex: 4,
          yAxisIndex: 4,
          data: kList,
          smooth: true,
          showSymbol: false,
          lineStyle: { color: '#38bdf8', width: 2 }
        },
        {
          name: 'D值',
          type: 'line',
          xAxisIndex: 4,
          yAxisIndex: 4,
          data: dList,
          smooth: true,
          showSymbol: false,
          lineStyle: { color: '#f59e0b', width: 2 },
          markLine: {
            symbol: 'none',
            data: [
              { yAxis: 80, lineStyle: { color: '#ef4444', type: 'dotted', width: 1.2 } },
              { yAxis: 20, lineStyle: { color: '#10b981', type: 'dotted', width: 1.2 } }
            ]
          }
        }
      ]
    };

    // 4. Inject Cross-Grid Indicator Annotations (for DATA_GUIDE)
    if (overlayData && overlayData.indicatorAnnotations) {
      overlayData.indicatorAnnotations.forEach(annot => {
        const targetSeries = option.series.find(s => s.name === annot.seriesName);
        if (targetSeries) {
          if (annot.type === 'markPoint') {
            targetSeries.markPoint = targetSeries.markPoint || { data: [] };
            targetSeries.markPoint.data.push({
              name: annot.label,
              coord: annot.coord,
              symbol: 'pin',
              symbolSize: [22, 52],
              itemStyle: { color: annot.color },
              label: {
                show: true,
                formatter: annot.label,
                position: 'top',
                distance: annot.yOffset ? Math.abs(annot.yOffset) : 10,
                color: annot.color,
                fontSize: 11,
                fontWeight: 'bold',
                backgroundColor: '#1e2330',
                padding: [4, 6],
                borderRadius: 4,
                borderColor: annot.color,
                borderWidth: 1
              }
            });
          } else if (annot.type === 'markArea') {
            targetSeries.markArea = targetSeries.markArea || { data: [] };
            targetSeries.markArea.data.push([
              { 
                yAxis: annot.yAxisStart,
                name: annot.label,
                itemStyle: { color: annot.color },
                label: { show: true, position: 'insideTopLeft', color: '#f43f5e', fontSize: 11, padding: 4 }
              },
              { yAxis: annot.yAxisEnd }
            ]);
          }
        }
      });
    }

    try {
      state.chartInstance.setOption(option, true);
      console.log('[ChartEngine Debug] ✅ setOption 成功完成！ECharts 實際尺寸:', {
        width: state.chartInstance.getWidth(),
        height: state.chartInstance.getHeight()
      });
    } catch (err) {
      console.error('[ChartEngine Debug] ❌ setOption 拋出異常:', err);
    }

    // Helper: 更新圖表各 Pane 左上角懸浮數值 HTML 覆蓋層 (零亂碼、高清晰、支援日/夜模式)
    const hudDefaultColor = isLight ? '#0f172a' : '#cbd5e1';
    const hudLabelColor = isLight ? '#475569' : '#94a3b8';
    const hudShadow = isLight ? 'none' : '0 1px 3px rgba(0,0,0,0.8)';
    const hudSepColor = isLight ? 'rgba(0,0,0,0.2)' : 'rgba(255,255,255,0.25)';
    const fastColor = isLight ? '#0284c7' : '#38bdf8';
    const slowColor = isLight ? '#d97706' : '#f59e0b';

    const updateInChartHUD = (idx) => {
      if (!state.chartInstance || state.chartInstance.isDisposed() || !stockData || !stockData.dates) return;
      if (idx < 0 || idx >= stockData.dates.length) return;

      const chartDom = state.chartInstance.getDom();
      if (!chartDom) return;

      // 確保 chartDom 為 relative 定位以容納 overlay
      if (getComputedStyle(chartDom).position === 'static') {
        chartDom.style.position = 'relative';
      }

      let overlay = chartDom.querySelector('.echart-in-chart-hud-container');
      if (!overlay) {
        overlay = document.createElement('div');
        overlay.className = 'echart-in-chart-hud-container';
        overlay.style.position = 'absolute';
        overlay.style.inset = '0';
        overlay.style.pointerEvents = 'none';
        overlay.style.zIndex = '5';
        chartDom.appendChild(overlay);
      }

      // 保持最新座標位置與日夜主題模式
      if (!overlay.dataset.renderedVersion || overlay.dataset.renderedVersion !== 'v87' || overlay.dataset.themeMode !== (isLight ? 'light' : 'dark')) {
        overlay.dataset.renderedVersion = 'v87';
        overlay.dataset.themeMode = isLight ? 'light' : 'dark';
        overlay.innerHTML = `
          <div id="ichud-pane-0" style="position:absolute; left:6.2%; top:8.5%; font-size:12px; font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif; color:${hudDefaultColor}; white-space:nowrap; text-shadow:${hudShadow}; line-height:1.2;"></div>
          <div id="ichud-pane-1" style="position:absolute; left:6.2%; top:35.0%; font-size:12px; font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif; color:${hudDefaultColor}; white-space:nowrap; text-shadow:${hudShadow}; line-height:1.2;"></div>
          <div id="ichud-pane-2" style="position:absolute; left:6.2%; top:50.0%; font-size:12px; font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif; color:${hudDefaultColor}; white-space:nowrap; text-shadow:${hudShadow}; line-height:1.2;"></div>
          <div id="ichud-pane-3" style="position:absolute; left:6.2%; top:65.0%; font-size:12px; font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif; color:${hudDefaultColor}; white-space:nowrap; text-shadow:${hudShadow}; line-height:1.2;"></div>
          <div id="ichud-pane-4" style="position:absolute; left:6.2%; top:80.0%; font-size:12px; font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif; color:${hudDefaultColor}; white-space:nowrap; text-shadow:${hudShadow}; line-height:1.2;"></div>
        `;
      }

      const d = stockData.dates[idx];
      const c = stockData.candles && stockData.candles[idx];
      const elP0 = overlay.querySelector('#ichud-pane-0');
      const elP1 = overlay.querySelector('#ichud-pane-1');
      const elP2 = overlay.querySelector('#ichud-pane-2');
      const elP3 = overlay.querySelector('#ichud-pane-3');
      const elP4 = overlay.querySelector('#ichud-pane-4');

      // Pane 0: K線與均線
      if (elP0 && c) {
        const o = c[0], cl = c[1], l = c[2], h = c[3];
        const prevCl = idx > 0 && stockData.candles[idx - 1] ? stockData.candles[idx - 1][1] : o;
        const diff = cl - prevCl;
        const pct = prevCl ? (diff / prevCl * 100) : 0;
        const upColor = '#ef4444';
        const downColor = '#10b981';
        const color = diff >= 0 ? upColor : downColor;
        const sign = diff >= 0 ? '+' : '';

        const m5 = stockData.ma5 && stockData.ma5[idx] != null ? `<span style="color:#f59e0b; font-weight:bold;">${Number(stockData.ma5[idx]).toFixed(2)}</span>` : '—';
        const m10 = stockData.ma10 && stockData.ma10[idx] != null ? `<span style="color:#3b82f6; font-weight:bold;">${Number(stockData.ma10[idx]).toFixed(2)}</span>` : '—';
        const m20 = stockData.ma20 && stockData.ma20[idx] != null ? `<span style="color:#ec4899; font-weight:bold;">${Number(stockData.ma20[idx]).toFixed(2)}</span>` : '—';
        const m60 = stockData.ma60 && stockData.ma60[idx] != null ? `<span style="color:#10b981; font-weight:bold;">${Number(stockData.ma60[idx]).toFixed(2)}</span>` : '—';
        const m120 = stockData.ma120 && stockData.ma120[idx] != null ? `<span style="color:#8b5cf6; font-weight:bold;">${Number(stockData.ma120[idx]).toFixed(2)}</span>` : '—';

        elP0.innerHTML = `<span style="color:${fastColor}; font-weight:bold;">${d}</span> <span style="color:${hudLabelColor};">開:</span><span style="font-weight:bold;">${o.toFixed(2)}</span> <span style="color:${hudLabelColor};">高:</span><span style="font-weight:bold;">${h.toFixed(2)}</span> <span style="color:${hudLabelColor};">低:</span><span style="font-weight:bold;">${l.toFixed(2)}</span> <span style="color:${hudLabelColor};">收:</span><span style="color:${color}; font-weight:bold;">${cl.toFixed(2)}</span> <span style="color:${hudLabelColor};">漲跌:</span><span style="color:${color}; font-weight:bold;">${sign}${diff.toFixed(2)} (${sign}${pct.toFixed(2)}%)</span> <span style="color:${hudSepColor}; margin:0 4px;">|</span> <span style="color:${hudLabelColor};">MA5:</span>${m5} <span style="color:${hudLabelColor};">MA10:</span>${m10} <span style="color:${hudLabelColor};">MA20:</span>${m20} <span style="color:${hudLabelColor};">MA60:</span>${m60} <span style="color:${hudLabelColor};">MA120:</span>${m120}`;
      }

      // Pane 1: 成交量與量均線
      if (elP1) {
        const v = stockData.volumes && stockData.volumes[idx];
        const mv5Val = stockData.vma5 && stockData.vma5[idx] != null ? `<span style="color:${fastColor}; font-weight:bold;">${Number(stockData.vma5[idx]).toLocaleString()}</span>` : '—';
        const mv20Val = stockData.vma20 && stockData.vma20[idx] != null ? `<span style="color:${slowColor}; font-weight:bold;">${Number(stockData.vma20[idx]).toLocaleString()}</span>` : '—';
        elP1.innerHTML = `<span style="color:${hudLabelColor};">成交量:</span> <span style="color:${fastColor}; font-weight:bold;">${v != null ? Number(v).toLocaleString() + ' 張' : '—'}</span> <span style="color:${hudSepColor}; margin:0 4px;">|</span> <span style="color:${hudLabelColor};">MV5:</span>${mv5Val} <span style="color:${hudLabelColor};">MV20:</span>${mv20Val}`;
      }

      // Pane 2: RSI
      if (elP2) {
        const r6Val = stockData.rsi6 && stockData.rsi6[idx] != null ? `<span style="color:${fastColor}; font-weight:bold;">${Number(stockData.rsi6[idx]).toFixed(2)}</span>` : '—';
        const r12Val = stockData.rsi12 && stockData.rsi12[idx] != null ? `<span style="color:${slowColor}; font-weight:bold;">${Number(stockData.rsi12[idx]).toFixed(2)}</span>` : '—';
        elP2.innerHTML = `<span style="color:${hudLabelColor};">RSI(6):</span>${r6Val} <span style="color:${hudLabelColor}; margin-left:8px;">RSI(12):</span>${r12Val}`;
      }

      // Pane 3: MACD
      if (elP3) {
        const difVal = stockData.dif && stockData.dif[idx] != null ? `<span style="color:${fastColor}; font-weight:bold;">${Number(stockData.dif[idx]).toFixed(2)}</span>` : '—';
        const macdVal = stockData.macdSignal && stockData.macdSignal[idx] != null ? `<span style="color:${slowColor}; font-weight:bold;">${Number(stockData.macdSignal[idx]).toFixed(2)}</span>` : '—';
        const hist = stockData.macdHist && stockData.macdHist[idx];
        const histColor = (hist != null && hist >= 0) ? '#ef4444' : '#10b981';
        const histVal = hist != null ? `<span style="color:${histColor}; font-weight:bold;">${Number(hist).toFixed(2)}</span>` : '—';
        elP3.innerHTML = `<span style="color:${hudLabelColor};">DIF快線:</span>${difVal} <span style="color:${hudLabelColor}; margin-left:8px;">MACD慢線:</span>${macdVal} <span style="color:${hudLabelColor}; margin-left:8px;">OSC柱體:</span>${histVal}`;
      }

      // Pane 4: KD
      if (elP4) {
        const kVal = stockData.kList && stockData.kList[idx] != null ? `<span style="color:${fastColor}; font-weight:bold;">${Number(stockData.kList[idx]).toFixed(2)}</span>` : '—';
        const dVal = stockData.dList && stockData.dList[idx] != null ? `<span style="color:${slowColor}; font-weight:bold;">${Number(stockData.dList[idx]).toFixed(2)}</span>` : '—';
        elP4.innerHTML = `<span style="color:${hudLabelColor};">K(9,3):</span>${kVal} <span style="color:${hudLabelColor}; margin-left:8px;">D(9,3):</span>${dVal}`;
      }
    };

    // 同步十字游標焦點至圖表內頂部 HUD
    if (state.axisPointerHandler) {
      state.chartInstance.off('updateAxisPointer', state.axisPointerHandler);
    }
    state.axisPointerHandler = (event) => {
      if (event.axesInfo && event.axesInfo.length) {
        const axisInfo = event.axesInfo[0];
        if (axisInfo && axisInfo.value != null) {
          const dataIndex = typeof axisInfo.value === 'number' ? axisInfo.value : dates.indexOf(axisInfo.value);
          if (dataIndex >= 0 && dataIndex < dates.length) {
            updateInChartHUD(dataIndex);
          }
        }
      }
    };
    state.chartInstance.on('updateAxisPointer', state.axisPointerHandler);

    // 預設呈現最新收盤日數據
    if (dates && dates.length) {
      const lastIdx = dates.length - 1;
      updateInChartHUD(lastIdx);
    }

    if (!state.hudMouseLeaveBound) {
      state.hudMouseLeaveBound = true;
      dom.addEventListener('mouseleave', () => {
        if (stockData && stockData.dates && stockData.dates.length) {
          const lastIdx = stockData.dates.length - 1;
          updateInChartHUD(lastIdx);
        }
      });
    }

    // 多階段延遲觸發 resize，保證容器切換完成後能正確取得寬高並繪製
    requestAnimationFrame(() => this.resize(dom));
    setTimeout(() => this.resize(dom), 60);
    setTimeout(() => this.resize(dom), 200);
  },

  _attachZoomFix(dom) {
    if (!dom) return null;

    const events = [
      'pointermove', 'pointerdown', 'pointerup',
      'mousemove', 'mousedown', 'mouseup',
      'click', 'dblclick', 'contextmenu',
      'wheel', 'mousewheel',
      'touchstart', 'touchmove'
    ];

    const correctCoords = (e) => {
      const zoom = parseFloat(document.body.style.zoom) || 1;
      if (Math.abs(zoom - 1) < 0.001) return;
      const rect = dom.getBoundingClientRect();
      const touch = (e.touches && e.touches[0]) || (e.changedTouches && e.changedTouches[0]);
      const clientX = touch ? touch.clientX : e.clientX;
      const clientY = touch ? touch.clientY : e.clientY;
      if (clientX == null || clientY == null) return;
      const zrX = (clientX - rect.left) / zoom;
      const zrY = (clientY - rect.top) / zoom;
      try {
        Object.defineProperty(e, 'zrX', { value: zrX, configurable: true, writable: true });
        Object.defineProperty(e, 'zrY', { value: zrY, configurable: true, writable: true });
      } catch (_) {
        e.zrX = zrX;
        e.zrY = zrY;
      }

      if (e.type === 'wheel' || e.type === 'mousewheel' || e.type === 'DOMMouseScroll') {
        let delta = 0;
        if (e.wheelDelta != null && e.wheelDelta !== 0) {
          delta = e.wheelDelta / 120;
        } else if (e.deltaY != null && e.deltaY !== 0) {
          delta = e.deltaY > 0 ? -1 : 1;
        } else if (e.detail != null && e.detail !== 0) {
          delta = -(e.detail / 3);
        }
        try {
          Object.defineProperty(e, 'zrDelta', { value: delta, configurable: true, writable: true });
        } catch (_) {
          e.zrDelta = delta;
        }
      }
    };

    events.forEach(evtName => {
      dom.addEventListener(evtName, correctCoords, { capture: true });
    });

    return () => {
      events.forEach(evtName => {
        dom.removeEventListener(evtName, correctCoords, { capture: true });
      });
    };
  },

  resize(container) {
    if (container) {
      const dom = typeof container === 'string' ? document.querySelector(container) : container;
      const state = dom ? this.instances.get(dom) : null;
      if (state && state.chartInstance && !state.chartInstance.isDisposed()) {
        const w = dom.clientWidth || (dom.parentElement ? dom.parentElement.clientWidth : 0);
        const h = dom.clientHeight || 850;
        if (w > 0 && h > 0) {
          state.chartInstance.resize({ width: w, height: h });
        } else {
          state.chartInstance.resize();
        }
      }
      return;
    }
    this.instances.forEach((state, dom) => {
      if (state && state.chartInstance && !state.chartInstance.isDisposed()) {
        if (dom.isConnected && dom.offsetWidth > 0) {
          const w = dom.clientWidth || (dom.parentElement ? dom.parentElement.clientWidth : 0);
          const h = dom.clientHeight || 850;
          if (w > 0 && h > 0) {
            state.chartInstance.resize({ width: w, height: h });
          } else {
            state.chartInstance.resize();
          }
        }
      }
    });
  },

  refreshTheme() {
    const isLight = document.documentElement.getAttribute('data-theme') === 'light';
    const targetTheme = isLight ? 'light' : 'dark';
    this.instances.forEach((state, dom) => {
      if (!dom || !dom.isConnected) return;
      if (state && state.lastStockData) {
        // 重新調用 render 以新主題初始化與套用配色
        this.render(dom, state.lastStockData, state.lastOverlayData, state.lastDisplayToggles || {});
      }
    });
  },

  setTheme(theme) {
    if (theme) {
      document.documentElement.setAttribute('data-theme', theme);
      document.body.setAttribute('data-theme', theme);
    }
    this.refreshTheme();
  },

  destroy(container) {
    if (container) {
      const dom = typeof container === 'string' ? document.querySelector(container) : container;
      const state = dom ? this.instances.get(dom) : null;
      if (state) {
        if (state.zoomCleanup) state.zoomCleanup();
        if (state.chartInstance && !state.chartInstance.isDisposed()) {
          state.chartInstance.dispose();
        }
        this.instances.delete(dom);
      }
      return;
    }
    this.instances.forEach((state, dom) => {
      if (state) {
        if (state.zoomCleanup) state.zoomCleanup();
        if (state.chartInstance && !state.chartInstance.isDisposed()) {
          state.chartInstance.dispose();
        }
      }
    });
    this.instances.clear();
    this.activeDom = null;
  }
};
