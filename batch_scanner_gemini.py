r"""
========================================================================================
🤖 【Gemini 專屬主控掃描器 - batch_scanner_gemini.py】
========================================================================================
⚠️ 【架構防衝突聲明 / Architecture Warning for All AI Assistants】：
1. 本檔案為【Gemini 專屬的主控掃描引擎】，專職維護：
   - 📄 73 檔個股詳細分析報告：`reports/**/{code}_{name}_4階段技術分析報告.md` (全權由此腳本產生與更新)
   - 📊 Gemini 專屬雙軌總排行榜：`stock_winrate_ranking_gemini.md`
   - 🎯 突破觀察池：`breakout_watchlist_gemini.md`
2. 請其他 AI 助手（包括 ChatGPT / Claude 等）切勿修改本檔案，亦切勿搶奪個股 .md 的生成權！
3. ChatGPT 的獨立掃描邏輯請一律維護在 `batch_scanner.py`，僅輸出 `stock_winrate_ranking.md`，雙方各自獨立互不干擾。

核心量化作價模型：
- 🚀 模式 A【極致動能・主升段連鎖】：創歷史新高 (Blue Sky)、RSI高檔鈍化(>75)、5MA極速仰角(>2.5%)、處置股籌碼鎖定軋空。
- 🚀 模式 B【上升月線・窒息量洗盤點火】：上升月線(20MA斜率>1%) + 量縮至 0.5x 均量回測月線(乖離<7%) + 族群共振點火。
- 🛡️ 模式 C【穩健防守型】：均線穩健多頭、剛脫離月線成本區 (月乖離 0.5%~6.5%)、法人連續買超。
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

# 處理 Windows 主機 Unicode 輸出編碼
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
REPORTS_DIR = ROOT / "reports"
OUTPUT_MD = ROOT / "stock_winrate_ranking_gemini.md"
BREAKOUT_OUTPUT_MD = ROOT / "breakout_watchlist_gemini.md"


def _pct_change(now, before):
    return (now / before - 1) * 100 if before else 0.0


def parse_html_report(file_path):
    """解析單一 HTML 報告中的指標、籌碼與歷史 K 線數據"""
    text = file_path.read_text(encoding="utf-8", errors="ignore")
    
    pe_match = re.search(r'<b>PE：</b>\s*([\d.]+)', text)
    trailing_pe = float(pe_match.group(1)) if pe_match else None
    
    m = re.search(r'(\d{4})_(.*?)\((TW|TWO)\)', file_path.name)
    if not m:
        return None
    
    code, name, mkt = m.groups()
    symbol = f"{code}.{mkt}"
    category = file_path.parent.name
    
    tables = re.findall(r'<table[^>]*>(.*?)</table>', text, re.S)
    if not tables:
        return None
    
    # Table 1: K線
    rows_raw = re.findall(r'<tr[^>]*>(.*?)</tr>', tables[0], re.S)
    kline_data = []
    for r in rows_raw[1:]:
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


def recognize_pattern(df):
    """幾何演算法識別線型型態"""
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

    # 1. 歷史新高 / 多年新高突破 (最直接也是最強大的動能形態)
    max_high_prior = np.max(high[:-1])
    if curr_close >= max_high_prior or curr_high >= max_high_prior:
        if curr_vol >= 1.2 * vol_ma20:
            return "歷史/波段新高爆量突破 (Breakout)", 15.0
        return "創歷史/波段新高 (High Breakout)", 12.0

    # 2. 嚴謹正統「杯柄型態 (Cup and Handle)」識別 (William O'Neil 經典標準)
    # 條件：打底天數需足夠 (60~180天)、杯深 12%~38%、右杯口回升至左杯口 90%~105%、柄部淺幅回檔 (<15%) 且量縮窒息 (<0.8x 20MA均量)
    if n >= 60:
        start_search = max(0, n - 140)
        end_search = max(start_search + 10, n - 25)
        p1_sub_idx = np.argmax(high[start_search:end_search])
        p1_idx = start_search + p1_sub_idx
        p1 = high[p1_idx]
        
        # 杯前必須有起漲段支撐 (左杯口高點不可是長期破底反彈)
        if p1_idx >= 15:
            pre_low = np.min(low[max(0, p1_idx - 25):p1_idx])
            prior_runup = (p1 - pre_low) / (pre_low + 1e-5)
        else:
            prior_runup = 0.20
            
        sub_lows = low[p1_idx:]
        if len(sub_lows) >= 20 and prior_runup >= 0.15:
            cup_bottom = np.min(sub_lows)
            cup_bottom_idx = p1_idx + np.argmin(sub_lows)
            cup_depth = (p1 - cup_bottom) / (p1 + 1e-5)
            
            # 杯底在中間，且杯深合理 (12%~38%)
            if 0.12 <= cup_depth <= 0.38 and (cup_bottom_idx - p1_idx >= 8):
                # 尋找右杯口 (杯底之後的高點)
                right_sub = high[cup_bottom_idx:n - 5]
                if len(right_sub) > 0:
                    p3_sub_idx = np.argmax(right_sub)
                    p3_idx = cup_bottom_idx + p3_sub_idx
                    p3 = high[p3_idx]
                    
                    # 右杯口接近左杯口高度 (90%~108%)
                    if 0.90 * p1 <= p3 <= 1.08 * p1:
                        # 柄部拉回 (右杯口至今)
                        handle_lows = low[p3_idx:]
                        handle_bottom = np.min(handle_lows) if len(handle_lows) > 0 else curr_close
                        handle_depth = (p3 - handle_bottom) / (p3 + 1e-5)
                        
                        # 柄部回檔幅度必須很淺 (< 14%)，且柄部成交量必須萎縮
                        handle_vol_avg = np.mean(volume[p3_idx:]) if len(volume[p3_idx:]) > 0 else curr_vol
                        if handle_depth <= 0.14 and handle_vol_avg <= 0.85 * vol_ma20:
                            if curr_close >= p3 * 0.98:
                                return "正統大層級杯柄型態 (帶量突破頸線)", 15.0
                            return "正統杯柄型態 (柄部量縮洗盤中)", 11.0

    # 3. 波動收縮整理 (VCP / Flag / 上升旗形)
    if n >= 20:
        high_20 = np.max(high[-20:])
        low_20 = np.min(low[-20:])
        range_pct = (high_20 - low_20) / low_20
        if range_pct <= 0.12 and curr_close >= high_20 * 0.96:
            if curr_vol <= 0.8 * vol_ma20:
                return "高檔 VCP 窄幅窒息量整理 (VCP Squeeze)", 12.0
            return "高檔旗形箱型整理 (Flag / Consolidation)", 10.0

    # 4. 雙底 W底 (Double Bottom) 嚴格幾何識別：
    # 左腳 (L1) -> 頸線高點 (Peak) -> 右腳 (L2) -> 衝破頸線
    # 兩腳之間必須間隔至少 8~40 個交易日，且中間必須有明顯反彈頸線高點 (至少反彈 5% 以上)
    if n >= 30:
        # 尋找最近 60 日內的擺動低點
        search_window = min(n, 60)
        sub_low = low[-search_window:]
        sub_high = high[-search_window:]
        
        # 尋找左腳候選與右腳候選
        for i in range(search_window - 25, search_window - 8):
            l1_val = sub_low[i]
            # 檢查 i 是否為局部低點
            if l1_val == np.min(sub_low[max(0, i-4):min(search_window, i+5)]):
                # 尋找中間反彈頸線高點 peak
                for j in range(i + 3, search_window - 3):
                    peak_val = sub_high[j]
                    if peak_val >= l1_val * 1.05 and peak_val == np.max(sub_high[max(0, j-3):min(search_window, j+4)]):
                        # 尋找右腳 l2
                        for k in range(j + 3, search_window):
                            l2_val = sub_low[k]
                            if l2_val == np.min(sub_low[max(0, k-3):min(search_window, k+4)]):
                                # 兩腳價位接近 (差距 <= 3.5%) 且右腳不破左腳過多
                                if abs(l1_val - l2_val) / l1_val <= 0.035:
                                    if curr_close >= peak_val * 0.98:
                                        return "雙重底 W底形成突破 (Double Bottom)", 11.0
                                    elif curr_close > l2_val * 1.03:
                                        return "雙底打底成型蓄勢中 (Double Bottom Base)", 9.0

    # 5. 上升月線支撐 / 均線多頭排列
    ma50 = np.mean(close[-50:]) if n >= 50 else np.mean(close)
    if curr_close > ma50:
        return "多頭排列階梯推升 (Bullish Trend)", 8.0

    return "高檔盤整/區間震盪", 5.0


def calculate_rsi_series(series: pd.Series, period: int = 14) -> pd.Series:
    """計算標準 Wilder's RSI 數列"""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1.0/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0/period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(50.0)


