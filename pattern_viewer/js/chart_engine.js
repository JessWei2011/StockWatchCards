/**
 * ChartEngine Module
 * Renders 5-pane synchronized interactive charts (K-line, Volume, RSI, MACD, KD)
 * using Apache ECharts with AI pattern visual vector overlay and teaching badges.
 */

window.ChartEngine = {
  chartInstance: null,

  /**
   * Initialize or update the 5-pane chart
   * @param {string|HTMLElement} container 
   * @param {Object} stockData 
   * @param {Object} overlayData 
   * @param {Object} displayToggles { showBoll: true, showMa: true }
   */
  render(container, stockData, overlayData, displayToggles = { showBoll: true, showMa: true }) {
    if (!stockData || !stockData.dates || stockData.dates.length === 0) {
      console.warn('ChartEngine: stockData is empty');
      return;
    }

    const dom = typeof container === 'string' ? document.querySelector(container) : container;
    if (!dom) return;

    if (!this.chartInstance) {
      this.chartInstance = echarts.init(dom, 'dark');
      window.addEventListener('resize', () => this.chartInstance.resize());
    }

    // Default to the most recent 10 trading days so candles aren't squished on a 50-day chart.
    // Only reset the zoom when the stock actually changes — re-renders for the same stock (toggling
    // BOLL/MA, switching pattern view) keep whatever zoom range the user has manually set.
    const total = stockData.dates.length;
    const stockKey = `${stockData.title}|${total}`;
    let zoomStart, zoomEnd;
    if (this._lastStockKey === stockKey) {
      const prevOption = this.chartInstance.getOption();
      const prevZoom = prevOption && prevOption.dataZoom && prevOption.dataZoom[0];
      if (prevZoom) { zoomStart = prevZoom.start; zoomEnd = prevZoom.end; }
    }
    if (zoomStart == null) {
      const defaultVisibleDays = 14;
      zoomStart = total > defaultVisibleDays ? Math.max(0, (1 - defaultVisibleDays / total) * 100) : 0;
      zoomEnd = 100;
    }
    this._lastStockKey = stockKey;

    const {
      dates,
      candles,
      volumes,
      ma5,
      rsi,
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
      // 1. Pivot points P1, P2, P3, P4 badges
      // Pivots that land on the same date (or very close together) get their pin graphics stacked
      // right on top of each other. Instead of moving the pin itself off its real coordinate, keep
      // the pin exact and stagger the LABEL — alternating above/below with growing distance — so
      // overlapping callouts stay readable instead of mashing into unreadable text.
      if (overlayData.pivots) {
        const dateGroups = {};
        overlayData.pivots.forEach(p => { (dateGroups[p.date] = dateGroups[p.date] || []).push(p); });

        overlayData.pivots.forEach(pivot => {
          const group = dateGroups[pivot.date];
          const posInGroup = group.indexOf(pivot);
          const clustered = group.length > 1;
          const position = clustered ? (posInGroup % 2 === 0 ? 'top' : 'bottom') : 'top';
          const distance = clustered ? 20 + Math.floor(posInGroup / 2) * 26 : 12;
          // Staggering the label alone doesn't stop the pin HEADS from stacking on top of each
          // other when several pivots share the same date — the marker itself still sits on the
          // exact same pixel. Fan the pins out sideways (in px) around their real x position so
          // the heads separate; the tip stays visually anchored near the candle, just nudged.
          const xOffset = clustered ? (posInGroup - (group.length - 1) / 2) * 24 : 0;

          markPoints.push({
            name: pivot.label,
            coord: [pivot.date, pivot.price],
            value: pivot.tag,
            symbol: 'pin',
            // Narrow + tall (instead of a fat 40x40 square) so the pin's point stretches further
            // away from the candle before the round head appears — keeps wicks unobstructed and
            // reduces head-to-head overlap when several pivots land on the same date.
            symbolSize: [22, 52],
            symbolOffset: [xOffset, 0],
            itemStyle: {
              color: overlayData.color || '#f59e0b',
              shadowColor: overlayData.color || '#f59e0b',
              shadowBlur: 10
            },
            label: {
              show: true,
              formatter: `${pivot.tag}\n$${pivot.price}`,
              position,
              distance,
              fontSize: 10,
              fontWeight: 'bold',
              color: '#ffffff',
              backgroundColor: 'rgba(15, 17, 23, 0.85)',
              padding: [2, 5],
              borderRadius: 3
            }
          });
        });
      }

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
        axisPointer: {
          type: 'cross',
          link: [{ xAxisIndex: 'all' }] // Crosshair sync across all 5 panes!
        },
        backgroundColor: 'rgba(22, 25, 34, 0.95)',
        borderColor: '#232734',
        borderWidth: 1,
        textStyle: { color: '#e2e8f0', fontSize: 12 }
      },
      axisPointer: {
        link: [{ xAxisIndex: 'all' }],
        label: { backgroundColor: '#3b82f6' }
      },
      legend: {
        data: ['K線', 'MA5', 'MA10', 'MA20', 'BOLL上軌', 'BOLL中軌', 'BOLL下軌', '成交量', 'RSI', 'MACD', 'K值', 'D值'],
        top: 4,
        textStyle: { color: '#94a3b8', fontSize: 11 },
        selected: {
          'BOLL上軌': displayToggles.showBoll,
          'BOLL中軌': displayToggles.showBoll,
          'BOLL下軌': displayToggles.showBoll,
          'MA5': displayToggles.showMa,
          'MA10': displayToggles.showMa,
          'MA20': displayToggles.showMa
        }
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
            color: '#ef4444',        // 紅漲
            color0: '#10b981',       // 綠跌
            borderColor: '#ef4444',
            borderColor0: '#10b981'
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
        // 3. BOLL Mid (Grid 0)
        {
          name: 'BOLL中軌',
          type: 'line',
          xAxisIndex: 0,
          yAxisIndex: 0,
          data: bollMid,
          smooth: true,
          showSymbol: false,
          lineStyle: { color: '#6366f1', width: 1 }
        },
        // 4. BOLL Lower (Grid 0)
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
          data: volumes.map((v, i) => ({
            value: v,
            itemStyle: {
              color: candles[i][1] >= candles[i][0] ? '#ef4444' : '#10b981'
            }
          }))
        },

        // 6. Pane 2: RSI (Grid 2)
        {
          name: 'RSI',
          type: 'line',
          xAxisIndex: 2,
          yAxisIndex: 2,
          data: rsi,
          showSymbol: false,
          lineStyle: { color: '#06b6d4', width: 1.5 },
          markLine: {
            symbol: 'none',
            data: [
              { yAxis: 70, lineStyle: { color: '#ef4444', type: 'dotted' } },
              { yAxis: 30, lineStyle: { color: '#10b981', type: 'dotted' } }
            ]
          }
        },

        // 7. Pane 3: MACD (Grid 3)
        {
          name: 'MACD',
          type: 'bar',
          xAxisIndex: 3,
          yAxisIndex: 3,
          data: macdHist.map(m => ({
            value: m,
            itemStyle: {
              color: m >= 0 ? '#ef4444' : '#10b981'
            }
          }))
        },

        // 8. Pane 4: KD (Grid 4)
        {
          name: 'K值',
          type: 'line',
          xAxisIndex: 4,
          yAxisIndex: 4,
          data: kList,
          showSymbol: false,
          lineStyle: { color: '#f59e0b', width: 1.5 }
        },
        {
          name: 'D值',
          type: 'line',
          xAxisIndex: 4,
          yAxisIndex: 4,
          data: dList,
          showSymbol: false,
          lineStyle: { color: '#3b82f6', width: 1.5 },
          markLine: {
            symbol: 'none',
            data: [
              { yAxis: 80, lineStyle: { color: '#ef4444', type: 'dotted' } },
              { yAxis: 20, lineStyle: { color: '#10b981', type: 'dotted' } }
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

    this.chartInstance.setOption(option, true);
  }
};
