/**
 * ChartEngine Module
 * Renders 5-pane synchronized interactive charts (K-line, Volume, RSI, MACD, KD)
 * using Apache ECharts with AI pattern visual vector overlay and teaching badges.
 */

window.ChartEngine = {
  chartInstance: null,
  _resizeHandler: null,

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
    const domHeight = Math.max(dom.clientHeight || dom.offsetHeight || 0, 650);

    console.log('[ChartEngine Debug] 🚀 ChartEngine.render() 開始執行', {
      stock: stockData.title,
      datesCount: stockData.dates ? stockData.dates.length : 0,
      domWidth,
      domHeight,
      rawClientWidth: dom.clientWidth,
      rawClientHeight: dom.clientHeight,
      parentWidth: parentW
    });

    if (this.chartInstance && this.chartInstance.getDom() !== dom) {
      this.destroy();
    }

    if (!this.chartInstance || this.chartInstance.isDisposed()) {
      this.chartInstance = echarts.init(dom, 'dark', {
        width: domWidth,
        height: domHeight
      });
      this._attachZoomFix(dom);
      this._resizeHandler = () => this.resize();
      window.addEventListener('resize', this._resizeHandler);
    } else {
      this.chartInstance.resize({
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
    let zoomStart, zoomEnd;
    let prevLegendSelected = null;
    if (this._lastStockKey === stockKey) {
      const prevOption = this.chartInstance.getOption();
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
    this._lastStockKey = stockKey;

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
    const option = {
      backgroundColor: '#161922',
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
        top: 4,
        textStyle: { color: '#94a3b8', fontSize: 11 },
        selected: Object.assign({
          'K線': true,
          'MA5': displayToggles.showMa !== false,
          'MA10': displayToggles.showMa !== false,
          'MA20': displayToggles.showMa !== false,
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
        }, prevLegendSelected || {})
      },
      grid: [
        { left: '6%', right: '4%', top: '7%', height: '35%' },   // Pane 0: K-Line
        { left: '6%', right: '4%', top: '45%', height: '10%' },  // Pane 1: VOL
        { left: '6%', right: '4%', top: '58%', height: '10%' },  // Pane 2: RSI
        { left: '6%', right: '4%', top: '71%', height: '10%' },  // Pane 3: MACD
        { left: '6%', right: '4%', top: '84%', height: '10%' }   // Pane 4: KD
      ],
      xAxis: [
        { type: 'category', data: dates, gridIndex: 0, axisLine: { lineStyle: { color: '#2d3345' } } },
        { type: 'category', data: dates, gridIndex: 1, axisLabel: { show: false }, axisLine: { lineStyle: { color: '#2d3345' } } },
        { type: 'category', data: dates, gridIndex: 2, axisLabel: { show: false }, axisLine: { lineStyle: { color: '#2d3345' } } },
        { type: 'category', data: dates, gridIndex: 3, axisLabel: { show: false }, axisLine: { lineStyle: { color: '#2d3345' } } },
        { type: 'category', data: dates, gridIndex: 4, axisLine: { lineStyle: { color: '#2d3345' } } }
      ],
      yAxis: [
        // Pane 0: K-line Price
        { scale: true, gridIndex: 0, axisLine: { lineStyle: { color: '#2d3345' } }, splitLine: { lineStyle: { color: '#1e2330' } } },
        // Pane 1: Volume
        { scale: true, gridIndex: 1, axisLabel: { show: false }, splitLine: { show: false } },
        // Pane 2: RSI (0-100)
        { min: 0, max: 100, gridIndex: 2, splitLine: { lineStyle: { color: '#1e2330' } } },
        // Pane 3: MACD
        { scale: true, gridIndex: 3, splitLine: { lineStyle: { color: '#1e2330' } } },
        // Pane 4: KD (0-100)
        { min: 0, max: 100, gridIndex: 4, splitLine: { lineStyle: { color: '#1e2330' } } }
      ],
      dataZoom: [
        { type: 'inside', xAxisIndex: [0, 1, 2, 3, 4], start: zoomStart, end: zoomEnd },
        { type: 'slider', xAxisIndex: [0, 1, 2, 3, 4], bottom: '1%', height: 16, borderColor: '#232734', fillerColor: 'rgba(59, 130, 246, 0.2)', start: zoomStart, end: zoomEnd }
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
              { yAxis: 80, lineStyle: { color: '#ef4444', type: 'dashed', width: 1 }, label: { show: true, position: 'insideEndTop', formatter: '80 極度過熱' } },
              { yAxis: 70, lineStyle: { color: 'rgba(239, 68, 68, 0.5)', type: 'dotted', width: 1 }, label: { show: false } },
              { yAxis: 50, lineStyle: { color: 'rgba(148, 163, 184, 0.35)', type: 'dashed', width: 1 }, label: { show: false } },
              { yAxis: 30, lineStyle: { color: 'rgba(16, 185, 129, 0.5)', type: 'dotted', width: 1 }, label: { show: false } },
              { yAxis: 20, lineStyle: { color: '#10b981', type: 'dashed', width: 1 }, label: { show: true, position: 'insideEndBottom', formatter: '20 極度超跌' } }
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
      this.chartInstance.setOption(option, true);
      console.log('[ChartEngine Debug] ✅ setOption 成功完成！ECharts 實際尺寸:', {
        width: this.chartInstance.getWidth(),
        height: this.chartInstance.getHeight()
      });
    } catch (err) {
      console.error('[ChartEngine Debug] ❌ setOption 拋出異常:', err);
    }

    // 同步十字游標焦點至右側「🎯 游標焦點即時數據看板」
    if (this._axisPointerHandler) {
      this.chartInstance.off('updateAxisPointer', this._axisPointerHandler);
    }
    this._axisPointerHandler = (event) => {
      if (event.axesInfo && event.axesInfo.length) {
        const axisInfo = event.axesInfo[0];
        if (axisInfo && axisInfo.value != null) {
          const dataIndex = typeof axisInfo.value === 'number' ? axisInfo.value : dates.indexOf(axisInfo.value);
          if (dataIndex >= 0 && dataIndex < dates.length) {
            if (window.updateFocusHUD) window.updateFocusHUD(dataIndex, stockData);
            if (window.parent && window.parent.updateFocusHUD) window.parent.updateFocusHUD(dataIndex, stockData);
          }
        }
      }
    };
    this.chartInstance.on('updateAxisPointer', this._axisPointerHandler);

    // 預設呈現最新收盤日數據
    if (dates && dates.length) {
      const lastIdx = dates.length - 1;
      if (window.updateFocusHUD) window.updateFocusHUD(lastIdx, stockData);
      if (window.parent && window.parent.updateFocusHUD) window.parent.updateFocusHUD(lastIdx, stockData);
    }

    if (!this._hudMouseLeaveBound) {
      this._hudMouseLeaveBound = true;
      const chartDom = document.getElementById('echart-main');
      if (chartDom) {
        chartDom.addEventListener('mouseleave', () => {
          if (this.currentStockData && this.currentStockData.dates && this.currentStockData.dates.length) {
            const lastIdx = this.currentStockData.dates.length - 1;
            if (window.updateFocusHUD) window.updateFocusHUD(lastIdx, this.currentStockData);
            if (window.parent && window.parent.updateFocusHUD) window.parent.updateFocusHUD(lastIdx, this.currentStockData);
          }
        });
      }
    }
    this.currentStockData = stockData;

    // 多階段延遲觸發 resize，保證容器切換完成後能正確取得寬高並繪製
    requestAnimationFrame(() => this.resize());
    setTimeout(() => this.resize(), 60);
    setTimeout(() => this.resize(), 200);
  },

  _attachZoomFix(dom) {
    if (this._zoomFixCleanup) {
      this._zoomFixCleanup();
      this._zoomFixCleanup = null;
    }
    if (!dom) return;

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

      // ECharts (ZRender) requires zrDelta on wheel events to trigger InsideZoom.
      // When zrX is manually assigned, ZRender skips normalizeEvent, so we must also provide zrDelta.
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

    this._zoomFixCleanup = () => {
      events.forEach(evtName => {
        dom.removeEventListener(evtName, correctCoords, { capture: true });
      });
    };
  },

  resize() {
    if (this.chartInstance && !this.chartInstance.isDisposed()) {
      const dom = this.chartInstance.getDom();
      if (dom) {
        const w = dom.clientWidth || (dom.parentElement ? dom.parentElement.clientWidth : 0);
        const h = dom.clientHeight || 650;
        if (w > 0 && h > 0) {
          this.chartInstance.resize({ width: w, height: h });
          return;
        }
      }
      this.chartInstance.resize();
    }
  },

  destroy() {
    if (this._zoomFixCleanup) {
      this._zoomFixCleanup();
      this._zoomFixCleanup = null;
    }
    if (this._resizeHandler) {
      window.removeEventListener('resize', this._resizeHandler);
      this._resizeHandler = null;
    }
    if (this.chartInstance && !this.chartInstance.isDisposed()) {
      this.chartInstance.dispose();
    }
    this.chartInstance = null;
    this._lastStockKey = null;
  }
};