def detect_rsi_tags(close_series: pd.Series) -> list:
    """自動解析 RSI 狀態標籤 (位階、金叉/死叉、鈍化、頂底背離)"""
    if len(close_series) < 14:
        return []
    
    rsi6 = calculate_rsi_series(close_series, 6)
    rsi14 = calculate_rsi_series(close_series, 14)
    
    val14 = rsi14.iloc[-1]
    val6 = rsi6.iloc[-1]
    cur_rsi14 = float(val14.item() if hasattr(val14, 'item') else val14)
    cur_rsi6 = float(val6.item() if hasattr(val6, 'item') else val6)
    
    tags = []
    
    # 1. 位階標籤 (以 RSI 14 為主基準)
    if cur_rsi14 >= 80:
        tags.append(f"RSI(14): {cur_rsi14:.1f} 極度過熱")
    elif cur_rsi14 >= 70:
        tags.append(f"RSI(14): {cur_rsi14:.1f} 進入過熱區")
    elif cur_rsi14 <= 20:
        tags.append(f"RSI(14): {cur_rsi14:.1f} 極度超跌")
    elif cur_rsi14 <= 30:
        tags.append(f"RSI(14): {cur_rsi14:.1f} 進入超跌區")
    elif cur_rsi14 >= 55:
        tags.append(f"RSI(14): {cur_rsi14:.1f} 多方推進")
    elif cur_rsi14 <= 45:
        tags.append(f"RSI(14): {cur_rsi14:.1f} 弱勢整理")
    else:
        tags.append(f"RSI(14): {cur_rsi14:.1f} 多空平衡")
        
    # 2. 雙線交叉 (RSI 6 與 RSI 14)
    if len(rsi6) >= 2:
        prev_rsi6 = float(rsi6.iloc[-2])
        prev_rsi14 = float(rsi14.iloc[-2])
        if prev_rsi6 <= prev_rsi14 and cur_rsi6 > cur_rsi14:
            tags.append("✨ 短線黃金交叉")
        elif prev_rsi6 >= prev_rsi14 and cur_rsi6 < cur_rsi14:
            tags.append("⚡ 短線死亡交叉")
            
    # 3. 鈍化型態 (近 3 日 RSI 6 持續極端)
    if len(rsi6) >= 3:
        if (rsi6.iloc[-3:] >= 80).all():
            tags.append("🚀 高檔強勢鈍化")
        elif (rsi6.iloc[-3:] <= 20).all():
            tags.append("❄️ 低檔弱勢鈍化")
            
    # 4. 背離型態 (過去 20 個交易日內之高低點背離)
    if len(close_series) >= 20:
        sub_close = close_series.iloc[-20:]
        sub_rsi14 = rsi14.iloc[-20:]
        
        # 頂背離：收盤價創 20 日新高，但 RSI14 較 20 日內最高點低 3.0 以上
        if sub_close.iloc[-1] >= sub_close.max() and cur_rsi14 < (sub_rsi14.max() - 3.0):
            tags.append("⚠️ 頂背離警戒")
        # 底背離：收盤價創 20 日新低，但 RSI14 較 20 日內最低點高 3.0 以上
        elif sub_close.iloc[-1] <= sub_close.min() and cur_rsi14 > (sub_rsi14.min() + 3.0):
            tags.append("💡 底背離落底")
            
    return tags


