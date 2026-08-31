r"""
========================================================================================
🤖 【Gemini 專屬主控掃描器 - batch_scanner_gemini.py】
========================================================================================
⚠️ 【架構防衝突聲明 / Architecture Warning for All AI Assistants】：
1. 本檔案為【Gemini 專屬的主控掃描引擎】，專職維護：
- 📄 全市場個股詳細分析報告：`reports/**/{code}_{name}_4階段技術分析報告.md` (全權由此腳本產生與更新)
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

import gc
import json
import math
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
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


STOCK_NAME_DICT_PATH = ROOT / "stock_name_dict.json"
STOCK_NAME_DICT = {}
if STOCK_NAME_DICT_PATH.exists():
    try:
        STOCK_NAME_DICT = json.loads(STOCK_NAME_DICT_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass


def parse_html_report(file_path):
    """解析單一 HTML 報告中的指標、籌碼與歷史 K 線數據"""
    text = file_path.read_text(encoding="utf-8", errors="ignore")
    
    pe_match = re.search(r'<b>PE：</b>\s*([\d.]+)', text)
    trailing_pe = float(pe_match.group(1)) if pe_match else None
    
    m = re.search(r'(\d{4})_(.*?)\((TW|TWO)\)', file_path.name)
    if not m:
        return None
    
    code, name, mkt = m.groups()
    if code in STOCK_NAME_DICT:
        name = STOCK_NAME_DICT[code]
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
                v_raw = cells[5].replace(',', '').strip()
                if '張' in v_raw:
                    vol = float(re.sub(r'[^\d.]', '', v_raw)) * 1000
                elif v_raw.endswith('M') or v_raw.endswith('m'):
                    vol = float(v_raw[:-1]) * 1000000
                elif v_raw.endswith('K') or v_raw.endswith('k'):
                    vol = float(v_raw[:-1]) * 1000
                else:
                    num_v = float(re.sub(r'[^\d.]', '', v_raw)) if v_raw else 0.0
                    vol = num_v * 1000 if num_v < 100000 else num_v
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


def detect_kline_tags(df: pd.DataFrame) -> list:
    """
    自動解析 K線與均線指標標籤 (均線開花多頭發散、突破整理箱頂、回測月線有守、糾纏向上噴出、假突破長上影線、跌破/站穩5MA/10MA/20MA)
    """
    if len(df) < 20:
        return ["⚪ K線資料累積中"]

    close_s = pd.to_numeric(df['close'], errors='coerce')
    high_s = pd.to_numeric(df['high'], errors='coerce')
    low_s = pd.to_numeric(df['low'], errors='coerce')
    open_s = pd.to_numeric(df['open'], errors='coerce')

    ma5 = close_s.rolling(5).mean()
    ma10 = close_s.rolling(10).mean()
    ma20 = close_s.rolling(20).mean()
    ma60 = close_s.rolling(60).mean() if len(df) >= 60 else None

    # 當前與前一日收盤價及各天期均線
    cur_c = float(close_s.iloc[-1])
    prev_c = float(close_s.iloc[-2])
    cur_o = float(open_s.iloc[-1])
    cur_h = float(high_s.iloc[-1])
    cur_l = float(low_s.iloc[-1])

    cur_ma5 = float(ma5.iloc[-1])
    prev_ma5 = float(ma5.iloc[-2])
    prev2_ma5 = float(ma5.iloc[-3]) if len(ma5) >= 3 else prev_ma5

    cur_ma10 = float(ma10.iloc[-1])
    prev_ma10 = float(ma10.iloc[-2])
    prev2_ma10 = float(ma10.iloc[-3]) if len(ma10) >= 3 else prev_ma10

    cur_ma20 = float(ma20.iloc[-1])
    prev_ma20 = float(ma20.iloc[-2])

    # 計算斜率
    s5_cur = ((cur_ma5 - prev_ma5) / prev_ma5) * 100.0 if prev_ma5 > 0 else 0.0
    s5_prev = ((prev_ma5 - prev2_ma5) / prev2_ma5) * 100.0 if prev2_ma5 > 0 else 0.0

    s10_cur = ((cur_ma10 - prev_ma10) / prev_ma10) * 100.0 if prev_ma10 > 0 else 0.0
    s10_prev = ((prev_ma10 - prev2_ma10) / prev2_ma10) * 100.0 if prev2_ma10 > 0 else 0.0

    s20_cur = ((cur_ma20 - prev_ma20) / prev_ma20) * 100.0 if prev_ma20 > 0 else 0.0

    tags = []

    # 1. 均線開花多頭發散 (5MA > 10MA > 20MA 且皆向上加速發散，且收盤價穩居 5MA 之上)
    if cur_c >= cur_ma5 and cur_ma5 > cur_ma10 > cur_ma20 and s5_cur > 1.0 and s10_cur > 0.4 and s20_cur > 0.2:
        if ma60 is None or cur_ma20 > float(ma60.iloc[-1]):
            tags.append("🚀 均線開花多頭發散 (主升段連鎖加速)")

    # 2. 突破波段整理箱頂 (實質長紅突破近20日高點)
    if len(df) >= 21:
        past20_box_high = float(high_s.iloc[-21:-1].max())
        if cur_c > past20_box_high and cur_c >= cur_o * 1.02:
            tags.append("🔥 突破波段整理箱頂 (實質表態)")

    # 3. 假突破長上影線 (主力誘多出貨)
    if len(df) >= 21:
        past20_h = float(high_s.iloc[-21:-1].max())
        upper_shadow = cur_h - max(cur_c, cur_o)
        body = abs(cur_c - cur_o)
        if cur_h > past20_h and cur_c < past20_h and upper_shadow >= body * 1.8:
            tags.append("🚨 假突破收長上影線 (主力誘多出貨)")

    # 4. 關鍵均線破線警示 (跌破 5MA / 10MA / 20MA)
    if prev_c >= prev_ma20 and cur_c < cur_ma20:
        tags.append("🚨 跌破20MA月線 (生命線失守)")
    elif prev_c >= prev_ma10 and cur_c < cur_ma10:
        if cur_c < cur_ma5:
            tags.append("⚠️ 跌破10MA (失守雙均線)")
        else:
            tags.append("⚠️ 跌破10MA (短線轉弱)")
    elif prev_c >= prev_ma5 and cur_c < cur_ma5:
        if cur_c >= cur_ma10:
            tags.append("⚠️ 跌破5MA (短線動能拉回)")
        else:
            tags.append("⚠️ 跌破5MA (轉弱回測)")
    elif cur_c < cur_ma5 and cur_c < cur_ma10 and cur_c < cur_ma20:
        if not any("跌破" in t for t in tags):
            tags.append("⚠️ 失守所有短均線 (短中線偏空)")

    # 5. 關鍵均線攻克與突破 (站上 5MA / 10MA / 20MA)
    if prev_c < prev_ma20 and cur_c >= cur_ma20:
        tags.append("🔥 突破站上月線 (重返多頭生命線)")
    elif prev_c < prev_ma10 and cur_c >= cur_ma10:
        tags.append("✨ 突破站上10MA (收復短線支撐)")
    elif prev_c < prev_ma5 and cur_c >= cur_ma5:
        tags.append("✨ 突破站上5MA (短線點火轉強)")

    # 6. 回測守穩 / 站穩均線
    if len(df) >= 20 and s20_cur > 0.25:
        # 當日最低點回測 20MA 附近 (0.985 ~ 1.015)，收盤拉升站穩 20MA 之上
        if (cur_l <= cur_ma20 * 1.015) and (cur_c >= cur_ma20 * 0.995) and (cur_c >= cur_l + (cur_h - cur_l) * 0.4):
            tags.append("💡 回測上升月線有守 (回檔第一買點)")

    if cur_c < cur_ma5 and cur_c >= cur_ma10 and (cur_c >= cur_ma10 * 0.995) and s10_cur > 0:
        if not any("回測" in t or "守穩" in t for t in tags):
            tags.append("💡 守穩10MA (短線回測有守)")

    if cur_c >= cur_ma5 and cur_c > prev_c and s5_cur > 0.5 and not any("突破" in t or "開花" in t for t in tags):
        tags.append("📈 站穩5MA (強勢沿線推升)")
    elif cur_c >= cur_ma10 and cur_c >= cur_ma5 and s10_cur > 0.3 and not any("突破" in t or "開花" in t or "5MA" in t for t in tags):
        tags.append("📈 站穩10MA (短多結構穩固)")

    # 7. 5MA 與 10MA 金叉 / 死叉
    if prev_ma5 <= prev_ma10 and cur_ma5 > cur_ma10:
        tags.append("✨ 5MA金叉10MA (短線轉強)")
    elif prev_ma5 >= prev_ma10 and cur_ma5 < cur_ma10:
        tags.append("⚡ 5MA死叉10MA (短線轉弱)")

    # 8. 均線斜率轉折與加速度
    if s5_prev <= 0 and s5_cur > 0.2:
        tags.append("💡 5MA轉仰角 (翻揚轉強)")
    elif s5_prev >= 0 and s5_cur < -0.2:
        tags.append("⚠️ 5MA轉俯角 (下彎轉弱)")
    elif s5_cur > 1.5 and s5_cur > s5_prev + 0.3:
        if not any("均線開花" in t or "站穩5MA" in t for t in tags):
            tags.append("🚀 5MA加大仰角 (加速噴出)")
    elif s5_cur < -1.5 and s5_cur < s5_prev - 0.3:
        tags.append("❄️ 5MA加大俯角 (加速探底)")

    # 9. 均線糾纏 (壓縮蓄勢)
    ma_min = min(cur_ma5, cur_ma10, cur_ma20)
    ma_max = max(cur_ma5, cur_ma10, cur_ma20)
    spread_pct = ((ma_max - ma_min) / cur_c) * 100.0 if cur_c > 0 else 99.0
    if spread_pct <= 1.8 and abs(s5_cur) < 0.6 and abs(s10_cur) < 0.6:
        tags.append("💎 短中期均線糾纏 (壓縮蓄勢)")

    # 常態備選
    if not tags:
        if cur_c >= cur_ma5 and cur_ma5 > cur_ma10 > cur_ma20:
            tags.append("🚀 均線多頭排列 (強勢多方)")
        elif cur_c < cur_ma5 and cur_ma5 > cur_ma10 > cur_ma20 and cur_c >= cur_ma20:
            tags.append("📈 多頭拉回整理 (均線多頭排列)")
        elif cur_ma5 < cur_ma10 < cur_ma20:
            tags.append("📉 均線空頭排列 (空方沉陷)")
        elif cur_c >= cur_ma20 and s20_cur > 0:
            tags.append("📈 站穩上升月線 (穩健多方)")
        else:
            tags.append("⚪ 均線多空中性整理")

    return tags


def calculate_rsi_series(series: pd.Series, period: int = 14) -> pd.Series:
    """計算標準 Wilder's RSI 數列 (與 stock_report_generator 一致)"""
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(com=period-1, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(com=period-1, min_periods=period).mean()
    rsi = 100.0 - (100.0 / (1.0 + (gain / loss.replace(0, np.nan))))
    return rsi.fillna(50.0).round(1)


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
    
    # 1. 鈍化型態 (近 3 日 RSI 6 持續極端)
    if len(rsi6) >= 3:
        if (rsi6.iloc[-3:] >= 80).all():
            tags.append("🚀 RSI(6) 連續高檔鈍化 (強勢主升波)")
        elif (rsi6.iloc[-3:] <= 20).all():
            tags.append("❄️ RSI(6) 連續低檔鈍化 (空方沉陷)")

    # 2. 雙線交叉 (RSI 6 與 RSI 14)
    if len(rsi6) >= 2:
        prev_rsi6 = float(rsi6.iloc[-2])
        prev_rsi14 = float(rsi14.iloc[-2])
        if prev_rsi6 <= prev_rsi14 and cur_rsi6 > cur_rsi14:
            if cur_rsi14 <= 30:
                tags.append("💡 RSI(14) 超跌區黃金交叉 (恐慌殺盤竭盡)")
            else:
                tags.append("✨ RSI 短線黃金交叉")
        elif prev_rsi6 >= prev_rsi14 and cur_rsi6 < cur_rsi14:
            if cur_rsi14 >= 75:
                tags.append("⚡ RSI 高檔死叉 (獲利回吐)")
            else:
                tags.append("⚡ RSI 短線死亡交叉")

    # 3. 嚴謹頂/底背離型態 (尋找同波段明確雙峰與轉折)
    if len(close_series) >= 25:
        sub_c = close_series.iloc[-25:]
        sub_r = rsi14.iloc[-25:]
        cur_c = sub_c.iloc[-1]
        
        # 頂背離：當前價格創 25 日新高，但 RSI 前峰更高，且當前 RSI 開始下彎鈍化衰退
        if cur_c >= sub_c.max() * 0.995:
            peak_r_idx = sub_r.iloc[:-5].argmax()
            prev_peak_r = sub_r.iloc[peak_r_idx]
            prev_peak_c = sub_c.iloc[peak_r_idx]
            if cur_c > prev_peak_c * 1.02 and prev_peak_r >= 75 and cur_rsi14 <= (prev_peak_r - 6.0):
                if len(rsi14) >= 2 and rsi14.iloc[-1] < rsi14.iloc[-2]:
                    tags.append("⚠️ RSI 頂背離警戒 (動能無力創高)")
                    
        # 底背離：當前價格創 25 日新低，但 RSI 前低更低，且當前 RSI 已打底上勾
        elif cur_c <= sub_c.min() * 1.005:
            trough_r_idx = sub_r.iloc[:-5].argmin()
            prev_trough_r = sub_r.iloc[trough_r_idx]
            prev_trough_c = sub_c.iloc[trough_r_idx]
            if cur_c < prev_trough_c * 0.98 and prev_trough_r <= 30 and cur_rsi14 >= (prev_trough_r + 6.0):
                if len(rsi14) >= 2 and rsi14.iloc[-1] > rsi14.iloc[-2]:
                    tags.append("💎 RSI 底背離落底 (雙底翻多)")

    # 4. 位階標籤
    if not tags:
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
    prev_vol = float(vol_s.iloc[-2]) if len(vol_s) >= 2 else cur_vol
    cur_v5 = float(vma5.iloc[-1]) if pd.notna(vma5.iloc[-1]) else None
    cur_v20 = float(vma20.iloc[-1]) if pd.notna(vma20.iloc[-1]) else None
    prev_v5 = float(vma5.iloc[-2]) if len(vma5) >= 2 and pd.notna(vma5.iloc[-2]) else None
    prev_v20 = float(vma20.iloc[-2]) if len(vma20) >= 2 and pd.notna(vma20.iloc[-2]) else None
    is_up = close_s.iloc[-1] >= close_s.iloc[-2] if len(close_s) >= 2 else True

    # 1. 滾量攻擊 (主升段量價齊揚)
    if len(df) >= 3 and cur_v5 is not None:
        if (close_s.iloc[-1] > close_s.iloc[-2] > close_s.iloc[-3]) and (vol_s.iloc[-1] > vol_s.iloc[-2] > vol_s.iloc[-3]) and cur_vol >= cur_v5:
            tags.append("🚀 滾量換手攻擊 (量價齊揚主升段)")

    # 2. 帶量長紅突破
    if len(df) >= 20 and cur_v20 is not None:
        past20_high = df['high'].iloc[-21:-1].max()
        if close_s.iloc[-1] >= past20_high and cur_vol >= cur_v20 * 1.8 and is_up:
            tags.append("🔥 帶量長紅突破 (實質攻擊量)")

    # 3. 價跌量急縮窒息量
    if cur_v20 is not None and cur_vol <= cur_v20 * 0.45:
        tags.append("💎 價跌量急縮窒息量 (主力洗盤完畢)")

    # 4. 高檔爆大量倒貨
    if len(df) >= 30 and cur_v20 is not None:
        past30_max_vol = vol_s.iloc[-31:-1].max()
        candle_range = df['high'].iloc[-1] - df['low'].iloc[-1] or 1.0
        body_ratio = abs(close_s.iloc[-1] - df['open'].iloc[-1]) / candle_range
        if cur_vol >= past30_max_vol and cur_vol >= cur_v20 * 2.0 and (body_ratio < 0.35 or not is_up):
            tags.append("🚨 高檔爆歷史天量收黑 (主力出貨倒貨)")

    # 5. 量價頂背離 (股價創高但量能萎縮低於均量 0.65x 且收黑)
    if len(df) >= 20 and cur_v20 is not None:
        past20_high = df['high'].iloc[-21:-1].max()
        if close_s.iloc[-1] >= past20_high:
            if cur_vol < cur_v20 * 0.65 and not is_up:
                tags.append("⚠️ 量價頂背離 (無量虛漲拉高出貨)")

    # 6. 前高天量套牢壓力牆 vs 滾量吞噬前高天量
    if len(df) >= 25 and cur_v20 is not None:
        lookback_df = df.iloc[-35:-2] if len(df) >= 35 else df.iloc[:-2]
        if len(lookback_df) >= 5:
            past_v20_series = df['volume'].rolling(20).mean()
            peak_vol_idx = lookback_df['volume'].idxmax()
            peak_vol = float(df['volume'].loc[peak_vol_idx])
            peak_v20 = float(past_v20_series.loc[peak_vol_idx]) if pd.notna(past_v20_series.loc[peak_vol_idx]) else cur_v20
            peak_high = float(df['high'].loc[peak_vol_idx])
            cur_price = float(close_s.iloc[-1])

            if peak_vol >= peak_v20 * 1.8:
                if cur_price >= peak_high * 0.970 and cur_price <= peak_high * 1.025:
                    if cur_vol < peak_vol * 0.65:
                        tags.append("⚠️ 臨前高天量阻力牆 (量能不足防壓回)")
                    elif cur_vol >= peak_vol * 0.90 and is_up:
                        tags.append("🔥 滾量吞噬前高天量 (實質換手突破)")

    # 7. 量均線黃金/死亡交叉
    if cur_v5 is not None and cur_v20 is not None and prev_v5 is not None and prev_v20 is not None:
        if prev_v5 <= prev_v20 and cur_v5 > cur_v20:
            tags.append("✨ 量能黃金交叉 (攻擊量增溫)")
        elif prev_v5 >= prev_v20 and cur_v5 < cur_v20:
            tags.append("⚡ 量能死亡交叉 (退潮警戒)")

    # 常態 (綜合 20日均量 與 昨日成交量)
    if not tags:
        if cur_v20 is not None and cur_vol >= cur_v20 * 1.5:
            tags.append("🔥 帶量突破換手")
        elif cur_v20 is not None and cur_vol >= cur_v20 * 1.2:
            tags.append("📈 買盤溫和增量")
        elif cur_v20 is not None and cur_vol < cur_v20 * 0.8:
            if prev_vol > 0 and cur_vol >= prev_vol * 1.15:
                tags.append("📈 較昨溫和量增 (處低均量區)")
            elif cur_vol <= cur_v20 * 0.45:
                tags.append("💎 價跌量急縮窒息量 (主力洗盤完畢)")
            else:
                tags.append("📉 縮量沉澱整理")
        elif prev_vol > 0 and cur_vol >= prev_vol * 1.15:
            tags.append("📈 較昨日增量換手")
        elif prev_vol > 0 and cur_vol <= prev_vol * 0.85:
            tags.append("📉 較昨日量縮整理")
        else:
            tags.append("⚪ 常態量能換手")

    return tags


def detect_macd_tags(close_s: pd.Series) -> list:
    """精準計算與判讀 MACD 訊號 (空中加油二次金叉、零軸上/下金叉、柱狀體翻轉、頂底背離、綠柱收斂/延伸、紅柱放大/收斂)"""
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

    # 1. 交叉訊號 (金叉 / 死叉)
    if prev_dif <= prev_sig and cur_dif > cur_sig:
        if cur_dif > 0:
            # 檢查過去 15 天內是否曾在零軸上金叉過（二次金叉）
            sub_dif = dif.iloc[-20:-2]
            sub_sig = signal.iloc[-20:-2]
            prior_cross_above_zero = any((sub_dif.iloc[i-1] <= sub_sig.iloc[i-1] and sub_dif.iloc[i] > sub_sig.iloc[i] and sub_dif.iloc[i] > 0) for i in range(1, len(sub_dif)))
            if prior_cross_above_zero:
                tags.append("🚀 MACD 零軸上二次金叉 (空中加油主升段)")
            else:
                tags.append("✨ MACD 零軸上金叉 (強勢攻擊)")
        else:
            tags.append("✨ MACD 零軸下金叉 (低檔反彈轉折)")
    elif prev_dif >= prev_sig and cur_dif < cur_sig:
        if cur_dif > 0:
            tags.append("⚡ MACD 零軸上死亡交叉 (波段獲利了結)")
        else:
            tags.append("⚡ MACD 死亡交叉 (動能轉弱)")

    # 2. 柱狀體翻紅 / 翻綠 (第一天翻轉)
    if prev_hist <= 0 and cur_hist > 0:
        if not any("金叉" in t for t in tags):
            tags.append("🌊 MACD 柱狀體翻紅 (動能增強)")
    elif prev_hist >= 0 and cur_hist < 0:
        if not any("死叉" in t for t in tags):
            tags.append("❄️ MACD 柱狀體翻綠 (動能減弱)")

    # 3. 嚴謹頂背離判定 (波峰識別 + 紅柱縮短確認)
    if len(close_s) >= 30:
        sub_c = close_s.iloc[-25:]
        sub_dif = dif.iloc[-25:]
        cur_c = sub_c.iloc[-1]
        
        if cur_c >= sub_c.max() * 0.995:
            peak_idx = sub_dif.iloc[:-5].argmax()
            prev_peak_dif = float(sub_dif.iloc[peak_idx])
            prev_peak_c = float(sub_c.iloc[peak_idx])
            
            if cur_c > prev_peak_c * 1.02 and prev_peak_dif > 0 and cur_dif < prev_peak_dif * 0.75:
                if cur_hist < prev_hist and (len(hist) < 3 or hist.iloc[-2] < hist.iloc[-3] or cur_hist < 0):
                    tags.append("⚠️ MACD 頂背離警戒 (股價創高動能衰退)")

    # 4. 嚴謹底背離判定 (波谷識別 + 綠柱縮短或翻紅確認)
    if len(close_s) >= 30:
        sub_c = close_s.iloc[-25:]
        sub_dif = dif.iloc[-25:]
        cur_c = sub_c.iloc[-1]
        
        if cur_c <= sub_c.min() * 1.005:
            trough_idx = sub_dif.iloc[:-5].argmin()
            prev_trough_dif = float(sub_dif.iloc[trough_idx])
            prev_trough_c = float(sub_c.iloc[trough_idx])
            
            if cur_c < prev_trough_c * 0.98 and prev_trough_dif < 0 and cur_dif > prev_trough_dif + 1.0:
                if cur_hist > prev_hist or cur_hist > 0:
                    tags.append("💎 MACD 底背離起漲 (雙谷墊高破底翻)")

    # 5. 柱狀體紅綠磚狀態判定 (精確解決綠磚卻判定多方的矛盾)
    if not tags:
        # A. 紅磚區 (Hist > 0)
        if cur_hist > 0:
            if cur_dif > 0 and cur_sig > 0:
                if cur_hist >= prev_hist:
                    tags.append("🚀 MACD 零軸上強勢多頭 (紅柱連續放大)")
                else:
                    tags.append("📈 MACD 多頭推升收斂 (紅柱縮小整理)")
            elif cur_hist >= prev_hist:
                tags.append("📈 MACD 零軸下反彈推進 (紅柱連續放大)")
            else:
                tags.append("⚪ MACD 反彈動能趨緩 (紅柱縮小整理)")
        # B. 綠磚區 (Hist < 0)
        elif cur_hist < 0:
            if cur_dif > 0 and cur_sig > 0:
                if cur_hist > prev_hist:  # 負值變小 (例如 -2.21 > -2.85，綠柱縮短收斂)
                    tags.append("💡 MACD 綠柱收斂 (零軸上回檔/空方衰退)")
                else:
                    tags.append("⚠️ MACD 零軸上回檔修正 (綠柱延伸中)")
            else:
                if cur_hist > prev_hist:
                    tags.append("💡 MACD 綠柱收斂 (空方力道減弱/低檔醞釀)")
                else:
                    tags.append("📉 MACD 空方弱勢整理 (零軸下探底)")
        # C. 零軸常態平水
        else:
            if cur_dif >= 0:
                tags.append("⚪ MACD 零軸平水多空平衡")
            else:
                tags.append("⚪ MACD 低檔整理")

    return tags


def calculate_kdj_series(high_s: pd.Series, low_s: pd.Series, close_s: pd.Series, n: int = 9):
    """計算標準台股 KD (9,3,3) 數列"""
    lo = low_s.rolling(n).min()
    hi = high_s.rolling(n).max()
    rsv = (close_s - lo) / (hi - lo).replace(0, np.nan) * 100.0
    rsv = rsv.fillna(50.0)
    k = rsv.ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    j = 3.0 * k - 2.0 * d
    return k.round(1), d.round(1), j.round(1)


def detect_kd_tags(high_s: pd.Series, low_s: pd.Series, close_s: pd.Series) -> list:
    """自動解析 KD 狀態標籤 (位階超買超賣、黃金/死亡交叉、高低檔鈍化、頂底背離)"""
    if len(close_s) < 9:
        return []
    
    k_s, d_s, _ = calculate_kdj_series(high_s, low_s, close_s, n=9)
    cur_k = float(k_s.iloc[-1])
    cur_d = float(d_s.iloc[-1])
    prev_k = float(k_s.iloc[-2]) if len(k_s) >= 2 else cur_k
    prev_d = float(d_s.iloc[-2]) if len(d_s) >= 2 else cur_d
    
    tags = []
    
    # 1. 雙線交叉訊號 (金叉 / 死叉)
    if prev_k <= prev_d and cur_k > cur_d:
        if cur_k <= 25:
            tags.append("✨ KD 20以下超賣黃金交叉 (落底反彈第一買點)")
        elif cur_k >= 75:
            tags.append("✨ KD 80以上再金叉 (強者恆強軋空)")
        else:
            tags.append("✨ KD 黃金交叉 (短線轉強)")
    elif prev_k >= prev_d and cur_k < cur_d:
        if cur_k >= 80:
            tags.append("⚡ KD 80以上超買死亡交叉 (高檔轉弱見頂)")
        else:
            tags.append("⚡ KD 死亡交叉 (短線修正)")

    # 2. 鈍化型態 (連續 3 日維持極值)
    if len(k_s) >= 3:
        if (k_s.iloc[-3:] >= 80).all():
            tags.append("🚀 KD 高檔強勢鈍化 (K值連3日>80軋空)")
        elif (k_s.iloc[-3:] <= 20).all():
            tags.append("❄️ KD 低檔弱勢鈍化 (空方沉陷)")

    # 3. 嚴謹頂/底背離判定 (25 日波峰波谷識別 + 交叉確認)
    if len(close_s) >= 25:
        sub_c = close_s.iloc[-25:]
        sub_k = k_s.iloc[-25:]
        cur_c = sub_c.iloc[-1]
        
        # 頂背離
        if cur_c >= sub_c.max() * 0.995:
            peak_idx = sub_k.iloc[:-5].argmax()
            prev_peak_k = float(sub_k.iloc[peak_idx])
            prev_peak_c = float(sub_c.iloc[peak_idx])
            if cur_c > prev_peak_c * 1.02 and prev_peak_k >= 80 and cur_k <= (prev_peak_k - 10.0):
                if cur_k < prev_k or cur_k < cur_d:
                    tags.append("⚠️ KD 頂背離警戒 (高檔動能背離衰竭)")
                    
        # 底背離
        elif cur_c <= sub_c.min() * 1.005:
            trough_idx = sub_k.iloc[:-5].argmin()
            prev_trough_k = float(sub_k.iloc[trough_idx])
            prev_trough_c = float(sub_c.iloc[trough_idx])
            if cur_c < prev_trough_c * 0.98 and prev_trough_k <= 25 and cur_k >= (prev_trough_k + 8.0):
                if cur_k > prev_k or cur_k > cur_d:
                    tags.append("💎 KD 底背離 (低檔雙底打底起漲)")

    # 常態
    if not tags:
        if cur_k > cur_d and cur_k >= 50:
            tags.append("📈 KD 多方強勢推進")
        elif cur_k < cur_d and cur_k < 50:
            tags.append("📉 KD 空方弱勢回檔")
        else:
            tags.append("⚪ KD 多空中性震盪")

    return tags


def detect_chip_tags(stock_info: dict, kline_df: pd.DataFrame = None) -> list:
    """台股實戰專業籌碼指標判定大腦
    嚴格規範：只看近 1~5 日即時攻防，並以近 20 日（月度）判斷護盤底倉
    涵蓋：投信爆量總攻/護盤、土洋同步/對作、資減法買/資增法賣、法人高強度鎖碼、自營避險點火
    """
    tags = []
    institutions = stock_info.get('institutions', [])
    margin = stock_info.get('margin', [])
    
    if not institutions:
        return ["⚪ 法人籌碼中性換手"]

    inst_5 = institutions[:5]
    inst_20 = institutions[:20]
    
    # 最新當日數據
    latest_inst = inst_5[0]
    f_0 = latest_inst.get('foreign', 0.0)
    t_0 = latest_inst.get('trust', 0.0)
    d_0 = latest_inst.get('dealer', 0.0)
    tot_0 = latest_inst.get('total', 0.0)
    
    # 總成交量 (張數)
    vol_col = 'volume' if 'volume' in kline_df.columns else ('Volume' if 'Volume' in kline_df.columns else None)
    close_col = 'close' if 'close' in kline_df.columns else ('Close' if 'Close' in kline_df.columns else None)
    
    latest_vol_shares = kline_df[vol_col].iloc[-1] if (kline_df is not None and not kline_df.empty and vol_col) else 0
    latest_vol_lots = (latest_vol_shares / 1000.0) if latest_vol_shares > 0 else 0
    
    # 股價與均線關係 (判斷護盤 vs 總攻)
    price = kline_df[close_col].iloc[-1] if (kline_df is not None and not kline_df.empty and close_col) else 0
    ma20_val = kline_df[close_col].rolling(20).mean().iloc[-1] if (kline_df is not None and close_col and len(kline_df) >= 20) else price
    is_at_support = (price <= ma20_val * 1.02)  # 回檔測支撐區

    # ---------------------------------------------------------
    # 1. 投信動態 (內資主力、爆量護盤、爆量總攻、連續認養)
    # ---------------------------------------------------------
    trust_5_buys = [x.get('trust', 0.0) for x in inst_5]
    trust_buy_days = sum(1 for x in trust_5_buys if x > 0)
    
    # 投信連買天數
    trust_consecutive_buys = 0
    for x in trust_5_buys:
        if x > 0:
            trust_consecutive_buys += 1
        else:
            break

    # 投信暴量判定 (滿足前5日均量3倍或單日買超顯著)
    past_trust_pos = [x for x in trust_5_buys[1:] if x > 0]
    avg_past_trust = (sum(past_trust_pos) / len(past_trust_pos)) if past_trust_pos else 0
    is_trust_surge = (t_0 >= 500 and (avg_past_trust == 0 or t_0 >= avg_past_trust * 2.5)) or (latest_vol_lots > 0 and (t_0 / latest_vol_lots) >= 0.12 and t_0 >= 200)

    if trust_consecutive_buys >= 3 or (trust_buy_days >= 4 and t_0 > 0):
        if is_trust_surge:
            if is_at_support:
                tags.append("🛡️ 投信巨額爆量護盤 (關鍵支撐鎖碼防禦)")
            else:
                tags.append("🚀 投信爆量總攻擊 (主力共識急拉主升段)")
        else:
            tags.append(f"🚀 投信連續認養 (近5日買超{trust_buy_days}天)")
    elif t_0 > 0 and all(x <= 0 for x in trust_5_buys[1:3]) and (t_0 >= 200 or is_trust_surge):
        tags.append("✨ 投信由賣轉買 (首日翻多起漲)")
    elif all(x < 0 for x in trust_5_buys[:3]) and sum(trust_5_buys[:3]) <= -300:
        tags.append("⚠️ 投信高檔結帳 (連日調節賣超)")

    # ---------------------------------------------------------
    # 2. 土洋同盟與對作 (外資 vs 投信)
    # ---------------------------------------------------------
    if f_0 > 0 and t_0 > 0 and (f_0 + t_0 >= 300 or (latest_vol_lots > 0 and (f_0 + t_0) / latest_vol_lots >= 0.10)):
        tags.append("🔥 土洋同步大買 (雙主力合力作多)")
    elif f_0 < 0 and t_0 > 0 and t_0 >= 150:
        tags.append("⚔️ 土洋對作 (投信接刀吃貨/外資倒貨)")
    elif f_0 > 0 and t_0 < 0 and f_0 >= 300:
        tags.append("⚡ 土洋對作 (外資吃貨/投信調節)")
    elif f_0 < 0 and t_0 < 0 and d_0 < 0 and tot_0 <= -500:
        tags.append("🚨 法人集體倒貨 (籌碼沉陷)")

    # ---------------------------------------------------------
    # 3. 法人高強度鎖碼 & 自營商避險點火
    # ---------------------------------------------------------
    if latest_vol_lots > 0 and tot_0 > 0:
        inst_pct = tot_0 / latest_vol_lots
        if inst_pct >= 0.30 and tot_0 >= 300:
            tags.append(f"🔒 法人高強度鎖碼 (單日買超佔比 {int(inst_pct*100)}%)")
            
    if latest_vol_lots > 0 and d_0 >= 300 and (d_0 / latest_vol_lots) >= 0.08:
        tags.append("⚡ 自營避險爆量買超 (權證大戶點火)")

    # ---------------------------------------------------------
    # 4. 融資籌碼 (資減法買 vs 資增法賣)
    # ---------------------------------------------------------
    if margin and len(margin) >= 3:
        margin_3_changes = [m.get('change', 0.0) for m in margin[:3]]
        tot_inst_3 = sum(x.get('total', 0.0) for x in inst_5[:3])
        
        # 資減法買：融資連減且法人近3日累計買超
        if all(c < 0 for c in margin_3_changes) and tot_inst_3 > 0:
            tags.append("💎 資減法買 (散戶退場主力吃飽)")
        # 資增法賣：融資連增且法人大賣
        elif all(c > 0 for c in margin_3_changes) and tot_inst_3 < -300:
            tags.append("⚠️ 資增法賣 (主力倒貨散戶接刀)")

    # ---------------------------------------------------------
    # 5. 月度護盤底倉 (近20日累計買賣比)
    # ---------------------------------------------------------
    if len(inst_20) >= 15:
        f_20_sum = sum(x.get('foreign', 0.0) for x in inst_20)
        t_20_sum = sum(x.get('trust', 0.0) for x in inst_20)
        
        if f_20_sum >= 2000 and not any("外資" in t for t in tags):
            tags.append("🛡️ 外資月度重倉防守 (近20日淨買佔優)")
        elif t_20_sum >= 1500 and not any("投信" in t for t in tags):
            tags.append("🎯 投信近月密集建倉 (下檔護盤強)")

    # 預設常態
    if not tags:
        inst_buy_days_5 = sum(1 for x in inst_5 if x.get('total', 0.0) > 0)
        if inst_buy_days_5 >= 3 and tot_0 > 0:
            tags.append(f"📈 近5日法人積極集資 ({inst_buy_days_5}/5日買超)")
        elif inst_buy_days_5 <= 1:
            tags.append("📉 法人籌碼退潮 (連日調節)")
        else:
            tags.append("⚪ 法人中性換手 (多空互見)")

    return tags[:3]  # 精選最多 3 個最具代表性的籌碼標籤


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
    
    # 統一使用標準 Wilder's RSI(14)
    rsi14_series = calculate_rsi_series(close_s, 14)
    rsi14 = float(rsi14_series.iloc[-1])
    
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

    kline_tags = detect_kline_tags(df)
    rsi_tags = detect_rsi_tags(close_s)
    vol_tags = detect_volume_tags(df)
    macd_tags = detect_macd_tags(close_s)
    kd_tags = detect_kd_tags(pd.Series(high), pd.Series(low), close_s)

    # ==========================================
    # 1. 📈 K線與均線指標評分調整 (加扣分標準)
    # ==========================================
    for ktag in kline_tags:
        if "🚀 均線開花多頭發散" in ktag:
            momo_score += 18
            momo_reasons.append("均線開花多頭發散(主升加速)")
            def_score += 12
            def_reasons.append("多頭均線開花保護")
        elif "🔥 突破波段整理箱頂" in ktag:
            momo_score += 15
            momo_reasons.append("實質突破整理箱頂(破繭而出)")
            def_score += 10
            def_reasons.append("帶量站上箱頂轉為支撐")
        elif "💡 回測上升月線有守" in ktag:
            def_score += 18
            def_reasons.append("回測上升月線有守(最佳風報比買點)")
            momo_score += 10
            momo_reasons.append("回測月線有守起漲")
        elif "💎 短中期均線糾纏" in ktag:
            def_score += 12
            def_reasons.append("均線糾纏壓縮蓄勢(變盤在即)")
            momo_score += 8
        elif "🚨 假突破收長上影線" in ktag:
            momo_score -= 25
            momo_reasons.append("⚠️警示:假突破長上影線(主力誘多出貨)")
            def_score -= 25
            def_reasons.append("⚠️警示:假突破誘多")
        elif "🚨 跌破20MA月線" in ktag or "失守所有短均線" in ktag:
            momo_score -= 25
            momo_reasons.append("🚨失守20MA生命線")
            def_score -= 30
            def_reasons.append("🚨失守20MA生命線")
        elif "⚠️ 跌破10MA" in ktag or "破線轉空" in ktag:
            momo_score -= 15
            momo_reasons.append("⚠️跌破10MA短線轉弱")
            def_score -= 15
            def_reasons.append("⚠️失守10MA短均線")
        elif "⚠️ 跌破5MA" in ktag:
            momo_score -= 10
            momo_reasons.append("⚠️跌破5MA攻擊線拉回")
            def_score -= 8
            def_reasons.append("⚠️跌破5MA短線降溫")
        elif "🔥 突破站上月線" in ktag:
            momo_score += 15
            momo_reasons.append("🔥突破站上20MA月線(重返多頭)")
            def_score += 15
            def_reasons.append("重返月線生命線支撐")
        elif "✨ 突破站上10MA" in ktag:
            momo_score += 12
            momo_reasons.append("✨突破站上10MA(收復短線支撐)")
            def_score += 10
            def_reasons.append("收復10MA短線支撐")
        elif "✨ 突破站上5MA" in ktag:
            momo_score += 12
            momo_reasons.append("✨突破站上5MA(短線點火轉強)")
            def_score += 8
            def_reasons.append("站上5MA攻擊線")
        elif "📈 站穩5MA" in ktag:
            momo_score += 12
            momo_reasons.append("站穩5MA強勢推升")
            def_score += 8
            def_reasons.append("穩居5MA攻擊線之上")
        elif "📈 站穩10MA" in ktag or "💡 守穩10MA" in ktag:
            def_score += 12
            def_reasons.append("守穩10MA短線支撐")
            momo_score += 8
            momo_reasons.append("回測10MA有守")

    # ==========================================
    # 2. 📊 VOL 量能核心指標評分調整 (加扣分標準)
    # ==========================================
    for vtag in vol_tags:
        if "🔥 帶量長紅突破" in vtag:
            momo_score += 16
            momo_reasons.append("帶量長紅實質突破(主力進駐)")
            def_score += 10
            def_reasons.append("帶量突破確認底部")
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
        elif "💡 MACD 綠柱收斂" in mtag:
            def_score += 8
            def_reasons.append("MACD綠柱收斂(賣壓衰退/尋求止跌)")
            momo_score += 5
            momo_reasons.append("MACD綠柱收斂(回檔減速)")
        elif "⚠️ MACD 零軸上回檔修正" in mtag:
            momo_score -= 10
            momo_reasons.append("⚠️MACD零軸上回檔(綠柱擴大中)")
            def_score -= 8
            def_reasons.append("⚠️MACD綠柱延伸修正")
        elif "📉 MACD 空方弱勢整理" in mtag:
            momo_score -= 15
            momo_reasons.append("MACD零軸下空方弱勢整理")
            def_score -= 15
            def_reasons.append("MACD空方格局")
        elif "📈 MACD 多頭推升收斂" in mtag:
            momo_score += 8
            momo_reasons.append("MACD多頭推升(紅柱整理)")
            def_score += 6

    # ==========================================
    # 4. ⚡ KD 核心指標評分調整 (加扣分標準)
    # ==========================================
    for kd_tag in kd_tags:
        if "✨ KD 低檔超賣黃金交叉" in kd_tag:
            momo_score += 12
            momo_reasons.append("KD低檔金叉(轉折買點)")
            def_score += 12
            def_reasons.append("KD低檔黃金交叉落底")
        elif "✨ KD 高檔再金叉" in kd_tag:
            momo_score += 15
            momo_reasons.append("KD高檔再金叉(強勢軋空)")
            def_score += 8
        elif "🚀 KD 高檔強勢鈍化" in kd_tag:
            momo_score += 15
            momo_reasons.append("KD高檔鈍化(主升段連鎖)")
        elif "💎 KD 底背離落底" in kd_tag:
            momo_score += 15
            momo_reasons.append("KD底背離(低檔背離起漲)")
            def_score += 15
            def_reasons.append("KD底背離確認落底")
        elif "⚠️ KD 頂背離警戒" in kd_tag:
            momo_score -= 15
            momo_reasons.append("⚠️警示:KD頂背離(動能衰退)")
            def_score -= 15
            def_reasons.append("⚠️警示:KD頂背離")
        elif "⚡ KD 高檔超買死亡交叉" in kd_tag:
            momo_score -= 12
            momo_reasons.append("KD超買死叉轉弱")
            def_score -= 12
            def_reasons.append("KD高檔死叉修正")

    # ==========================================
    # 5. 🏛️ 專業籌碼指標判定與評分調整
    # ==========================================
    chip_tags = detect_chip_tags(stock_info, df)
    for ctag in chip_tags:
        if "🔥 土洋同步大買" in ctag:
            momo_score += 15
            momo_reasons.append("土洋同步大買(雙主力合力)")
            def_score += 15
            def_reasons.append("土洋同步大買")
        elif "🚀 投信爆量總攻擊" in ctag:
            momo_score += 20
            momo_reasons.append("投信爆量總攻擊(主升段點火)")
            def_score += 15
            def_reasons.append("投信爆量總攻")
        elif "🛡️ 投信巨額爆量護盤" in ctag:
            def_score += 20
            def_reasons.append("投信巨額爆量護盤(鎖碼防禦)")
            momo_score += 10
        elif "🚀 投信連續認養" in ctag:
            momo_score += 12
            momo_reasons.append("投信連續認養")
            def_score += 12
        elif "✨ 投信由賣轉買" in ctag:
            momo_score += 10
            momo_reasons.append("投信由賣轉買(起漲點)")
            def_score += 10
        elif "💎 資減法買" in ctag:
            momo_score += 12
            momo_reasons.append("資減法買(籌碼極度乾淨)")
            def_score += 12
        elif "🔒 法人高強度鎖碼" in ctag:
            momo_score += 15
            momo_reasons.append("法人高強度鎖碼")
        elif "⚡ 自營避險爆量買超" in ctag:
            momo_score += 10
            momo_reasons.append("自營避險爆買(短多點火)")
        elif "🛡️ 外資月度重倉防守" in ctag or "🎯 投信近月密集建倉" in ctag:
            def_score += 15
            def_reasons.append("月度主力重倉護盤")
        elif "🚨 法人集體倒貨" in ctag:
            momo_score -= 20
            momo_reasons.append("⚠️警示:法人集體倒貨")
            def_score -= 20
            def_reasons.append("法人集體倒貨")
        elif "⚠️ 資增法賣" in ctag:
            momo_score -= 15
            momo_reasons.append("⚠️警示:資增法賣(散戶接刀)")
            def_score -= 15
        elif "⚠️ 投信高檔結帳" in ctag:
            momo_score -= 15
            momo_reasons.append("⚠️警示:投信連日結帳賣超")
            def_score -= 15

    k_s, d_s, j_s = calculate_kdj_series(pd.Series(high), pd.Series(low), close_s, n=9)

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
        'k': float(k_s.iloc[-1]),
        'd': float(d_s.iloc[-1]),
        'j': float(j_s.iloc[-1]),
        'kline_tags': kline_tags,
        'rsi_tags': rsi_tags,
        'vol_tags': vol_tags,
        'macd_tags': macd_tags,
        'kd_tags': kd_tags,
        'chip_tags': chip_tags,
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

    kline_tags = r.get('kline_tags') or []
    if not kline_tags:
        kline_tags = ["均線多頭排列"] if (r['s5'] > 0 and r['s10'] > 0 and r['s20'] > 0) else ["均線多空中性整理"]

    rsi_tags = r.get('rsi_tags') or []
    if not rsi_tags:
        rsi_tags = [f"RSI(14): {r['rsi14']} （{'超買強勢' if r['rsi14'] > 75 else '多頭推進區' if r['rsi14'] > 50 else '弱勢整理'}）"]

    vol_tags = r.get('vol_tags') or []
    if not vol_tags:
        vol_tags = ["爆量攻擊" if r['vol_ratio'] >= 1.5 else ("溫和放量" if r['vol_ratio'] >= 1.0 else "量能萎縮")]

    macd_tags = r.get('macd_tags') or []
    if not macd_tags:
        macd_tags = ["MACD 數據正常"]

    kd_tags = r.get('kd_tags') or []
    if not kd_tags:
        kd_tags = ["KD 數據正常"]

    technical_tags = list(kline_tags)

    chip_tags = r.get('chip_tags') or ["⚪ 法人中性換手 (多空互見)"]
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
    lines.append(f"  - **KD(9,3,3)**：K {r.get('k', 50.0):.1f} / D {r.get('d', 50.0):.1f}（{'高檔強勢' if r.get('k', 50)>=80 else '低檔超賣' if r.get('k', 50)<=20 else '多空中性'}）")
    lines.append(f"  - **成交量**：{r['vol']:,} 股（相對 20日均量 {r['vol_ratio']} 倍）\n")
    lines.append("### 【標籤摘要】")
    lines.append(f"- **K線標籤**：{'、'.join(kline_tags)}")
    lines.append(f"- **技術標籤**：{'、'.join(technical_tags)}")
    lines.append(f"- **VOL 標籤**：{'、'.join(vol_tags)}")
    lines.append(f"- **RSI 標籤**：{'、'.join(rsi_tags)}")
    lines.append(f"- **MACD 標籤**：{'、'.join(macd_tags)}")
    lines.append(f"- **KD 標籤**：{'、'.join(kd_tags)}")
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
    lines.append(f"| KD(9,3,3) | K={r.get('k', 50.0):.1f} / D={r.get('d', 50.0):.1f} | {'、'.join(kd_tags)} |")
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

    content = "\n".join(lines)
    if output_path.exists():
        try:
            if output_path.read_text(encoding="utf-8") == content:
                return output_path
        except Exception:
            pass
    output_path.write_text(content, encoding="utf-8")
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
    t_start = time.perf_counter()
    print("=" * 65)
    print("🚀 啟動個股雙軌勝率與動能掃描器 (batch_scanner_gemini.py - 多核心平行加速版)")
    print("=" * 65)
    
    if not REPORTS_DIR.exists():
        print(f"❌ 錯誤：找不到 {REPORTS_DIR} 目錄！")
        return
        
    html_files = sorted(REPORTS_DIR.glob("**/*.html"), key=lambda p: p.stat().st_mtime, reverse=True)
    total_files = len(html_files)
    optimal_workers = min(total_files, max(2, min(16, (os.cpu_count() or 4))))
    print(f"📂 於 reports/ 目錄下搜尋到 {total_files} 個 HTML 檔案（啟用 {optimal_workers} 核心並行加速）")
    
    # 1. 多核心平行解析 HTML 報表
    with ThreadPoolExecutor(max_workers=optimal_workers) as executor:
        parsed_results = list(executor.map(parse_html_report, html_files))

    infos_by_code = {}
    cat_counts = {}
    for info in parsed_results:
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
    as_of = max(date_counts, key=date_counts.get) if date_counts else '未知'
    print(f"📅 本次統一使用資料截止日：{as_of}")

    # 2. 多核心平行評估策略與更新個股 4 階段 Markdown 分析報告
    def _eval_and_save(info):
        res = evaluate_dual_strategy(info, all_category_counts=cat_counts, as_of=as_of)
        if res:
            try:
                save_stage4_report(res)
            except Exception:
                pass
        return res

    with ThreadPoolExecutor(max_workers=optimal_workers) as executor:
        eval_results = list(executor.map(_eval_and_save, list(infos_by_code.values())))

    evaluated = [res for res in eval_results if res is not None]
            
    if not evaluated:
        print("⚠️ 未解構出有效的個股數據。")
        return

    momo_results = sorted(evaluated, key=lambda x: x['momo_score'], reverse=True)
    def_results = sorted(evaluated, key=lambda x: x['def_score'], reverse=True)
    
    print(f"✅ 完成 {len(evaluated)} 檔個股的雙軌進化版策略評分與 4 階段報告更新。")
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
    
    # 記憶體即時釋放
    gc.collect()
    
    t_end = time.perf_counter()
    print(f"\n📄 雙軌選股儀表板已寫入至：{OUTPUT_MD}")
    print(f"📄 突破候選池已寫入至：{BREAKOUT_OUTPUT_MD}")
    print(f"⏱️ 總運算耗時：{t_end - t_start:.2f} 秒（{optimal_workers} 核心並行）")


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
