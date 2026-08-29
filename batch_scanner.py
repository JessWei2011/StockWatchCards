r"""
========================================================================================
🤖 【ChatGPT 專屬掃描器 - batch_scanner.py】
========================================================================================
⚠️ 【架構防衝突聲明 / Architecture Warning for All AI Assistants】：
1. 本檔案為【ChatGPT 專屬的雙軌觀察掃描器】，其唯一輸出目標為：
   - 📊 `stock_winrate_ranking.md` (ChatGPT 雙軌總排行榜)
   - 🎯 `breakout_watchlist.md` (ChatGPT 突破觀察池)
2. 【禁止覆寫個股報告】：全市場 73 檔個股詳細分析報告（`reports/**/{code}_{name}_4階段技術分析報告.md`）
   已全權交由 `batch_scanner_gemini.py` 統一產出與維護，本腳本請勿重複生成或覆蓋個股 .md，以確保版本一致！
3. 如需調整 Gemini 的掃描邏輯或個股報告格式，請至 `batch_scanner_gemini.py` 修改，雙方各自獨立互不衝突。
========================================================================================
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
BREAKOUT_OUTPUT_MD = ROOT / "breakout_watchlist.md"
YFINANCE_CACHE_DIR = ROOT / ".cache" / "yfinance"

# yfinance 預設會將時區／Cookie 快取寫到使用者設定目錄；在受限環境下會
# 失敗並讓 fetch_latest_bar 靜默回傳 None。固定放在專案可寫入的快取目錄。
if YFINANCE_AVAILABLE:
    YFINANCE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    yf.set_tz_cache_location(str(YFINANCE_CACHE_DIR))


def parse_html_report(file_path):
    """解析單一 HTML 報告中的指標、籌碼與歷史 K 線數據"""
    text = file_path.read_text(encoding="utf-8", errors="ignore")
    pe_match = re.search(r'<b>PE：</b>\s*([\d.]+)', text)
    trailing_pe = float(pe_match.group(1)) if pe_match else None
    
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
                
    # 提取三大法人、融資融券。舊版只計算「買超天數」，會把小量買超和
    # 大額承接混為一談；保留逐日資料供新的突破篩選器判讀。
    inst_buy_days = 0
    institutions = []
    margin = []
    if len(tables) >= 2:
        inst_rows = re.findall(r'<tr[^>]*>(.*?)</tr>', tables[1], re.S)
        for r in inst_rows[1:]:
            cells = [re.sub(r'<[^>]+>', '', c).strip() for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', r, re.S)]
            if len(cells) >= 5:
                try:
                    foreign = float(cells[1].replace(',', '').replace('+', ''))
                    trust = float(cells[2].replace(',', '').replace('+', ''))
                    dealer = float(cells[3].replace(',', '').replace('+', ''))
                    val = float(cells[4].replace(',', '').replace('+', ''))
                    institutions.append({'date': cells[0], 'foreign': foreign, 'trust': trust,
                                         'dealer': dealer, 'total': val})
                    if val > 0:
                        inst_buy_days += 1
                except ValueError:
                    pass

    if len(tables) >= 3:
        margin_rows = re.findall(r'<tr[^>]*>(.*?)</tr>', tables[2], re.S)
        for r in margin_rows[1:]:
            cells = [re.sub(r'<[^>]+>', '', c).strip() for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', r, re.S)]
            if len(cells) >= 5:
                try:
                    margin.append({'date': cells[0], 'balance': float(cells[1].replace(',', '')),
                                   'change': float(cells[2].replace(',', '').replace('+', ''))})
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
        'institutions': institutions,
        'margin': margin,
        'trailing_pe': trailing_pe,
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
        technical_tags.append("均線多頭排列")
    else:
        technical_tags.append("均線多空中性整理")
    if r['ma10_trend_state'] == '下彎':
        technical_tags.append("10MA轉俯角 (波段修正)")
    elif r['ma10_trend_state'] == '上彎':
        technical_tags.append("10MA轉仰角 (波段翻多)")
    else:
        technical_tags.append("均線糾纏蓄勢")

    if all(r[key] is not None for key in ('ma50', 'ma100', 'ma200')) and r['ma50'] > r['ma100'] > r['ma200']:
        technical_tags.append("中長期均線多頭排列")
    elif all(r[key] is not None for key in ('ma50', 'ma100', 'ma200')) and r['ma50'] < r['ma100'] < r['ma200']:
        technical_tags.append("中長期均線空頭排列")

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


def _pct_change(now, before):
    return (now / before - 1) * 100 if before else 0.0


def analyse_breakout_candidate(stock_info):
    """用昨天收盤資料找「接近突破」而非把碰到前高視為已突破。"""
    df = pd.DataFrame(stock_info['kline'])
    # 既有 HTML 報表保留約 50 根日 K 線；40 日線已可用來判斷中期方向，
    # 不因資料呈現長度而讓所有股票被錯誤排除。
    if len(df) < 50:
        return None
    for col in ('open', 'high', 'low', 'close', 'volume'):
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df.dropna().reset_index(drop=True)
    if len(df) < 50 or (df['volume'].tail(20) <= 0).any():
        return None

    close, high, low, volume = (df[c].to_numpy(dtype=float) for c in ('close', 'high', 'low', 'volume'))
    price = close[-1]
    ma10 = pd.Series(close).rolling(10).mean()
    ma20 = pd.Series(close).rolling(20).mean()
    ma40 = pd.Series(close).rolling(40).mean()
    ma20_slope = _pct_change(ma20.iloc[-1], ma20.iloc[-6])
    ma40_slope = _pct_change(ma40.iloc[-1], ma40.iloc[-6])

    # 壓力位只從今天以前的資料取得；最後兩天不納入，避免把今天盤中碰高誤判成突破。
    pivot = float(np.max(high[-32:-2]))
    distance_to_pivot = (pivot / price - 1) * 100
    prior_vol20 = float(np.mean(volume[-21:-1]))
    vol_ratio = volume[-1] / prior_vol20 if prior_vol20 else 0.0
    bar_range = max(high[-1] - low[-1], 1e-9)
    close_location = (close[-1] - low[-1]) / bar_range
    upper_wick = (high[-1] - max(close[-1], df['open'].iloc[-1])) / bar_range

    # 「收斂」須同時表現在價格波動和成交量，不用主觀的杯柄/W 底名稱替代。
    daily_range_pct = (high - low) / np.maximum(close, 1e-9)
    recent_range = float(np.mean(daily_range_pct[-5:]))
    base_range = float(np.mean(daily_range_pct[-25:-5]))
    recent_vol = float(np.mean(volume[-10:]))
    base_vol = float(np.mean(volume[-30:-10]))
    range_contraction = recent_range / base_range if base_range else 9.0
    volume_contraction = recent_vol / base_vol if base_vol else 9.0

    inst = stock_info.get('institutions', [])[:5]
    inst_total_5 = sum(x['total'] for x in inst)
    inst_buy_days = sum(x['total'] > 0 for x in inst)
    trust_buy_days = sum(x['trust'] > 0 for x in inst)
    foreign_buy_days = sum(x['foreign'] > 0 for x in inst)
    margin_5 = sum(x['change'] for x in stock_info.get('margin', [])[:5])

    # 先排除明顯不適合等突破的情況。這比事後以大量加分掩蓋弱勢可靠。
    trend_ok = price > ma20.iloc[-1] > ma40.iloc[-1] and ma20_slope > 0 and ma40_slope >= -0.2
    near_pivot = 0 <= distance_to_pivot <= 5.0
    # 突破後已拉離壓力太多，不再是可追的「剛發動」訊號；把它留給
    # 另一種趨勢持有策略，而不是混進短線突破池。
    breakout_extension = (price / pivot - 1) * 100
    confirmed = (0.3 <= breakout_extension <= 3.5 and vol_ratio >= 1.5
                 and close_location >= 0.65 and upper_wick <= 0.30)
    if not trend_ok or not (near_pivot or confirmed):
        return None
    if upper_wick > 0.50 or (close[-1] < df['open'].iloc[-1] and vol_ratio >= 1.5):
        return None  # 爆量收黑或長上影線，較像賣壓而不是突破

    score = 0.0
    reasons, risks = [], []
    score += 22; reasons.append('價格站上 20/40 日均線，且中期均線沒有下彎')
    if range_contraction <= 0.85:
        score += 14; reasons.append(f'近期波動縮至整理期的 {range_contraction:.0%}')
    else:
        risks.append('價格波動尚未明顯收斂')
    if volume_contraction <= 0.85:
        score += 12; reasons.append(f'整理期量能縮至前段的 {volume_contraction:.0%}')
    else:
        risks.append('整理期量能未縮，賣壓可能尚未消化')
    if confirmed:
        score += 20; reasons.append(f'收盤有效站上 {pivot:.2f}，量為 20 日均量 {vol_ratio:.2f} 倍')
        status = '已確認突破'
    else:
        score += max(0, 14 - distance_to_pivot * 2)
        reasons.append(f'距離突破確認價 {pivot:.2f} 尚有 {distance_to_pivot:.2f}%')
        status = '觀察：尚未突破'
    if inst_buy_days >= 3 and inst_total_5 > 0:
        score += 10; reasons.append(f'近 5 日法人買超 {inst_buy_days} 日')
    else:
        risks.append('法人承接不連續或近 5 日合計偏賣')
    if trust_buy_days >= 3:
        score += 5; reasons.append(f'投信買超 {trust_buy_days} 日')
    if foreign_buy_days == 0:
        risks.append('外資近 5 日未出現買超')
    if margin_5 > 0:
        risks.append(f'近 5 日融資增加 {margin_5:,.0f} 張，突破時需防籌碼浮額')
        score -= min(8, margin_5 / max(volume[-1], 1) * 100)

    stop = float(np.min(low[-10:]))
    return {
        'code': stock_info['code'], 'name': stock_info['name'], 'category': stock_info['category'],
        'date': str(df['date'].iloc[-1]), 'price': price, 'status': status, 'score': round(score, 1),
        'pivot': pivot, 'distance': round(distance_to_pivot, 2), 'vol_ratio': round(vol_ratio, 2),
        'range_contraction': round(range_contraction, 2), 'volume_contraction': round(volume_contraction, 2),
        'stop': stop, 'reasons': reasons, 'risks': risks,
    }


def rank_observed_stock(stock_info, as_of=None):
    """在既有強勢觀察池內排序；不以硬門檻刪除股票。"""
    df = pd.DataFrame(stock_info['kline'])
    if as_of:
        # 報表採同一年 MM/DD 格式；先固定到共同資料截止日，不能把少數
        # 已更新到今天的報告混進昨天的排行。
        df = df[df['date'].astype(str) <= as_of].copy()
    if len(df) < 50:
        return None
    for col in ('open', 'high', 'low', 'close', 'volume'):
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df.dropna().reset_index(drop=True)
    if len(df) < 50:
        return None
    close, high, low, volume = (df[c].to_numpy(dtype=float) for c in ('close', 'high', 'low', 'volume'))
    price = close[-1]
    close_s = pd.Series(close)
    ma10, ma20, ma40 = (close_s.rolling(n).mean() for n in (10, 20, 40))
    ma20_slope = _pct_change(ma20.iloc[-1], ma20.iloc[-6])
    ma40_slope = _pct_change(ma40.iloc[-1], ma40.iloc[-6])
    pivot = float(np.max(high[-32:-2]))
    distance = (pivot / price - 1) * 100
    extension = -distance
    prior_vol20 = float(np.mean(volume[-21:-1]))
    vol_ratio = volume[-1] / prior_vol20 if prior_vol20 else 0.0
    day_range = max(high[-1] - low[-1], 1e-9)
    close_location = (price - low[-1]) / day_range
    upper_wick = (high[-1] - max(price, df['open'].iloc[-1])) / day_range
    recent_range = float(np.mean((high[-5:] - low[-5:]) / np.maximum(close[-5:], 1e-9)))
    base_range = float(np.mean((high[-25:-5] - low[-25:-5]) / np.maximum(close[-25:-5], 1e-9)))
    range_ratio = recent_range / base_range if base_range else 1.0
    volume_ratio = float(np.mean(volume[-10:])) / max(float(np.mean(volume[-30:-10])), 1e-9)
    inst = stock_info.get('institutions', [])[:5]
    inst_total = sum(x['total'] for x in inst)
    inst_buy_days = sum(x['total'] > 0 for x in inst)
    trust_buy_days = sum(x['trust'] > 0 for x in inst)
    margin_5 = sum(x['change'] for x in stock_info.get('margin', [])[:5])

    score, reasons, risks = 0.0, [], []
    # 趨勢：位置和方向同時成立才給高分。
    if price > ma20.iloc[-1] > ma40.iloc[-1]:
        score += 20; reasons.append('收盤站在 20/40 日均線之上')
    elif price > ma20.iloc[-1]:
        score += 9; reasons.append('收盤仍站上 20 日均線')
    else:
        score -= 12; risks.append('收盤跌回 20 日均線下方')
    if ma20_slope > 0 and ma40_slope >= -0.2:
        score += 12; reasons.append('20 日均線上彎、中期趨勢未轉弱')
    elif ma20_slope > 0:
        score += 4; risks.append('中期均線仍偏弱')
    else:
        score -= 10; risks.append('20 日均線未上彎')

    # 整理品質：越縮越好，但不把未縮量的股票直接刪掉。
    if range_ratio <= .80:
        score += 15; reasons.append(f'最近波動縮至前段的 {range_ratio:.0%}')
    elif range_ratio <= 1.0:
        score += 7; reasons.append('近期波動沒有擴大')
    else:
        score -= 5; risks.append('近期波動放大')
    if volume_ratio <= .85:
        score += 12; reasons.append(f'整理期量能縮至前段的 {volume_ratio:.0%}')
    elif volume_ratio <= 1.05:
        score += 4
    else:
        score -= 4; risks.append('整理期量能未縮')

    # 位置：剛接近壓力最有價值；已拉太遠則扣分，避免把漲多股排第一。
    if 0 <= distance <= 4:
        score += 18 - distance * 2; reasons.append(f'距突破價 {pivot:.2f} 尚有 {distance:.2f}%')
    elif 0.3 <= extension <= 3.5 and vol_ratio >= 1.5 and close_location >= .65 and upper_wick <= .30:
        score += 13; reasons.append('剛完成有效突破，未過度延伸')
    elif extension > 3.5:
        score -= min(18, extension); risks.append(f'已離前高 {extension:.1f}%，不屬剛發動位置')
    else:
        score += 2; risks.append('距離壓力較遠')
    if upper_wick > .45:
        score -= 8; risks.append('當日上影線長，壓力明顯')
    if price < df['open'].iloc[-1] and vol_ratio >= 1.5:
        score -= 12; risks.append('爆量收黑，疑似賣壓')

    # 籌碼：看連續性，同時對融資快速增加保留風險。
    if inst_buy_days >= 3 and inst_total > 0:
        score += 12; reasons.append(f'近 5 日法人買超 {inst_buy_days} 日')
    elif inst_total > 0:
        score += 4
    else:
        score -= 6; risks.append('近 5 日法人合計偏賣')
    if trust_buy_days >= 3:
        score += 5; reasons.append(f'投信買超 {trust_buy_days} 日')
    if margin_5 > 0:
        penalty = min(10, margin_5 / max(volume[-1], 1) * 100)
        score -= penalty
        if penalty >= 2:
            risks.append(f'近 5 日融資增加 {margin_5:,.0f} 張')

    return {'code': stock_info['code'], 'name': stock_info['name'], 'category': stock_info['category'],
            'date': str(df['date'].iloc[-1]), 'price': price, 'pivot': pivot, 'distance': round(distance, 2),
            'score': round(score, 1), 'reasons': reasons, 'risks': risks}


def evaluate_dual_strategy(stock_info, all_category_counts=None, as_of=None):
    """
    進化版雙軌選股模型 (融合實戰分析師與多因子量化邏輯)：
    1. 🚀 暴漲動能型 (Explosive Momentum)：
       - 【模式 A】極致動能：歷史/波段新高突破 (Blue Sky)、RSI高檔強勢鈍化(>75)、5MA極速仰角(>2.5%)、處置股籌碼鎖定軋空、當日收盤在K棒上緣(>90%)。
       - 【模式 B】洗盤表態點火：上升月線(20MA斜率>1%) + 窒息量回測月線(量比<0.6x，乖離<6%) + 族群共振點火。
       - 【族群共振加權】：同題材族群（光學、CPO、被動元件、散熱）集體轉強時加權。
    2. 🛡️ 穩健防守型 (Solid Defensive)：
       - 均線穩健翻揚、剛脫離月線成本區 (乖離0.5%~6.5%)、法人連續買超、低風險報酬比。
    """
    df = pd.DataFrame(stock_info['kline'])
    if as_of:
        df = df[df['date'].astype(str) <= as_of].copy()
    if len(df) < 20:
        return None
    for col in ('open', 'high', 'low', 'close', 'volume'):
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df.dropna().reset_index(drop=True)
    if len(df) < 20:
        return None

    close = df['close'].to_numpy(dtype=float)
    high = df['high'].to_numpy(dtype=float)
    low = df['low'].to_numpy(dtype=float)
    volume = df['volume'].to_numpy(dtype=float)
    price = close[-1]
    
    n = len(df)
    close_s = pd.Series(close)
    ma5 = close_s.rolling(5).mean()
    ma10 = close_s.rolling(10).mean()
    ma20 = close_s.rolling(20).mean()
    
    s5 = _pct_change(ma5.iloc[-1], ma5.iloc[-2]) if len(ma5) >= 2 else 0.0
    s10 = _pct_change(ma10.iloc[-1], ma10.iloc[-2]) if len(ma10) >= 2 else 0.0
    s20 = _pct_change(ma20.iloc[-1], ma20.iloc[-2]) if len(ma20) >= 2 else 0.0
    s20_3d = _pct_change(ma20.iloc[-1], ma20.iloc[-4]) if len(ma20) >= 24 else s20
    ret1 = _pct_change(close[-1], close[-2]) if n >= 2 else 0.0
    ret3 = _pct_change(close[-1], close[-4]) if n >= 4 else ret1
    ret5 = _pct_change(close[-1], close[-6]) if n >= 6 else ret3
    ret10 = _pct_change(close[-1], close[-11]) if n >= 11 else ret5
    
    # RSI(14)
    delta = np.diff(close)
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = np.mean(gain[-14:]) if len(gain) >= 14 else 1e-5
    avg_loss = np.mean(loss[-14:]) if len(loss) >= 14 else 1e-5
    rs = avg_gain / (avg_loss + 1e-5)
    rsi14 = 100 - (100 / (1 + rs))
    
    prior_vol20 = float(np.mean(volume[-21:-1])) if n >= 21 else float(np.mean(volume))
    vol_ratio = volume[-1] / (prior_vol20 + 1e-5)
    vol5_ratio = (float(np.mean(volume[-5:])) / (float(np.mean(volume[-25:-5])) + 1e-5)
                  if n >= 25 else vol_ratio)
    recent_range = float(np.mean((high[-5:] - low[-5:]) / np.maximum(close[-5:], 1e-9)))
    base_range = (float(np.mean((high[-25:-5] - low[-25:-5]) / np.maximum(close[-25:-5], 1e-9)))
                  if n >= 25 else recent_range)
    range_ratio = recent_range / (base_range + 1e-9)
    
    # 處置股判斷
    is_disposal = ('處置' in stock_info['name']) or ('處置' in stock_info['path'])
    
    # 乖離率
    bias_20 = ((price - ma20.iloc[-1]) / ma20.iloc[-1] * 100) if len(ma20) >= 20 and ma20.iloc[-1] > 0 else 0.0
    bias_5 = ((price - ma5.iloc[-1]) / ma5.iloc[-1] * 100) if len(ma5) >= 5 and ma5.iloc[-1] > 0 else 0.0
    
    # K棒收盤位置 (0.0 = 最低, 1.0 = 收在最高點/漲停)
    bar_range = max(high[-1] - low[-1], 1e-9)
    close_location = (price - low[-1]) / bar_range
    
    # 型態識別與歷史高點
    pattern_name, _ = recognize_pattern(pd.DataFrame({'Close': close, 'High': high, 'Low': low, 'Volume': volume}))
    prior_high = float(np.max(high[:-1])) if n >= 2 else float(high[0])
    is_new_high = (price >= prior_high * 0.99) or (high[-1] >= prior_high)
    dist_to_high = (price - prior_high) / prior_high * 100
    
    # 法人籌碼
    inst = stock_info.get('institutions', [])[:5]
    inst_buy_days = sum(x['total'] > 0 for x in inst) if inst else stock_info.get('inst_buy_days', 0)
    inst_total = sum(x['total'] for x in inst)
    trust_buy_days = sum(x['trust'] > 0 for x in inst)
    margin_5 = sum(x['change'] for x in stock_info.get('margin', [])[:5])
    trailing_pe = stock_info.get('trailing_pe')
    
    pivot = float(np.max(high[-32:-2])) if n >= 32 else float(np.max(high[:-1]))
    distance = (pivot / price - 1) * 100
    stop_loss = float(np.min(low[-10:])) if n >= 10 else price * 0.92

    # ==========================================
    # 1. 🚀 進化版暴漲動能型評分 (Explosive Momentum)
    # ==========================================
    momo_score = 50.0
    momo_reasons = []
    attack_modes = []
    
    # 【模式 A 因子：歷史/波段新高突破 Blue Sky】
    if is_new_high:
        momo_score += 30
        momo_reasons.append("創歷史/波段新高無套牢壓(Blue Sky)")
    elif dist_to_high >= -5.0:
        momo_score += 15
        momo_reasons.append(f"逼近歷史前高(距高{abs(dist_to_high):.1f}%)")
        
    # 【收在當日最高/漲停確認】
    if close_location >= 0.95:
        momo_score += 20
        momo_reasons.append("當日強勢收在最高點/亮燈")
    elif close_location >= 0.80:
        momo_score += 10
        momo_reasons.append("收盤位居高檔強勢區")
        
    # 【5MA 極速仰角】
    if s5 >= 4.0:
        momo_score += min(s5 * 4, 25)
        momo_reasons.append(f"5MA極速仰角+{s5:.2f}%")
    elif s5 >= 2.0:
        momo_score += 12
        momo_reasons.append(f"5MA加速上揚+{s5:.2f}%")
        
    # 【RSI 高檔強勢鈍化】
    if rsi14 >= 75:
        momo_score += 15
        momo_reasons.append(f"RSI強勢主升鈍化({rsi14:.1f})")
    elif 60 <= rsi14 < 75:
        momo_score += 8
        momo_reasons.append(f"RSI多頭攻擊({rsi14:.1f})")
        
    # 【處置股籌碼鎖定軋空】
    if is_disposal:
        momo_score += 15
        momo_reasons.append("處置分盤籌碼高度鎖定(軋空)")
        
    # 【模式 B 因子：上升月線 + 窒息量洗盤點火】(如禾伸堂/信昌電)
    if s20 >= 1.0 and 0 <= bias_20 <= 7.0 and vol_ratio <= 0.65:
        momo_score += 25
        momo_reasons.append(f"上升月線+窒息量洗盤完畢(量比{vol_ratio:.2f}x)")

    # 分析師案例校準後，攻擊型不能只認「已創新高」；至少有三條不同路徑。
    # A. 強勢續攻：前一日已大漲且收在高檔，隔日可能延續，而非一律視為漲多。
    continuation = ret3 >= 8.0 and close_location >= 0.72 and s20_3d >= 2.0
    if continuation:
        momo_score += 18
        momo_reasons.append(f"3日強勢+{ret3:.1f}%且收盤靠近高點")
        attack_modes.append('強勢續攻')

    # B. 洗盤後點火：中期成本線仍明顯上升，但短線回落到月線附近。
    # 這能涵蓋「禾伸堂／信昌電」類型，不會只追已經噴出的股票。
    reset_ignition = (s20_3d >= 2.0 and -15.0 <= ret5 <= 5.0 and
                      0.0 <= bias_20 <= 8.0 and 48.0 <= rsi14 <= 66.0 and
                      (ret1 < 0 or range_ratio <= 0.80))
    if reset_ignition:
        momo_score += 26
        momo_reasons.append(f"上升月線洗盤待點火(5日{ret5:+.1f}%)")
        attack_modes.append('洗盤點火')

    # C. 估值＋籌碼推升：低本益比只在趨勢與籌碼同時支持時加分。
    value_chip = (trailing_pe is not None and 0 < trailing_pe <= 25 and s20_3d >= 2.0 and
                  (trust_buy_days >= 3 or inst_total > 0) and margin_5 <= 0)
    if value_chip:
        momo_score += 20
        momo_reasons.append(f"低PE({trailing_pe:.1f})＋籌碼改善")
        attack_modes.append('估值籌碼')

    if trust_buy_days >= 4:
        momo_score += 8
        momo_reasons.append(f"投信買超{trust_buy_days}/5日")
    if margin_5 < 0:
        momo_score += 4

    if not attack_modes:
        attack_modes.append('一般動能')

    # 類群檔數不是族群強弱；舊版用「同分類股票多」加分會造成假訊號，已取消。

    # ==========================================
    # 2. 🛡️ 穩健防守型評分 (Solid Defensive)
    # ==========================================
    def_score = 50.0
    def_reasons = []
    
    if is_disposal:
        def_score -= 40
    if s20 > 1.0:
        def_score += min(s20 * 3.5, 15)
        def_reasons.append(f"月線穩健上揚+{s20:.2f}%")
    elif s20 > 0:
        def_score += 6
    else:
        def_score -= 20
        
    if price >= ma5.iloc[-1] >= ma10.iloc[-1] >= ma20.iloc[-1]:
        def_score += 10
        def_reasons.append("均線多頭排列")
    elif price < ma20.iloc[-1]:
        def_score -= 25
        
    if 0.5 <= bias_20 <= 6.5:
        def_score += 18
        def_reasons.append(f"剛脫離成本區(月乖離{bias_20:.1f}%)")
    elif 6.5 < bias_20 <= 12.0:
        def_score += 6
    elif bias_20 > 14.0:
        def_score -= 15
    elif bias_20 < 0:
        def_score -= 15
        
    if inst_buy_days >= 4:
        def_score += 20
        def_reasons.append(f"法人連買({inst_buy_days}/5天)")
    elif inst_buy_days >= 2:
        def_score += 10
    else:
        def_score -= 12
        
    if 55 <= rsi14 <= 76:
        def_score += 8
        def_reasons.append(f"RSI健康攻擊區({rsi14:.1f})")
    elif rsi14 > 80:
        def_score -= 8

    # 穩健榜重視估值與回撤控制；PE 只占一部分，避免把弱勢便宜股排前面。
    if trailing_pe is not None and 0 < trailing_pe <= 25:
        def_score += 12
        def_reasons.append(f"本益比相對收斂({trailing_pe:.1f})")
    elif trailing_pe is not None and trailing_pe >= 80:
        def_score -= 8
    if range_ratio <= 0.85:
        def_score += 8
        def_reasons.append("近期波動收斂")
    elif range_ratio > 1.20:
        def_score -= 6
    if margin_5 <= 0:
        def_score += 5
    elif margin_5 > 0 and margin_5 / max(volume[-1], 1) > 0.08:
        def_score -= 6

    return {
        'code': stock_info['code'],
        'name': stock_info['name'],
        'category': stock_info['category'],
        'date': str(df['date'].iloc[-1]),
        'price': price,
        'pivot': pivot,
        'distance': round(distance, 2),
        'stop': stop_loss,
        's5': round(s5, 2),
        's10': round(s10, 2),
        's20': round(s20, 2),
        's20_3d': round(s20_3d, 2),
        'ret1': round(ret1, 2),
        'ret3': round(ret3, 2),
        'ret5': round(ret5, 2),
        'ret10': round(ret10, 2),
        'bias_20': round(bias_20, 2),
        'bias_5': round(bias_5, 2),
        'rsi14': round(rsi14, 1),
        'vol_ratio': round(vol_ratio, 2),
        'vol5_ratio': round(vol5_ratio, 2),
        'range_ratio': round(range_ratio, 2),
        'close_loc': round(close_location, 2),
        'is_new_high': is_new_high,
        'inst_buy_days': inst_buy_days,
        'trust_buy_days': trust_buy_days,
        'margin_5': margin_5,
        'trailing_pe': trailing_pe,
        'is_disposal': is_disposal,
        'momo_score': round(momo_score, 1),
        'def_score': round(def_score, 1),
        'momo_reasons': momo_reasons,
        'attack_modes': attack_modes,
        'def_reasons': def_reasons,
        'pattern': pattern_name
    }


def select_balanced_attack_list(evaluated, limit=10):
    """避免創高股壟斷攻擊榜，為三種可驗證的發動路徑保留名額。"""
    ordered = sorted(evaluated, key=lambda x: x['momo_score'], reverse=True)
    selected = []
    quotas = [('強勢續攻', 4), ('洗盤點火', 3), ('估值籌碼', 2)]
    for mode, quota in quotas:
        candidates = [r for r in ordered if mode in r['attack_modes'] and r not in selected]
        selected.extend(candidates[:quota])
    for r in ordered:
        if len(selected) >= limit:
            break
        if r not in selected:
            selected.append(r)
    return sorted(selected[:limit], key=lambda x: x['momo_score'], reverse=True)


def write_ranked_watchlist(momo_results, def_results):
    cutoff = sorted({r['date'] for r in momo_results})[-1] if momo_results else '未知'
    lines = [
        '# 台股雙軌觀察排行', '',
        f'> 資料截止日：{cutoff}。攻擊榜與穩健榜採不同邏輯；分數是條件吻合度，不是勝率。', '',
        '---', '',
        '## 攻擊型 Top 10',
        '> 同時辨識三種路徑：創高續攻、上升月線洗盤後點火、低估值且籌碼改善。接受較大波動。', '',
        '| 排名 | 股票代號 | 股票名稱 | 路徑 | 收盤價 | 5MA斜率 | RSI(14) | 成交量比 | 動能評分 | 核心動能特徵 |',
        '|:---:|:---:|:---|:---|---:|---:|---:|---:|---:|:---|'
    ]
    for i, r in enumerate(momo_results[:10], 1):
        disp = " *(處置)*" if r['is_disposal'] else ""
        reasons = '；'.join(r['momo_reasons'][:3]) or '強勢動能'
        modes = '／'.join(r['attack_modes'])
        lines.append(f"| **{i}** | `{r['code']}` | **{r['name']}{disp}** | {modes} | {r['price']:.2f} | {r['s5']:+.2f}% | {r['rsi14']} | {r['vol_ratio']:.1f}x | **{r['momo_score']}** | {reasons} |")

    lines.extend([
        '', '---', '',
        '## 穩健型 Top 10',
        '> 著重均線穩定、低乖離、法人承接、波動收斂、融資風險與本益比；不等於保證不跌。', '',
        '| 排名 | 股票代號 | 股票名稱 | 類群 | 收盤價 | 20MA斜率 | 月乖離率 | 法人買超 | 穩健評分 | 核心穩健特徵 |',
        '|:---:|:---:|:---|:---|---:|---:|---:|---:|---:|:---|'
    ])
    for i, r in enumerate(def_results[:10], 1):
        reasons = '；'.join(r['def_reasons'][:3]) or '穩健多頭'
        lines.append(f"| **{i}** | `{r['code']}` | **{r['name']}** | {r['category']} | {r['price']:.2f} | +{r['s20']:.2f}% | +{r['bias_20']:.1f}% | {r['inst_buy_days']}/5天 | **{r['def_score']}** | {reasons} |")

    lines.extend([
        '', '---', '',
        '## 判讀原則', '',
        '1. **創高續攻**：近期漲幅、均線加速度、收盤位置及是否接近新高共同確認。',
        '2. **洗盤後點火**：20 日線仍上升、短線回到成本區、RSI 未破壞且波動開始收斂。',
        '3. **估值與籌碼**：低本益比必須同時搭配趨勢、投信承接或融資下降，不能單獨作為買進理由。',
        '4. 本次規則由單日分析師案例歸納，只能視為待驗證假設，不能宣稱已有穩定命中率。'
    ])
    content = '\n'.join(lines)
    BREAKOUT_OUTPUT_MD.write_text(content, encoding='utf-8')
    OUTPUT_MD.write_text(content, encoding='utf-8')


def main():
    print("=" * 65)
    print("啟動個股雙軌觀察掃描器 (batch_scanner.py)")
    print("=" * 65)
    
    if not REPORTS_DIR.exists():
        print(f"❌ 錯誤：找不到 {REPORTS_DIR} 目錄！")
        return
        
    html_files = list(REPORTS_DIR.glob("**/*.html"))
    print(f"📂 於 reports/ 目錄下搜尋到 {len(html_files)} 個 HTML 檔案")
    
    infos_by_code = {}
    cat_counts = {}
    for p in html_files:
        info = parse_html_report(p)
        if not info:
            continue
        infos_by_code.setdefault(info['code'], info)
        cat = info['category']
        cat_counts[cat] = cat_counts.get(cat, 0) + 1

    # 以最多報告共有的最後交易日為準（本批資料為 08/26）
    date_counts = {}
    for info in infos_by_code.values():
        if info['kline']:
            date = info['kline'][-1]['date']
            date_counts[date] = date_counts.get(date, 0) + 1
    as_of = max(date_counts, key=date_counts.get)
    print(f"📅 本次統一使用資料截止日：{as_of}")

    evaluated = []
    for info in infos_by_code.values():
        res = evaluate_dual_strategy(info, all_category_counts=cat_counts, as_of=as_of)
        if res:
            evaluated.append(res)
            
    if not evaluated:
        print("⚠️ 未解構出有效的個股數據。")
        return

    momo_results = select_balanced_attack_list(evaluated, limit=10)
    def_results = sorted(evaluated, key=lambda x: x['def_score'], reverse=True)
    
    print(f"✅ 完成 {len(evaluated)} 檔個股的雙軌條件評分。")
    print("\n【攻擊型 TOP 10】")
    print(f"{'排名':<4} {'代號':<6} {'股票名稱':<10} {'攻擊路徑':<12} {'收盤價':<10} {'5MA斜率':<10} {'RSI':<8} {'量比':<8} {'動能分數'}")
    print("-" * 85)
    for i, r in enumerate(momo_results[:10], 1):
        disp_name = r['name'] + ("(處置)" if r['is_disposal'] else "")
        mode = '/'.join(r['attack_modes'])
        print(f"#{i:<3} {r['code']:<6} {disp_name:<10} {mode:<12} {r['price']:<10.2f} {r['s5']:+9.2f}% {r['rsi14']:<8.1f} {r['vol_ratio']:<7.1f}x {r['momo_score']}")

    print("\n【穩健型 TOP 10】")
    print(f"{'排名':<4} {'代號':<6} {'股票名稱':<10} {'類群':<8} {'收盤價':<10} {'20MA斜率':<10} {'月乖離':<8} {'法人買':<8} {'穩健分數'}")
    print("-" * 85)
    for i, r in enumerate(def_results[:10], 1):
        print(f"#{i:<3} {r['code']:<6} {r['name']:<10} {r['category']:<8} {r['price']:<10.2f} +{r['s20']:<9.2f}% {r['bias_20']:<7.1f}% {r['inst_buy_days']}/5天   {r['def_score']}")

    write_ranked_watchlist(momo_results, def_results)
    print(f"\n📄 雙軌選股儀表板已寫入至：{OUTPUT_MD}")
    print(f"📄 突破候選池已寫入至：{BREAKOUT_OUTPUT_MD}")


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