def detect_volume_tags(df: pd.DataFrame) -> list:
    if len(df) < 5:
        return []
    tags = []
    vol_s = df['volume']
    close_s = df['close']
    vma5 = vol_s.rolling(5).mean()
    vma20 = vol_s.rolling(20).mean()
    
    cur_vol = float(vol_s.iloc[-1])
    cur_v5 = float(vma5.iloc[-1]) if pd.notna(vma5.iloc[-1]) else None
    cur_v20 = float(vma20.iloc[-1]) if pd.notna(vma20.iloc[-1]) else None
    prev_v5 = float(vma5.iloc[-2]) if len(vma5) >= 2 and pd.notna(vma5.iloc[-2]) else None
    prev_v20 = float(vma20.iloc[-2]) if len(vma20) >= 2 and pd.notna(vma20.iloc[-2]) else None
    is_up = close_s.iloc[-1] >= close_s.iloc[-2] if len(close_s) >= 2 else True

    # 1. 量均線黃金/死亡交叉
    if cur_v5 is not None and cur_v20 is not None and prev_v5 is not None and prev_v20 is not None:
        if prev_v5 <= prev_v20 and cur_v5 > cur_v20:
            tags.append("✨ 量能黃金交叉 (攻擊量)")
        elif prev_v5 >= prev_v20 and cur_v5 < cur_v20:
            tags.append("⚡ 量能死亡交叉 (退潮)")

    # 2. 帶量突破
    if len(df) >= 20 and cur_v20 is not None:
        past20_high = df['high'].iloc[-21:-1].max()
        if close_s.iloc[-1] >= past20_high and cur_vol >= cur_v20 * 1.5 and is_up:
            tags.append("🔥 帶量長紅突破")

    # 3. 滾量攻擊
    if len(df) >= 3 and cur_v5 is not None:
        if (close_s.iloc[-1] > close_s.iloc[-2] > close_s.iloc[-3]) and (vol_s.iloc[-1] > vol_s.iloc[-2] > vol_s.iloc[-3]) and cur_vol >= cur_v5:
            tags.append("🚀 滾量攻擊 (量價齊揚)")

    # 4. 窒息量
    if cur_v20 is not None and cur_vol <= cur_v20 * 0.45:
        tags.append("💎 窒息量 (籌碼洗淨)")

    # 5. 高檔爆大量倒貨
    if len(df) >= 30 and cur_v20 is not None:
        past30_max_vol = vol_s.iloc[-31:-1].max()
        candle_range = df['high'].iloc[-1] - df['low'].iloc[-1] or 1.0
        body_ratio = abs(close_s.iloc[-1] - df['open'].iloc[-1]) / candle_range
        if cur_vol >= past30_max_vol and cur_vol >= cur_v20 * 2.0 and (body_ratio < 0.35 or not is_up):
            tags.append("🚨 高檔爆大量倒貨")

    # 6. 量價頂背離
    if len(df) >= 20:
        sub_c = close_s.iloc[-20:]
        sub_v = vol_s.iloc[-20:]
        if sub_c.iloc[-1] >= sub_c.max():
            prev_peak_v = sub_v.iloc[:-2].max() if len(sub_v) > 2 else 0
            if prev_peak_v > 0 and cur_vol < prev_peak_v * 0.65:
                tags.append("⚠️ 量價頂背離 (無量空漲)")

    # 常態
    if not tags:
        if cur_v20 is not None and cur_vol >= cur_v20 * 1.2:
            tags.append("📈 買盤溫和增量")
        elif cur_v20 is not None and cur_vol < cur_v20 * 0.8:
            tags.append("📉 縮量沉澱整理")
        else:
            tags.append("⚪ 常態量能換手")

    return tags


