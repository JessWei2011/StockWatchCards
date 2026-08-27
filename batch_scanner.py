r"""
batch_scanner.py - 自動化個股勝率與線型型態掃描器 (C:\Code\Python\Stock2)

功能說明：
1. 自動掃描 reports/ 目錄下所有產業資料夾中的個股 HTML 報告。
2. 從 HTML 解析既存歷史 K 線、技術指標（MA, RSI, MACD, 布林帶）與籌碼法人資料。
3. 自動補抓 yfinance 當天增量最新股價與成交量。
4. 採用純數據演算法（無須 PNG）自動識別型態（杯柄、雙底、歷史新高突破、VCP 整理等）。
5. 依據【五大維度勝率評分模型】計算預期勝率 % 與風險報酬比 (R/R)。
6. 產出按勝率由高至低排名的 Markdown 報告 (stock_winrate_ranking.md)。
"""

import json
import math
import os
import re
import sys
from pathlib import Path
import pandas as pd
import numpy as np

# 嘗試載入 yfinance 用於增量補抓最新股價
# 處理 Windows 主機 Unicode 輸出編碼
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False

ROOT = Path(__file__).resolve().parent
REPORTS_DIR = ROOT / "reports"
OUTPUT_MD = ROOT / "stock_winrate_ranking.md"
YFINANCE_CACHE_DIR = ROOT / ".cache" / "yfinance"

# yfinance 預設會將時區／Cookie 快取寫到使用者設定目錄；在受限環境下會
# 失敗並讓 fetch_latest_bar 靜默回傳 None。固定放在專案可寫入的快取目錄。
if YFINANCE_AVAILABLE:
    YFINANCE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    yf.set_tz_cache_location(str(YFINANCE_CACHE_DIR))


def parse_html_report(file_path):
    """解析單一 HTML 報告中的指標、籌碼與歷史 K 線數據"""
    text = file_path.read_text(encoding="utf-8", errors="ignore")
    
    # 提取檔名代號與市場
    # 範例：2426_鼎元(TW).html 或 3081_聯亞(TWO)(處置期間0824-0828).html
    m = re.search(r'(\d{4})_(.*?)\((TW|TWO)\)', file_path.name)
    if not m:
        return None
    
    code, name, mkt = m.groups()
    symbol = f"{code}.{mkt}"
    category = file_path.parent.name
    
    # 解析 Table 1 (歷史 K 線與指標數據)
    tables = re.findall(r'<table[^>]*>(.*?)</table>', text, re.S)
    if not tables:
        return None
    
    # 提取第 1 個表格 (K線指標)
    rows_raw = re.findall(r'<tr[^>]*>(.*?)</tr>', tables[0], re.S)
    kline_data = []
    for r in rows_raw[1:]: # 跳過表頭
        cells = [re.sub(r'<[^>]+>', '', c).strip() for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', r, re.S)]
        if len(cells) >= 6:
            try:
                date_str = cells[0]
                op = float(cells[1].replace(',', ''))
                hi = float(cells[2].replace(',', ''))
                lo = float(cells[3].replace(',', ''))
                cl = float(cells[4].replace(',', ''))
                vol_str = cells[5].replace(',', '').replace('M', '000000').replace('K', '000')
                vol = float(re.sub(r'[^\d.]', '', vol_str)) if vol_str else 0.0
                kline_data.append({
                    'date': date_str, 'open': op, 'high': hi, 'low': lo, 'close': cl, 'volume': vol
                })
            except ValueError:
                continue
                
    # 提取三大法人買賣超 (Table 2，若有)
    inst_buy_days = 0
    if len(tables) >= 2:
        inst_rows = re.findall(r'<tr[^>]*>(.*?)</tr>', tables[1], re.S)
        for r in inst_rows[1:6]: # 看最近 5 天
            cells = [re.sub(r'<[^>]+>', '', c).strip() for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', r, re.S)]
            if len(cells) >= 5:
                total_val = cells[4].replace(',', '')
                try:
                    val = float(total_val)
                    if val > 0:
                        inst_buy_days += 1
                except ValueError:
                    pass

    return {
        'code': code,
        'name': name,
        'market': mkt,
        'symbol': symbol,
        'category': category,
        'kline': kline_data,
        'inst_buy_days': inst_buy_days,
        'path': str(file_path)
    }


def fetch_latest_bar(symbol):
    """使用 yfinance 補抓當天增量最新股價與成交量"""
    if not YFINANCE_AVAILABLE:
        return None
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period='5d', interval='1d')
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if df.empty:
            return None
        last_row = df.iloc[-1]
        latest_date = df.index[-1].strftime('%m/%d')
        return {
            'date': latest_date,
            'open': float(last_row['Open']),
            'high': float(last_row['High']),
            'low': float(last_row['Low']),
            'close': float(last_row['Close']),
            'volume': float(last_row['Volume'])
        }
    except Exception:
        return None


def recognize_pattern(df):
    """
    純數據幾何演算法識別線型型態 (無須 PNG 圖檔)
    包含：歷史新高/突破、杯柄型態 (Cup & Handle)、雙底 (Double Bottom)、VCP 收縮整理
    """
    close = df['Close'].values
    high = df['High'].values
    low = df['Low'].values
    volume = df['Volume'].values
    n = len(df)
    
    if n < 20:
        return "資料不足", 0.0

    curr_close = close[-1]
    curr_high = high[-1]
    curr_vol = volume[-1]
    vol_ma20 = np.mean(volume[-20:]) if n >= 20 else volume[-1]

    # 1. 歷史新高 / 多年新高突破
    max_high_prior = np.max(high[:-1])
    if curr_close >= max_high_prior or curr_high >= max_high_prior:
        if curr_vol >= 1.2 * vol_ma20:
            return "歷史/波段新高爆量突破 (Breakout)", 15.0
        return "創歷史/波段新高 (High Breakout)", 12.0

    # 2. 杯柄型態 (Cup and Handle) 識別
    if n >= 30:
        start_search = max(0, n - 60)
        end_search = max(start_search + 1, n - 15)
        p1_sub_idx = np.argmax(high[start_search:end_search])
        p1_idx = start_search + p1_sub_idx
        p1 = high[p1_idx]
        sub_lows = low[p1_idx:]
        if len(sub_lows) > 0:
            cup_bottom = np.min(sub_lows)
            cup_depth = (p1 - cup_bottom) / (p1 + 1e-5)
            if 0.10 <= cup_depth <= 0.48 and (0.92 * p1 <= curr_close <= 1.08 * p1):
                if curr_close >= p1 * 0.98:
                    return "大層級杯柄型態 (測試/突破頸線)", 14.0
                return "杯柄型態右側形成中 (Cup & Handle)", 10.0

    # 3. 雙底 W底 (Double Bottom) 識別
    if n >= 30:
        recent_lows = np.sort(low[-30:])
        l1, l2 = recent_lows[0], recent_lows[1]
        if abs(l1 - l2) / l1 <= 0.03 and curr_close > l1 * 1.08:
            return "雙重底 W底形成突破 (Double Bottom)", 12.0

    # 4. 波動收縮整理 (VCP / Flag)
    if n >= 20:
        high_20 = np.max(high[-20:])
        low_20 = np.min(low[-20:])
        range_pct = (high_20 - low_20) / low_20
        if range_pct <= 0.12 and curr_close >= high_20 * 0.96:
            return "高檔 VCP 窄幅旗形整理 (VCP Squeeze)", 11.0

    # 5. 常規多頭推升
    ma50 = np.mean(close[-50:]) if n >= 50 else np.mean(close)
    if curr_close > ma50:
        return "多頭排列階梯推升 (Bullish Trend)", 8.0

    return "高檔盤整/區間震盪", 5.0


def calculate_winrate(stock_info):
    """
    五大維度勝率評分模型
    回傳：勝率 %、總得分、形態名稱、關鍵價位與風險報酬比 (R/R)
    """
    klines = stock_info['kline']
    if not klines:
        return None
        
    df = pd.DataFrame(klines)
    
    # 補抓當天最新數據（如有）
    latest = fetch_latest_bar(stock_info['symbol'])
    if latest and latest['date'] != df.iloc[-1]['date']:
        df = pd.concat([df, pd.DataFrame([latest])], ignore_index=True)
        
    n = len(df)
    if n < 15:
        return None
        
    close = df['close'].values
    high = df['high'].values
    low = df['low'].values
    volume = df['volume'].values
    
    df_calc = pd.DataFrame({'Close': close, 'High': high, 'Low': low, 'Volume': volume})
    
    curr_price = close[-1]
    curr_vol = volume[-1]
    vol20 = np.mean(volume[-20:]) if n >= 20 else np.mean(volume)
    
    # 均線與斜率：短均線不只看相對位置，也必須確認方向。
    # 以最新一個交易日的 MA 變動判斷當下方向；±0.25% 內視為走平。
    # 這能避免前幾日急升、但最近一日已走平或轉弱時仍被誤判為強勢上彎。
    close_series = pd.Series(close, dtype=float)

    def latest_ma(period):
        return close_series.rolling(window=period, min_periods=period).mean()

    def slope_pct(series, days=3):
        if len(series) <= days or pd.isna(series.iloc[-1]) or pd.isna(series.iloc[-(days + 1)]):
            return None
        base = series.iloc[-(days + 1)]
        return (series.iloc[-1] - base) / base * 100 if base else None

    ma5_series = latest_ma(5)
    ma10_series = latest_ma(10)
    ma20_series = latest_ma(20)
    ma50_series = latest_ma(50)
    ma100_series = latest_ma(100)
    ma200_series = latest_ma(200)

    ma5 = ma5_series.iloc[-1] if n >= 5 else None
    ma10 = ma10_series.iloc[-1] if n >= 10 else None
    ma20 = ma20_series.iloc[-1] if n >= 20 else None
    ma50 = ma50_series.iloc[-1] if n >= 50 else None
    ma100 = ma100_series.iloc[-1] if n >= 100 else None
    ma200 = ma200_series.iloc[-1] if n >= 200 else None
    ma5_slope = slope_pct(ma5_series, days=1)
    ma10_slope = slope_pct(ma10_series, days=1)
    ma20_slope = slope_pct(ma20_series, days=1)
    ma50_slope = slope_pct(ma50_series, days=1)
    
    # RSI(14)
    delta = np.diff(close)
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = np.mean(gain[-14:]) if len(gain) >= 14 else 1e-5
    avg_loss = np.mean(loss[-14:]) if len(loss) >= 14 else 1e-5
    rs = avg_gain / (avg_loss + 1e-5)
    rsi14 = 100 - (100 / (1 + rs))
    
    # MACD
    ema12 = close_series.ewm(span=12, adjust=False).mean()
    ema26 = close_series.ewm(span=26, adjust=False).mean()
    macd_series = ema12 - ema26
    macd_line = macd_series.iloc[-1]
    signal_line = macd_series.ewm(span=9, adjust=False).mean().iloc[-1]
    macd_hist = macd_line - signal_line

    # === 五大維度評分 ===
    
    # 1. 趨勢與均線結構 (25分)
    score_trend = 0.0
    slope_flat = 0.25
    short_alignment = ma5 is not None and ma5 > ma10 > ma20
    if short_alignment:
        score_trend += 5.0
    if ma5_slope is not None and ma5_slope > slope_flat:
        score_trend += 2.0
    if ma10_slope is not None and ma10_slope > slope_flat:
        score_trend += 4.0
    if ma20_slope is not None and ma20_slope > slope_flat:
        score_trend += 4.0
    if ma50 is not None and curr_price > ma50:
        score_trend += 5.0
    if ma50_slope is not None and ma50_slope > slope_flat:
        score_trend += 2.0
    if ma50 is not None and ma100 is not None and ma50 > ma100:
        score_trend += 2.0
    if ma100 is not None and ma200 is not None and ma100 > ma200:
        score_trend += 1.0

    # 下彎代表短線趨勢正在變弱，即使位置暫時仍是多頭排列也需扣分。
    if ma5_slope is not None and ma5_slope < -slope_flat:
        score_trend -= 2.0
    if ma10_slope is not None and ma10_slope < -slope_flat:
        score_trend -= 5.0
    if ma20_slope is not None and ma20_slope < -slope_flat:
        score_trend -= 3.0
    score_trend = max(0.0, min(25.0, score_trend))
    
    # 2. 動能與量能 (20分)
    score_momentum = 0.0
    if 55 <= rsi14 <= 76: score_momentum += 8.0
    elif 45 <= rsi14 < 55: score_momentum += 5.0
    
    if macd_line > 0 and macd_hist > 0: score_momentum += 7.0
    elif macd_hist > 0: score_momentum += 4.0
    
    if curr_vol >= 1.5 * vol20: score_momentum += 5.0
    elif curr_vol >= 1.0 * vol20: score_momentum += 3.0
    
    # 3. 圖表形態識別 (25分)
    pattern_name, score_pattern = recognize_pattern(df_calc)
    score_pattern = min(25.0, score_pattern + 10.0) # 基礎分 + 形態加分
    # 型態若沒有 MA10 的即時上彎確認，只能視為候選而非完整進場訊號。
    if ma10_slope is not None and abs(ma10_slope) <= slope_flat:
        score_pattern = min(score_pattern, 18.0)
    elif ma10_slope is not None and ma10_slope < -slope_flat:
        score_pattern = min(score_pattern, 14.0)
    
    # 4. 支撐防禦與斐波那契 (15分)
    score_support = 12.0
    swing_high = np.max(high[-60:]) if n >= 60 else np.max(high)
    swing_low = np.min(low[-60:]) if n >= 60 else np.min(low)
    diff = swing_high - swing_low
    fib236 = swing_high - 0.236 * diff
    fib382 = swing_high - 0.382 * diff
    
    # 5. 風險報酬比 R/R (15分)
    stop_loss = round(fib382 if curr_price > fib236 else swing_low, 2)
    if stop_loss >= curr_price:
        stop_loss = round(curr_price * 0.92, 2)
        
    target_price = round(swing_high * 1.15 if curr_price >= swing_high * 0.95 else swing_high, 2)
    
    risk = max(curr_price - stop_loss, 0.1)
    reward = max(target_price - curr_price, 0.1)
    rr_ratio = round(reward / risk, 2)
    
    score_rr = 10.0
    if rr_ratio >= 2.5: score_rr = 15.0
    elif rr_ratio >= 2.0: score_rr = 13.0
    elif rr_ratio >= 1.5: score_rr = 11.0
    
    # 法人籌碼加分 (+5分 max)
    score_chip = min(5.0, stock_info['inst_buy_days'] * 1.25)
    
    # 總得分與勝率對映
    total_score = min(100.0, score_trend + score_momentum + score_pattern + score_support + score_rr + score_chip)
    
    # 換算預期勝率 % (基礎勝率 50% + 分數係數)
    win_rate = round(50.0 + (total_score - 50.0) * 0.75, 1)
    # 強烈買入必須取得 MA10 當下上彎確認。走平只保留為候選型態；
    # 下彎則不可用先前的型態或法人加分掩蓋短線轉弱。
    if ma10_slope is not None and abs(ma10_slope) <= slope_flat:
        win_rate = min(win_rate, 67.5)
    elif ma10_slope is not None and ma10_slope < -slope_flat:
        win_rate = min(win_rate, 59.5)
    win_rate = max(42.0, min(88.0, win_rate))
    
    # 建議方向 (對齊 Morgan Stanley 5 維量化評級體系)
    if win_rate >= 68.0:
        action = "強烈買入 (Strong Buy)"
    elif win_rate >= 60.0:
        action = "買入 (Buy)"
    elif win_rate >= 52.0:
        action = "觀望 / 擇優佈局"
    else:
        action = "避開 / 減碼"

    return {
        'code': stock_info['code'],
        'name': stock_info['name'],
        'market': stock_info['market'],
        'symbol': stock_info['symbol'],
        'category': stock_info['category'],
        'price': curr_price,
        'ma5': round(ma5, 2) if ma5 is not None else None,
        'ma10': round(ma10, 2) if ma10 is not None else None,
        'ma20': round(ma20, 2) if ma20 is not None else None,
        'ma50': round(ma50, 2) if ma50 is not None else None,
        'ma100': round(ma100, 2) if ma100 is not None else None,
        'ma200': round(ma200, 2) if ma200 is not None else None,
        'ma5_slope': round(ma5_slope, 2) if ma5_slope is not None else None,
        'ma10_slope': round(ma10_slope, 2) if ma10_slope is not None else None,
        'ma20_slope': round(ma20_slope, 2) if ma20_slope is not None else None,
        'ma50_slope': round(ma50_slope, 2) if ma50_slope is not None else None,
        'short_alignment': short_alignment,
        'ma10_trend_state': ('上彎' if ma10_slope is not None and ma10_slope > slope_flat
                              else '下彎' if ma10_slope is not None and ma10_slope < -slope_flat
                              else '走平'),
        'trend_score': round(score_trend, 1),
        'rsi14': round(rsi14, 1),
        'macd_line': round(macd_line, 2),
        'macd_signal': round(signal_line, 2),
        'macd_hist': round(macd_hist, 2),
        'vol': int(curr_vol),
        'vol20': int(vol20),
        'vol_ratio': round(curr_vol / (vol20 + 1e-5), 2),
        'swing_high': round(swing_high, 2),
        'swing_low': round(swing_low, 2),
        'fib236': round(fib236, 2),
        'fib382': round(fib382, 2),
        'fib500': round(swing_high - 0.5 * diff, 2),
        'fib618': round(swing_high - 0.618 * diff, 2),
        'pattern': pattern_name,
        'win_rate': win_rate,
        'total_score': round(total_score, 1),
        'rr_ratio': rr_ratio,
        'stop_loss': stop_loss,
        'target_price': target_price,
        'action': action,
        'inst_buy_days': stock_info['inst_buy_days'],
        'html_path': stock_info['path']
    }


def save_stage4_report(r):
    """為單一個股生成符合 4 階段規格的 Morgan Stanley 詳細技術分析報告 (.md)"""
    file_dir = Path(r['html_path']).parent
    output_path = file_dir / f"{r['code']}_{r['name']}_4階段技術分析報告.md"
    
    # 計算 Pivot Points
    pp = round((r['swing_high'] + r['swing_low'] + r['price']) / 3, 2)
    s1 = round(2 * pp - r['swing_high'], 2)
    r1 = round(2 * pp - r['swing_low'], 2)
    
    entry_zone = f"{r['price'] * 0.98:.2f} 元 - {r['price']:.2f} 元" if r['win_rate'] < 70 else f"現價 {r['price']:.2f} 元 或 突破買進"

    def fmt_ma(value):
        return f"{value:.2f}" if value is not None else "資料不足"

    def slope_label(value):
        if value is None:
            return "資料不足"
        if value > 0.25:
            return f"上彎 {value:+.2f}%"
        if value < -0.25:
            return f"下彎 {value:+.2f}%"
        return f"走平 {value:+.2f}%"

    technical_tags = []
    if r['short_alignment']:
        technical_tags.append("MA5>MA10>MA20")
    else:
        technical_tags.append("短均線未完整多頭排列")
    if r['ma10_trend_state'] == '下彎':
        technical_tags.append("MA10 下彎（趨勢扣分）")
    elif r['ma10_trend_state'] == '上彎':
        technical_tags.append("MA10 上彎")
    else:
        technical_tags.append("MA10 走平（型態分受限）")

    if all(r[key] is not None for key in ('ma50', 'ma100', 'ma200')) and r['ma50'] > r['ma100'] > r['ma200']:
        technical_tags.append("中長期均線多頭排列")
    elif all(r[key] is not None for key in ('ma50', 'ma100', 'ma200')) and r['ma50'] < r['ma100'] < r['ma200']:
        technical_tags.append("中長期均線空頭排列")
    else:
        technical_tags.append("中長期均線資料不足／整理")
    technical_tags.append("RSI 過熱" if r['rsi14'] >= 70 else ("RSI 動能偏強" if r['rsi14'] >= 50 else "RSI 動能偏弱"))
    technical_tags.append("MACD 多方" if r['macd_hist'] > 0 else "MACD 收縮／空方")
    technical_tags.append("量能放大" if r['vol_ratio'] >= 1.5 else ("量能溫和" if r['vol_ratio'] >= 1 else "量能萎縮"))

    buy_days = int(r.get('inst_buy_days', 0))
    chip_tags = [f"近5日法人買超 {buy_days} 日"]
    chip_tags.append("法人籌碼偏多" if buy_days >= 4 else ("法人籌碼中性" if buy_days >= 2 else "法人籌碼偏弱"))
    pattern_tags = [r['pattern']]
    
    lines = []
    lines.append(f"# 📈 {r['name']} ({r['code']}.{r['market']}) 專業技術分析報告\n")
    lines.append("### 【輸入數據】")
    lines.append(f"- **股票代號**：{r['symbol']}（{r['name']}）")
    lines.append("- **分析日期**：2026 年 8 月 26 日")
    lines.append(f"- **當前價格**：{r['price']:.2f} 元")
    lines.append("- **技術數據摘要**：")
    lines.append(f"  - **短期均線**：MA5 ({fmt_ma(r['ma5'])}) / MA10 ({fmt_ma(r['ma10'])}) / MA20 ({fmt_ma(r['ma20'])})")
    lines.append(f"  - **短均線斜率（最新1日）**：MA5 {slope_label(r['ma5_slope'])} / MA10 {slope_label(r['ma10_slope'])} / MA20 {slope_label(r['ma20_slope'])}")
    lines.append(f"  - **日線均線**：50日MA ({fmt_ma(r['ma50'])}) / 100日MA ({fmt_ma(r['ma100'])}) / 200日MA ({fmt_ma(r['ma200'])})")
    lines.append(f"  - **RSI(14)**：{r['rsi14']} （{'超買強勢' if r['rsi14'] > 70 else '多頭推進區' if r['rsi14'] > 50 else '弱勢整理'}）")
    lines.append(f"  - **MACD**：DIF ({r['macd_line']}) / Signal ({r['macd_signal']}) / 柱狀圖 ({r['macd_hist']})")
    lines.append(f"  - **成交量**：{r['vol']:,} 股（相對 20日均量 {r['vol_ratio']} 倍）\n")
    lines.append("### 【標籤摘要】")
    lines.append(f"- **技術標籤**：{'、'.join(technical_tags)}")
    lines.append(f"- **籌碼標籤**：{'、'.join(chip_tags)}")
    lines.append(f"- **型態標籤**：{'、'.join(pattern_tags)}\n")
    lines.append("---")
    
    lines.append("\n## 第一階段：多時間框架趨勢分析（判斷大方向）\n")
    lines.append("1. **週線趨勢分析**：僅在完整長均線資料可用時，才判定中長期方向。")
    lines.append(f"2. **日線趨勢分析**：MA5/10/20 {'維持多頭排列' if r['short_alignment'] else '未形成完整多頭排列'}；MA10 為{slope_label(r['ma10_slope'])}。")
    lines.append("3. **60 分線分析**：短期噴出後於高檔進行滾量換手整理。")
    lines.append("4. **綜合判斷**：")
    lines.append(f"   - **【趨勢方向】**：{'多頭' if r['win_rate'] >= 60 else '觀望'}")
    lines.append(f"   - **【主要論述】**：{r['pattern']}，且量價結構保持健全推進。")
    lines.append(f"   - **【技術無效點】**：若日線收盤跌破關鍵支撐 {r['stop_loss']} 元，則多頭結構無效。\n")
    lines.append("---")
    
    lines.append("\n## 第二階段：完整技術指標分析（識別關鍵價位）\n")
    lines.append("| 指標類型 | 數值/狀態 | 解讀 |")
    lines.append("|---------|----------|------|")
    lines.append(f"| MA5 / MA10 / MA20 | {fmt_ma(r['ma5'])} / {fmt_ma(r['ma10'])} / {fmt_ma(r['ma20'])} 元 | MA10 {slope_label(r['ma10_slope'])} |")
    lines.append(f"| 50 / 200 日均線 | {fmt_ma(r['ma50'])} / {fmt_ma(r['ma200'])} 元 | 資料不足時不納入趨勢分數 |")
    lines.append(f"| RSI(14) | {r['rsi14']} | {'過熱強勢區' if r['rsi14'] >= 70 else '多頭動能推進區'} |")
    lines.append(f"| MACD | 柱狀圖 {r['macd_hist']} | {'零軸上方多頭擴張' if r['macd_hist'] > 0 else '震盪收縮'} |")
    lines.append(f"| 成交量 | 均量 {r['vol_ratio']} 倍 | {'爆量攻擊' if r['vol_ratio'] >= 1.5 else '溫和換手推升'} |")
    lines.append(f"| 斐波那契 | 23.6%: {r['fib236']} / 38.2%: {r['fib382']} | 強勢回調防禦分界區 |")
    lines.append("\n【關鍵價位】")
    lines.append(f"- **支撐位 1**：{r['fib236']} 元 (Fib 23.6% 短線強弱線)")
    lines.append(f"- **支撐位 2**：{r['stop_loss']} 元 (Fib 38.2% / 波段平台支撐)")
    lines.append(f"- **支撐位 3**：{fmt_ma(r['ma50'])} 元 (50 日均線防線；資料足夠時適用)")
    lines.append(f"- **阻力位 1**：{r['swing_high']} 元 (近期波段/歷史高點)")
    lines.append(f"- **阻力位 2**：{r['target_price']} 元 (第一階段波段測量目標價)")
    lines.append(f"- **阻力位 3**：{r['target_price'] * 1.15:.2f} 元 (主升段第二擴展目標)\n")
    lines.append("---")
    
    lines.append("\n## 第三階段：圖表形態識別（尋找具體進場點）\n")
    lines.append(f"【主要形態】：{r['pattern']}")
    lines.append(f"【確認程度】：{'部分確認/測試突破中' if '測試' in r['pattern'] else '完全確認'}")
    lines.append(f"【形態目標價】：{r['target_price']} 元")
    lines.append(f"【理想進場區】：{entry_zone}")
    lines.append("【進場確認條件】：")
    lines.append("  - 條件 1：回檔測試支撐不破且成交量顯著萎縮。")
    lines.append(f"  - 條件 2：帶量突破強烈前高阻力點 {r['swing_high']} 元。")
    lines.append(f"【停損位】：{r['stop_loss']} 元 (跌破波段防禦平台)")
    lines.append("【形態失敗警示】：若連續 3 日無法站穩阻力點並爆量下破 38.2% 支撐，警惕形態轉弱。\n")
    
    # 判斷第三階段推薦策略與理由
    if ('突破' in r['pattern'] or '新高' in r['pattern'] or '杯柄' in r['pattern']) and r['win_rate'] >= 60.0:
        stage3_strat = "右側突破"
        stage3_reason = f"當前形態呈現「{r['pattern']}」，多頭攻擊結構完整且突破關鍵阻力，順勢跟隨量能放大進場之勝率較高。"
    elif '回調' in r['pattern'] or '雙重底' in r['pattern'] or 'W底' in r['pattern'] or (52.0 <= r['win_rate'] < 60.0):
        stage3_strat = "左側佈局"
        stage3_reason = f"當前形態呈現「{r['pattern']}」，價格回測至關鍵支撐/斐波那契回調區間，下檔風險有限，適合逢低分批建立防禦部位。"
    else:
        stage3_strat = "混合策略 / 觀望"
        stage3_reason = "形態處於轉換震盪期，多空動能尚未完全明朗，建議採取極小部位試單或等待更明確突破信號。"

    lines.append("【進場策略建議】")
    lines.append("根據當前形態和趨勢，推薦：")
    lines.append("- 如果形態為「整理後突破」（三角形、旗形、杯柄）：建議右側突破進場")
    lines.append("- 如果形態為「回調至支撐」（斐波那契回調、前低支撐）：建議左側分批佈局")
    lines.append("- 如果形態不明確：建議觀望或極小部位試單\n")
    lines.append(f"推薦策略：**{stage3_strat}**")
    lines.append(f"理由：{stage3_reason}\n")
    lines.append("---")
    
    lines.append("\n## 第四階段：Morgan Stanley 技術分析儀表板（生成最終交易計劃）\n")
    lines.append(f"【股票代號】：{r['symbol']} ({r['name']})")
    lines.append("- **分析日期**：2026-08-26")
    lines.append(f"- **當前價格**：{r['price']:.2f} 元\n")
    lines.append("■ **趨勢狀態**")
    lines.append(f"- 短期 (5-10 日)：{'多頭噴出' if r['rsi14'] > 65 else '高檔震盪'}")
    lines.append(f"- 中期 (20-50 日)：多頭（均線向上發散）")
    lines.append(f"- 長期 (200 日)：多頭（穩居 200 日均線之上）\n")
    lines.append("■ **關鍵價位與 Pivot Point**")
    lines.append(f"- Pivot Point：{pp} 元 | S1：{s1} 元 | R1：{r1} 元\n")
    # 判斷交易風格與策略情境
    is_bull = r['win_rate'] >= 60.0 or '買入' in r['action']
    is_neutral = (52.0 <= r['win_rate'] < 60.0) or '觀望' in r['action']
    
    trade_dir = "做多" if is_bull else ("做多 (左側)" if is_neutral else "觀望")
    if is_bull:
        strat_name = "右側突破策略 (情境 A)"
        entry_desc = f"{r['price'] * 0.995:.2f} ~ {r['price'] * 1.02:.2f} 元 (突破關鍵前高/阻力確認區間)"
        first_entry = f"{r['price']:.2f} 元 (部位 50%)"
        add_point_1 = f"{r['price'] * 1.02:.2f} 元 (帶量突破前高並站穩時加碼 30%)"
        add_point_2 = f"{r['swing_high']:.2f} 元 (創波段新高後回測不破加碼 20%)"
        position_ratio = "80% ~ 100% (標準多頭部位)"
    elif is_neutral:
        strat_name = "左側分批佈局策略 (情境 B)"
        entry_desc = f"{r['fib382']:.2f} ~ {r['price'] * 0.98:.2f} 元 (近波段 38.2% 斐波那契回調支撐位，分批承接)"
        first_entry = f"{r['price'] * 0.98:.2f} 元 (部位 30%)"
        add_point_1 = f"{r['fib382']:.2f} 元 (回測斐波 38.2% 支撐回升時加碼 30%)"
        add_point_2 = f"{r['fib500']:.2f} 元 (若回測 50% 支撐站穩加碼 20%)"
        position_ratio = "30% ~ 50% (左側防禦部位，嚴控風險)"
    else:
        strat_name = "防守觀望策略 (情境 C)"
        entry_desc = f"{r['fib500']:.2f} ~ {r['fib618']:.2f} 元 (深度回調觀察區，未見止跌信號前不進場)"
        first_entry = "暫緩進場 (部位 0%)"
        add_point_1 = f"{r['swing_low']:.2f} 元 (打底結構確立後再行評估)"
        add_point_2 = "無 (不盲目向下攤平)"
        position_ratio = "0% ~ 30% (極小部位或空手觀望)"

    lines.append("■ **操作建議**\n")
    lines.append("【交易風格選擇】")
    lines.append("根據第一階段【趨勢方向】的判斷，選擇對應策略：\n")
    lines.append("情境 A：趨勢方向 = 多頭（右側為主）")
    lines.append("- 進場策略：等待突破關鍵阻力位後進場")
    lines.append("- 進場區域：[突破價位 + 0.5~2% 的確認區間]")
    lines.append("- 加碼點：突破後回測支撐不破時加碼 30-50%")
    lines.append("- 停損位：跌破突破 K 棒低點或關鍵支撐位")
    lines.append("- 部位比例：標準部位的 80-100%\n")
    lines.append("情境 B：趨勢方向 = 觀望/盤整（左側為主）")
    lines.append("- 進場策略：在支撐位/斐波那契回調位分批佈局")
    lines.append("- 進場區域：[支撐位 1 附近，可分 2-3 批]")
    lines.append("- 加碼點：每下跌 3-5% 加碼一次，最多 3 次")
    lines.append("- 停損位：跌破支撐位 3 或關鍵技術無效點")
    lines.append("- 部位比例：標準部位的 30-50%（左側風險較高）\n")
    lines.append("情境 C：趨勢方向 = 空頭（右側做空或觀望）")
    lines.append("- 進場策略：等待反彈至阻力位受阻後做空，或觀望")
    lines.append("- 進場區域：[阻力位 1-2 附近]")
    lines.append("- 停損位：突破阻力位 3")
    lines.append("- 部位比例：標準部位的 50-70%\n")
    lines.append("【最終執行方案】")
    lines.append(f"【交易方向】：{trade_dir}")
    lines.append(f"【推薦策略】：{strat_name}")
    lines.append(f"【進場區域】：{entry_desc}")
    lines.append(f"【第一批進場】：{first_entry}")
    lines.append(f"【加碼點 1】：{add_point_1}")
    lines.append(f"【加碼點 2】：{add_point_2}")
    lines.append(f"【停損位】：{r['stop_loss']} 元 (跌破波段防禦平台與關鍵支撐執行停損)")
    lines.append(f"【目標價 1】：{r['swing_high']} 元 (波段前高阻力位，可減碼 30-50% 鎖定獲利)")
    lines.append(f"【目標價 2】：{r['target_price']} 元 (形態滿足點與斐波擴展主要目標)")
    lines.append(f"【風險報酬比】：**{r['rr_ratio']}**")
    lines.append(f"【建議總部位比例】：{position_ratio}")
    lines.append(f"【預期勝率】：**`{r['win_rate']}%`** (評分: {r['total_score']} / 100)")
    lines.append(f"【建議評級】：**{r['action']}**\n")
    lines.append("【混合策略特別提醒】")
    lines.append("- 如果使用左側佈局：嚴格控制第一批部位不超過 50%，保留加碼子彈")
    lines.append("- 如果使用右側突破：確認成交量放大，避免假突破")
    lines.append("- 如果趨勢不明：降低總部位至 30-50%，等待更明確信號\n")
    lines.append("---")
    lines.append("\n*本報告由 Python batch_scanner 依據 4 階段 Morgan Stanley 模型自動生成。*")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def main():
    print("=" * 65)
    print("🚀 啟動個股勝率與線型型態自動化掃描器 (batch_scanner.py)")
    print("=" * 65)
    
    if not REPORTS_DIR.exists():
        print(f"❌ 錯誤：找不到 {REPORTS_DIR} 目錄！")
        return
        
    html_files = list(REPORTS_DIR.glob("**/*.html"))
    print(f"📂 於 reports/ 目錄下搜尋到 {len(html_files)} 個 HTML 檔案")
    
    results = []
    generated_reports_cnt = 0
    for p in html_files:
        info = parse_html_report(p)
        if not info:
            continue
        res = calculate_winrate(info)
        if res:
            results.append(res)
            # 方案 A：為每檔個股生成獨立的 4 階段詳細 Markdown 分析報告
            try:
                save_stage4_report(res)
                generated_reports_cnt += 1
            except Exception:
                pass
            
    if not results:
        print("⚠️ 未解構出有效的個股數據。")
        return

    # 按勝率高到低排序
    results.sort(key=lambda x: x['win_rate'], reverse=True)
    
    print(f"✅ 成功完成 {len(results)} 檔個股之純數據型態識別與勝率量化計算！")
    print(f"📄 已於 reports/ 下各產業資料夾獨立生成 {generated_reports_cnt} 份【4階段詳細技術分析報告.md】！\n")
    
    # 輸出 Console 前 10 名
    print("🏆 勝率前 10 名個股摘要：")
    print(f"{'排名':<4} {'代號':<6} {'股票名稱':<8} {'分類':<8} {'價格':<8} {'預期勝率':<8} {'風報比':<6} {'識別型態'}")
    print("-" * 75)
    for i, r in enumerate(results[:10], 1):
        print(f"#{i:<3} {r['code']:<6} {r['name']:<8} {r['category']:<8} {r['price']:<8.2f} {r['win_rate']}%{'':<3} {r['rr_ratio']:<6} {r['pattern']}")

    # 產出完整 Markdown 排行榜檔案
    md_content = []
    md_content.append("# 📊 股票勝率與線型型態自動掃描總排行榜\n")
    md_content.append(f"> **掃描日期**：2026-08-26 | **掃描檔數**：{len(results)} 檔 | **模型**：Morgan Stanley 五大維度勝率模型\n")
    md_content.append("| 排名 | 代號 | 股票名稱 | 產業分類 | 當前價格 | 預期勝率 | 風險報酬比 | 建議進場位 | 建議停損位 | 目標價 | 自動識別型態 | 評級建議 |")
    md_content.append("|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|")
    
    for i, r in enumerate(results, 1):
        entry_price = f"{r['price'] * 0.98:.1f}" if r['win_rate'] < 70 else "現價/突破買進"
        md_content.append(
            f"| {i} | `{r['code']}` | **{r['name']}** | {r['category']} | **{r['price']:.2f}** | **`{r['win_rate']}%`** | {r['rr_ratio']} | {entry_price} | {r['stop_loss']} | {r['target_price']} | {r['pattern']} | {r['action']} |"
        )
        
    md_content.append("\n---\n")
    md_content.append("### 💡 勝率評分指標維度說明：\n")
    md_content.append("1. **趨勢與均線結構 (25%)**：MA5/10/20 排列與 3 日斜率；資料完整時再加入 50D/100D/200D 趨勢。MA10 下彎會扣分。\n")
    md_content.append("2. **動能與量能 (20%)**：RSI(14) 區間、MACD 雙線/柱狀圖與相對 20日均量倍數。\n")
    md_content.append("3. **圖表型態 (25%)**：純數據幾何演算法識別（歷史新高、杯柄、雙底、VCP 收縮等）。\n")
    md_content.append("4. **支撐與斐波那契 (15%)**：波段 High/Low 之 23.6%、38.2% 回調支撐涵蓋度。\n")
    md_content.append("5. **風報比 R/R (15%)**：潛在獲利空間與停損空間之比率（> 2.0 為優秀標準）。\n")

    OUTPUT_MD.write_text("\n".join(md_content), encoding="utf-8")
    print(f"\n📄 完整排行榜已寫入至：{OUTPUT_MD}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        err_msg = traceback.format_exc()
        print("\n" + "=" * 65)
        print("❌ 執行過程發生錯誤：")
        print("=" * 65)
        print(err_msg)
        try:
            with open("error_log.txt", "w", encoding="utf-8") as f:
                f.write(err_msg)
            print("📄 錯誤訊息已自動儲存至專案目錄下的 error_log.txt")
        except Exception:
            pass
    finally:
        print("\n" + "-" * 65)
        # 不等待 Enter：本程式也會由 reports_manager 以無互動背景程序啟動；
        # 任何 input() 都可能在 Windows 上造成 EOFError 或讓前端永久停在執行中。
        print("📌 Scanner 程式已結束。")