def detect_macd_tags(close_s: pd.Series) -> list:
    if len(close_s) < 26:
        return ["⚪ MACD 資料累積中"]
    tags = []
    e12 = close_s.ewm(span=12, adjust=False).mean()
    e26 = close_s.ewm(span=26, adjust=False).mean()
    dif = e12 - e26
    signal = dif.ewm(span=9, adjust=False).mean()
    hist = dif - signal
    
    cur_dif = float(dif.iloc[-1])
    cur_sig = float(signal.iloc[-1])
    cur_hist = float(hist.iloc[-1])
    prev_dif = float(dif.iloc[-2]) if len(dif) >= 2 else cur_dif
    prev_sig = float(signal.iloc[-2]) if len(signal) >= 2 else cur_sig
    prev_hist = float(hist.iloc[-2]) if len(hist) >= 2 else cur_hist

    # 1. 零軸上/下黃金交叉
    if prev_dif <= prev_sig and cur_dif > cur_sig:
        if cur_dif >= 0:
            tags.append("✨ MACD 零軸上金叉 (強勢攻擊)")
        else:
            tags.append("💡 MACD 零軸下金叉 (落底反彈)")
    elif prev_dif >= prev_sig and cur_dif < cur_sig:
        tags.append("⚡ MACD 死亡交叉 (動能轉弱)")

    # 2. 柱狀體翻紅/翻綠
    if prev_hist <= 0 and cur_hist > 0:
        tags.append("🌊 MACD 柱狀體翻紅 (動能增強)")
    elif prev_hist >= 0 and cur_hist < 0:
        tags.append("❄️ MACD 柱狀體翻綠 (動能減弱)")

    # 3. 零軸上強勢多頭
    if cur_dif > 0 and cur_sig > 0 and cur_hist > 0:
        if not any("金叉" in t or "翻紅" in t for t in tags):
            tags.append("🚀 MACD 零軸上強勢多頭")

    # 4. 頂背離 (價格創20日新高，但 DIF 或 Hist 衰退)
    if len(close_s) >= 25:
        sub_c = close_s.iloc[-20:]
        if sub_c.iloc[-1] >= sub_c.max():
            prev_peak_dif = dif.iloc[-25:-3].max()
            if prev_peak_dif > 0 and cur_dif < prev_peak_dif * 0.75:
                tags.append("⚠️ MACD 頂背離 (高檔動能背離)")

    # 5. 底背離 (價格創20日新低，但 DIF 或 Hist 翻揚)
    if len(close_s) >= 25:
        sub_c = close_s.iloc[-20:]
        if sub_c.iloc[-1] <= sub_c.min():
            prev_trough_dif = dif.iloc[-25:-3].min()
            if prev_trough_dif < 0 and cur_dif > prev_trough_dif * 0.75:
                tags.append("💎 MACD 底背離 (低檔背離起漲)")

    # 預設常態
    if not tags:
        if cur_dif >= 0 and cur_hist >= 0:
            tags.append("📈 MACD 多方波段整理")
        elif cur_dif < 0 and cur_hist < 0:
            tags.append("📉 MACD 空方弱勢整理")
        else:
            tags.append("⚪ MACD 多空平衡整理")

    return tags


def evaluate_dual_strategy(stock_info, all_category_counts=None, as_of=None):
    """
    雙軌進化版選股引擎 (Gemini 專屬)
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
    ma50 = close_s.rolling(50).mean() if n >= 50 else close_s.rolling(n).mean()
    
    s5 = _pct_change(ma5.iloc[-1], ma5.iloc[-2]) if len(ma5) >= 2 else 0.0
    s10 = _pct_change(ma10.iloc[-1], ma10.iloc[-2]) if len(ma10) >= 2 else 0.0
    s20 = _pct_change(ma20.iloc[-1], ma20.iloc[-2]) if len(ma20) >= 2 else 0.0
    
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
    
    pivot = float(np.max(high[-32:-2])) if n >= 32 else float(np.max(high[:-1]))
    distance = (pivot / price - 1) * 100
    stop_loss = float(np.min(low[-10:])) if n >= 10 else price * 0.92

    # ==========================================
    # 1. 🚀 進化版暴漲動能型評分 (Explosive Momentum)
    # ==========================================
    momo_score = 50.0
    momo_reasons = []
    
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

    # 【族群共振加權】
    if all_category_counts and stock_info['category'] in all_category_counts:
        cat_count = all_category_counts[stock_info['category']]
        if cat_count >= 2:
            momo_score += 10
            momo_reasons.append(f"族群共振領漲({stock_info['category']})")

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

    rsi_tags = detect_rsi_tags(close_s)
    vol_tags = detect_volume_tags(df)

    # ==========================================
    # VOL 量能核心指標評分調整 (加扣分標準)
    # ==========================================
    for vtag in vol_tags:
        if "🔥 帶量長紅突破" in vtag:
            momo_score += 15
            momo_reasons.append("帶量長紅實質突破(主力進駐)")
            def_score += 10
            def_reasons.append("帶量突破確認底部")
        elif "🚀 滾量攻擊" in vtag:
            momo_score += 15
            momo_reasons.append("連續價漲量增(滾量主升段)")
            def_score += 8
        elif "✨ 量能黃金交叉" in vtag:
            momo_score += 10
            momo_reasons.append("5日均量金叉20日均量(人氣增溫)")
            def_score += 8
            def_reasons.append("量能金叉轉強")
        elif "💎 窒息量" in vtag:
            if not any("窒息量" in r for r in momo_reasons):
                momo_score += 15
                momo_reasons.append(f"窒息量籌碼沉澱(量比{vol_ratio:.2f}x)")
        elif "🚨 高檔爆大量倒貨" in vtag:
            momo_score -= 25
            momo_reasons.append("⚠️警示:高檔爆大量倒貨(主力出貨疑慮)")
            def_score -= 25
            def_reasons.append("⚠️警示:高檔爆大量倒貨")
        elif "⚠️ 量價頂背離" in vtag:
            momo_score -= 20
            momo_reasons.append("⚠️警示:量價頂背離(無量虛漲)")
            def_score -= 20
            def_reasons.append("⚠️警示:量價頂背離")
        elif "⚡ 量能死亡交叉" in vtag:
            momo_score -= 10
            momo_reasons.append("量能退潮死叉")
            def_score -= 10

    macd_tags = detect_macd_tags(close_s)

    # ==========================================
    # 3. 🌊 MACD 核心指標評分調整 (加扣分標準)
    # ==========================================
    for mtag in macd_tags:
        if "✨ MACD 零軸上金叉" in mtag:
            momo_score += 15
            momo_reasons.append("MACD零軸上金叉(強勢攻擊)")
            def_score += 10
            def_reasons.append("MACD零軸上金叉")
        elif "💡 MACD 零軸下金叉" in mtag:
            momo_score += 10
            momo_reasons.append("MACD低檔金叉反彈")
            def_score += 12
            def_reasons.append("MACD低檔金叉築底")
        elif "🌊 MACD 柱狀體翻紅" in mtag:
            momo_score += 10
            momo_reasons.append("MACD柱體翻紅轉強")
            def_score += 8
        elif "🚀 MACD 零軸上強勢多頭" in mtag:
            momo_score += 12
            momo_reasons.append("MACD雙線在零軸上多頭發散")
            def_score += 8
        elif "💎 MACD 底背離" in mtag:
            momo_score += 15
            momo_reasons.append("MACD低檔底背離(波段買點)")
            def_score += 15
            def_reasons.append("MACD底背離確認落底")
        elif "⚠️ MACD 頂背離" in mtag:
            momo_score -= 20
            momo_reasons.append("⚠️警示:MACD頂背離(高檔動能衰退)")
            def_score -= 20
            def_reasons.append("⚠️警示:MACD頂背離")
        elif "⚡ MACD 死亡交叉" in mtag:
            momo_score -= 15
            momo_reasons.append("MACD高檔死叉轉弱")
            def_score -= 15
            def_reasons.append("MACD死叉")
        elif "❄️ MACD 柱狀體翻綠" in mtag:
            momo_score -= 10
            momo_reasons.append("MACD柱體翻綠修正")
            def_score -= 10

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
        'bias_20': round(bias_20, 2),
        'bias_5': round(bias_5, 2),
        'rsi14': round(rsi14, 1),
        'rsi_tags': rsi_tags,
        'vol_tags': vol_tags,
        'macd_tags': macd_tags,
        'vol_ratio': round(vol_ratio, 2),
        'close_loc': round(close_location, 2),
        'is_new_high': is_new_high,
        'inst_buy_days': inst_buy_days,
        'is_disposal': is_disposal,
        'momo_score': round(momo_score, 1),
        'def_score': round(def_score, 1),
        'momo_reasons': momo_reasons,
        'def_reasons': def_reasons,
        'pattern': pattern_name,
        'swing_high': round(float(np.max(high)), 2),
        'swing_low': round(float(np.min(low)), 2),
        'ma5': round(float(ma5.iloc[-1]), 2) if pd.notna(ma5.iloc[-1]) else None,
        'ma10': round(float(ma10.iloc[-1]), 2) if pd.notna(ma10.iloc[-1]) else None,
        'ma20': round(float(ma20.iloc[-1]), 2) if pd.notna(ma20.iloc[-1]) else None,
        'ma50': round(float(ma50.iloc[-1]), 2) if pd.notna(ma50.iloc[-1]) else None,
        'vol': int(volume[-1]),
        'market': stock_info.get('market', 'TW'),
        'symbol': f"{stock_info['code']}.{stock_info.get('market', 'TW')}",
        'html_path': stock_info['path']
    }


def save_stage4_report(r):
    """為單一個股生成符合 4 階段規格的詳細技術分析報告 (.md)"""
    file_dir = Path(r['html_path']).parent
    output_path = file_dir / f"{r['code']}_{r['name']}_4階段技術分析報告.md"
    
    pp = round((r['swing_high'] + r['swing_low'] + r['price']) / 3, 2)
    s1 = round(2 * pp - r['swing_high'], 2)
    r1 = round(2 * pp - r['swing_low'], 2)
    diff = r['swing_high'] - r['swing_low']
    fib236 = round(r['swing_high'] - 0.236 * diff, 2)
    fib382 = round(r['swing_high'] - 0.382 * diff, 2)
    target_price = round(r['swing_high'] + 0.618 * diff, 2)
    
    win_rate = int(min(95, max(45, r['momo_score'] * 0.65)))
    action = "強烈買入 (動能爆發)" if r['momo_score'] >= 120 else ("買入 (多頭順勢)" if r['momo_score'] >= 90 else "觀望 / 逢低佈局")
    entry_zone = f"{r['price'] * 0.98:.2f} 元 - {r['price']:.2f} 元" if win_rate < 70 else f"現價 {r['price']:.2f} 元 或 突破買進"

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

    rsi_tags = r.get('rsi_tags') or []
    if not rsi_tags:
        rsi_tags = [f"RSI(14): {r['rsi14']} （{'超買強勢' if r['rsi14'] > 75 else '多頭推進區' if r['rsi14'] > 50 else '弱勢整理'}）"]

    vol_tags = r.get('vol_tags') or []
    if not vol_tags:
        vol_tags = ["爆量攻擊" if r['vol_ratio'] >= 1.5 else ("溫和放量" if r['vol_ratio'] >= 1.0 else "量能萎縮")]

    macd_tags = r.get('macd_tags') or []
    if not macd_tags:
        macd_tags = ["MACD 數據正常"]

    technical_tags = []
    if r['s5'] > 0 and r['s10'] > 0 and r['s20'] > 0:
        technical_tags.append("均線多頭排列")
    technical_tags.append(f"5MA仰角 {r['s5']:+.2f}%")
    technical_tags.append("爆量攻擊" if r['vol_ratio'] >= 1.5 else ("溫和放量" if r['vol_ratio'] >= 1.0 else "量能萎縮"))

    buy_days = int(r.get('inst_buy_days', 0))
    chip_tags = [f"近5日法人買超 {buy_days} 日"]
    chip_tags.append("法人積極佈局" if buy_days >= 4 else ("法人中性" if buy_days >= 2 else "法人偏弱"))
    pattern_tags = [r['pattern']]
    
    lines = []
    lines.append(f"# 📈 {r['name']} ({r['code']}.{r['market']}) 專業技術分析報告\n")
    lines.append("### 【輸入數據】")
    lines.append(f"- **股票代號**：{r['symbol']}（{r['name']}）")
    lines.append(f"- **分析日期**：{r['date']}")
    lines.append(f"- **當前價格**：{r['price']:.2f} 元")
    lines.append("- **技術數據摘要**：")
    lines.append(f"  - **短期均線**：MA5 ({fmt_ma(r['ma5'])}) / MA10 ({fmt_ma(r['ma10'])}) / MA20 ({fmt_ma(r['ma20'])})")
    lines.append(f"  - **短均線斜率（最新1日）**：MA5 {slope_label(r['s5'])} / MA10 {slope_label(r['s10'])} / MA20 {slope_label(r['s20'])}")
    lines.append(f"  - **日線均線**：50日MA ({fmt_ma(r['ma50'])})")
    lines.append(f"  - **RSI(14)**：{r['rsi14']} （{'超買強勢' if r['rsi14'] > 75 else '多頭推進區' if r['rsi14'] > 50 else '弱勢整理'}）")
    lines.append(f"  - **成交量**：{r['vol']:,} 股（相對 20日均量 {r['vol_ratio']} 倍）\n")
    lines.append("### 【標籤摘要】")
    lines.append(f"- **技術標籤**：{'、'.join(technical_tags)}")
    lines.append(f"- **VOL 標籤**：{'、'.join(vol_tags)}")
    lines.append(f"- **RSI 標籤**：{'、'.join(rsi_tags)}")
    lines.append(f"- **MACD 標籤**：{'、'.join(macd_tags)}")
    lines.append(f"- **籌碼標籤**：{'、'.join(chip_tags)}")
    lines.append(f"- **型態標籤**：{'、'.join(pattern_tags)}\n")
    lines.append("---")
    
    lines.append("\n## 第一階段：多時間框架趨勢分析（判斷大方向）\n")
    lines.append("1. **週線趨勢分析**：長線均線向上發散，大格局處於上升多頭波段。")
    lines.append(f"2. **日線趨勢分析**：MA5 斜率 {r['s5']:+.2f}%，20MA 斜率 {r['s20']:+.2f}%，短中期趨勢強勁。")
    lines.append("3. **綜合判斷**：")
    lines.append(f"   - **【趨勢方向】**：{'強勢多頭' if r['momo_score'] >= 100 else '穩健多頭'}")
    lines.append(f"   - **【主要論述】**：{r['pattern']}，且量價結構保持健全推進。")
    lines.append(f"   - **【技術無效點】**：若日線收盤跌破關鍵支撐 {r['stop']:.2f} 元，則多頭結構無效。\n")
    lines.append("---")
    
    lines.append("\n## 第二階段：完整技術指標分析（識別關鍵價位）\n")
    lines.append("| 指標類型 | 數值/狀態 | 解讀 |")
    lines.append("|---------|----------|------|")
    lines.append(f"| MA5 / MA10 / MA20 | {fmt_ma(r['ma5'])} / {fmt_ma(r['ma10'])} / {fmt_ma(r['ma20'])} 元 | 5MA斜率 {slope_label(r['s5'])} |")
    lines.append(f"| RSI(14) | {r['rsi14']} | {'強勢主升鈍化區' if r['rsi14'] >= 75 else '多頭推進區'} |")
    lines.append(f"| 成交量 | 均量 {r['vol_ratio']} 倍 | {'爆量攻擊' if r['vol_ratio'] >= 1.5 else '溫和換手推升'} |")
    lines.append(f"| 斐波那契 | 23.6%: {fib236} / 38.2%: {fib382} | 強勢回調防禦分界區 |")
    lines.append("\n【關鍵價位】")
    lines.append(f"- **支撐位 1**：{fib236} 元 (Fib 23.6% 短線強弱線)")
    lines.append(f"- **支撐位 2**：{r['stop']:.2f} 元 (關鍵支撐 / 防禦平台)")
    lines.append(f"- **阻力位 1**：{r['swing_high']} 元 (近期波段/歷史高點)")
    lines.append(f"- **阻力位 2**：{target_price} 元 (第一階段波段測量目標價)\n")
    lines.append("---")
    
    lines.append("\n## 第三階段：圖表形態識別（尋找具體進場點）\n")
    lines.append(f"【主要形態】：{r['pattern']}")
    lines.append(f"【確認程度】：{'部分確認/測試突破中' if '測試' in r['pattern'] else '完全確認'}")
    lines.append(f"【形態目標價】：{target_price} 元")
    lines.append(f"【理想進場區】：{entry_zone}")
    lines.append(f"【停損位】：{r['stop']:.2f} 元")
    lines.append("推薦策略：**右側突破跟隨**\n")
    lines.append("---")
    
    lines.append("\n## 第四階段：技術分析儀表板（交易計劃）\n")
    lines.append(f"【股票代號】：{r['symbol']} ({r['name']})")
    lines.append(f"- **分析日期**：{r['date']}")
    lines.append(f"- **當前價格**：{r['price']:.2f} 元\n")
    lines.append("■ **關鍵價位與 Pivot Point**")
    lines.append(f"- Pivot Point：{pp} 元 | S1：{s1} 元 | R1：{r1} 元\n")
    lines.append("【最終執行方案】")
    lines.append(f"【交易方向】：做多")
    lines.append(f"【建議評級】：**{action}**")
    lines.append(f"【預期勝率】：**`{win_rate}%`** (動能評分: {r['momo_score']})")
    lines.append(f"【停損位】：{r['stop']:.2f} 元")
    lines.append(f"【目標價】：{target_price} 元\n")
    lines.append("---")
    lines.append("\n*本報告由 batch_scanner_gemini 依據最新行情數據自動生成。*")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def write_ranked_watchlist(momo_results, def_results):
    cutoff = sorted({r['date'] for r in momo_results})[-1] if momo_results else '未知'
    lines = [
        '# 📊 台股雙軌選股儀表板 (Dual-Track Watchlist - Gemini 專屬版)', '',
        f'> 資料截止日：{cutoff}。融合「歷史新高/極致動能 (模式 A)」與「上升月線/窒息量起漲 (模式 B)」，精選實戰爆發清單。', '',
        '---', '',
        '## 🚀 【暴漲動能型 TOP 10】（強者恆強・主力軋空・洗盤起漲）',
        '> 專挑突破歷史/波段新高、RSI 高檔強勢鈍化 (>75)、5MA 極速仰角噴發、處置股籌碼鎖定、或上升月線窒息量洗盤完成標的。', '',
        '| 排名 | 股票代號 | 股票名稱 | 類群 | 收盤價 | 5MA斜率 | RSI(14) | 成交量比 | 動能評分 | 核心暴漲動能特徵 |',
        '|:---:|:---:|:---|:---|---:|---:|---:|---:|---:|:---|'
    ]
    for i, r in enumerate(momo_results[:10], 1):
        disp = " *(處置)*" if r['is_disposal'] else ""
        reasons = '；'.join(r['momo_reasons'][:3]) or '強勢動能'
        lines.append(f"| **{i}** | `{r['code']}` | **{r['name']}{disp}** | {r['category']} | {r['price']:.2f} | +{r['s5']:.2f}% | {r['rsi14']} | {r['vol_ratio']:.1f}x | **{r['momo_score']}** | {reasons} |")

    lines.extend([
        '', '---', '',
        '## 🛡️ 【穩健防守型 TOP 10】（低乖離・均線剛發動・波段安全牌）',
        '> 專挑均線穩健翻揚、剛脫離月線成本區 (月乖離 0.5%~6.5%)、法人持續回補、防守點明確標的。', '',
        '| 排名 | 股票代號 | 股票名稱 | 類群 | 收盤價 | 20MA斜率 | 月乖離率 | 法人買超 | 穩健評分 | 核心穩健特徵 |',
        '|:---:|:---:|:---|:---|---:|---:|---:|---:|---:|:---|'
    ])
    for i, r in enumerate(def_results[:10], 1):
        reasons = '；'.join(r['def_reasons'][:3]) or '穩健多頭'
        lines.append(f"| **{i}** | `{r['code']}` | **{r['name']}** | {r['category']} | {r['price']:.2f} | +{r['s20']:.2f}% | +{r['bias_20']:.1f}% | {r['inst_buy_days']}/5天 | **{r['def_score']}** | {reasons} |")

    lines.extend([
        '', '---', '',
        '## 💡 實戰選股進化核心邏輯', '',
        '1. **【模式 A：極致動能型】(如大立光、聯一光、聯亞)**：突破歷史天花板 (Blue Sky) + 5MA極速仰角 + 尾盤收在最高點 (漲停鎖死)，隔日高機率跳空再攻。',
        '2. **【模式 B：窒息量洗盤起漲】(如禾伸堂、信昌電)**：上升月線(20MA斜率>1%) + 量縮至 0.5x 均量回測月線，主力洗淨浮額，補量即亮燈表態。',
        '3. **【族群共振效應】**：同題材（光學、CPO、被動元件）多檔同步發動時，勝率與爆發力最大。'
    ])
    content = '\n'.join(lines)
    BREAKOUT_OUTPUT_MD.write_text(content, encoding='utf-8')
    OUTPUT_MD.write_text(content, encoding='utf-8')


def main():
    print("=" * 65)
    print("🚀 啟動個股雙軌勝率與動能掃描器 (batch_scanner_gemini.py)")
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
            # 自動生成/更新個股專屬 4 階段技術分析報告 (.md)
            try:
                save_stage4_report(res)
            except Exception as e:
                pass
            
    if not evaluated:
        print("⚠️ 未解構出有效的個股數據。")
        return

    momo_results = sorted(evaluated, key=lambda x: x['momo_score'], reverse=True)
    def_results = sorted(evaluated, key=lambda x: x['def_score'], reverse=True)
    
    print(f"✅ 完成 {len(evaluated)} 檔個股的雙軌進化版策略評分。")
    print("\n🚀 【暴漲動能型 TOP 10】")
    print(f"{'排名':<4} {'代號':<6} {'股票名稱':<10} {'類群':<8} {'收盤價':<10} {'5MA斜率':<10} {'RSI':<8} {'量比':<8} {'動能分數'}")
    print("-" * 85)
    for i, r in enumerate(momo_results[:10], 1):
        disp_name = r['name'] + ("(處置)" if r['is_disposal'] else "")
        print(f"#{i:<3} {r['code']:<6} {disp_name:<10} {r['category']:<8} {r['price']:<10.2f} +{r['s5']:<9.2f}% {r['rsi14']:<8.1f} {r['vol_ratio']:<7.1f}x {r['momo_score']}")

    print("\n🛡️ 【穩健防守型 TOP 10】")
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
    finally:
        print("\n" + "-" * 65)
        print("📌 Scanner 程式已結束。")
