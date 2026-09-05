#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
========================================================================================
🏆 AI 獨有實戰勝率與自我進化引擎 (evolution_engine.py) - 旗艦多維平衡版
----------------------------------------------------------------------------------------
【實事求是與科學量化核心】：
1. 拒絕粗暴大屠殺：一票否決僅保留「處置撮合陷阱」與「月乖離 > 22% 瘋狂過熱」。
2. 漸進式綜合評分矩陣：
   - 攻守兼備：同時納入「放量突破起漲」與「量縮良性回測月線守穩」。
   - 開高走低長黑採平滑扣分制，絕不因單日震盪盲目錯殺優質回測買點。
3. 風報比（R/R）：個股前醒目標註，不作死門檻硬剔除，由操盤手決策。
4. 驅動核心：直連 Google REST API (Gemini 3.5 Flash / 備援架構)，秒級情報審查。
========================================================================================
"""

import sys
import os
import re
import json
import time
from pathlib import Path
import requests
import pandas as pd

# Windows 終端機 UTF-8 輸出修復
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

ROOT_DIR = Path(__file__).resolve().parent
REPORTS_DIR = ROOT_DIR / "reports"
OUTPUT_EVO_MD = ROOT_DIR / "stock_winrate_ranking_evolution.md"
EVOLUTION_LOG_MD = ROOT_DIR / "evolution_log.md"

sys.path.insert(0, str(ROOT_DIR))
from batch_scanner_gemini import (
    parse_html_report,
    calculate_rsi_series,
    calculate_kdj_series,
    detect_kline_tags,
    detect_volume_tags,
    detect_macd_tags,
    recognize_pattern
)

def get_gemini_api_key():
    api_key = os.environ.get('GEMINI_API_KEY')
    if api_key:
        return api_key

    # 1. 優先從 指標數據/ai_keys_local.py 載入
    ai_keys_file = ROOT_DIR / "指標數據" / "ai_keys_local.py"
    if ai_keys_file.exists():
        try:
            for line in ai_keys_file.read_text(encoding='utf-8', errors='ignore').splitlines():
                line = line.strip()
                if line.startswith('GEMINI_API_KEY') and '=' in line:
                    val = line.split('=', 1)[1].strip().strip('"\'')
                    if val:
                        return val
        except Exception:
            pass

    # 2. 從根目錄 .env 載入
    env_file = ROOT_DIR / '.env'
    if env_file.exists():
        for line in env_file.read_text(encoding='utf-8', errors='ignore').splitlines():
            line = line.strip()
            if line.startswith('GEMINI_API_KEY='):
                val = line.split('=', 1)[1].strip().strip('"\'')
                if val:
                    return val
    return None

def call_gemini_rest(prompt, api_key, models_priority=['gemini-2.5-flash', 'gemini-flash-latest', 'gemini-flash-lite-latest']):
    for m in models_priority:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2}
        }
        try:
            res = requests.post(url, json=payload, timeout=20)
            if res.status_code == 200:
                data = res.json()
                text = data['candidates'][0]['content']['parts'][0]['text']
                return text, m
            elif res.status_code in (503, 429):
                continue
        except Exception:
            continue
    return None, None

def calculate_evolution_score(stock_info):
    kline = stock_info.get('kline', [])
    if len(kline) < 15:
        return None

    df = pd.DataFrame(kline)
    close_s = df['close']
    high_s = df['high']
    low_s = df['low']
    open_s = df['open']
    vol_s = df['volume']
    n = len(df)

    price = float(close_s.iloc[-1])
    prev_close = float(close_s.iloc[-2])
    today_pct = (price - prev_close) / prev_close * 100
    open_p = float(open_s.iloc[-1])
    intraday_pct = (price - open_p) / prev_close * 100

    ma5 = close_s.rolling(5).mean()
    ma10 = close_s.rolling(10).mean()
    ma20 = close_s.rolling(20).mean()

    s5 = ((ma5.iloc[-1] - ma5.iloc[-2]) / ma5.iloc[-2] * 100) if len(ma5) >= 2 else 0.0
    s20 = ((ma20.iloc[-1] - ma20.iloc[-2]) / ma20.iloc[-2] * 100) if len(ma20) >= 20 else 0.0

    bias_20 = ((price - ma20.iloc[-1]) / ma20.iloc[-1] * 100) if len(ma20) >= 20 and ma20.iloc[-1] > 0 else 0.0
    bias_5 = ((price - ma5.iloc[-1]) / ma5.iloc[-1] * 100) if len(ma5) >= 5 and ma5.iloc[-1] > 0 else 0.0

    vol20 = float(vol_s.rolling(20).mean().iloc[-1]) if n >= 20 else float(vol_s.iloc[-1])
    vol_ratio = float(vol_s.iloc[-1]) / max(vol20, 1.0)

    rsi14_series = calculate_rsi_series(close_s, 14)
    rsi14 = float(rsi14_series.iloc[-1])

    is_disposal = ('處置' in stock_info.get('name', '')) or ('處置' in stock_info.get('path', ''))

    df_upper = df.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'})
    pattern_name, _ = recognize_pattern(df_upper)
    ktags = detect_kline_tags(df)
    vtags = detect_volume_tags(df, is_disposal)
    mtags = detect_macd_tags(close_s)

    inst = stock_info.get('institutions', [])[:5]
    inst_buy_days = sum(x['total'] > 0 for x in inst) if inst else 0
    trust_buy_days = sum(x['trust'] > 0 for x in inst)

    bar_range = max(high_s.iloc[-1] - low_s.iloc[-1], 1e-9)
    close_loc = (price - low_s.iloc[-1]) / bar_range

    # 🛑 【真正致命的客觀一票否決】
    if is_disposal:
        return None
    if bias_20 > 22.0:
        return None
    if bias_20 < -18.0:
        return None

    # ── 多維漸進式評分矩陣 (基準分 50.0) ──
    score = 50.0
    reasons = []

    # A. 均線與K線位置 (攻守兼備)
    if any("均線開花" in t for t in ktags):
        score += 22.0
        reasons.append("🚀均線開花多頭發散")
    elif any("突破站上5MA" in t or "5MA轉仰角" in t for t in ktags):
        score += 18.0
        reasons.append("✨突破5MA翻揚")
    elif any("站穩5MA" in t for t in ktags):
        score += 14.0
        reasons.append("📈站穩5MA推進")
    elif price >= ma10.iloc[-1] * 0.995:
        score += 10.0
        reasons.append("💡守穩10MA支撐")
    elif price >= ma20.iloc[-1] * 0.99:
        score += 8.0
        reasons.append("🛡️月線有守支撐扎實")
    else:
        score -= 10.0
        reasons.append("⚠️失守短均線(-10分)")

    if "杯柄" in pattern_name or "VCP" in pattern_name:
        score += 16.0
        reasons.append(f"🏆型態:{pattern_name}")
    elif "多頭排列" in pattern_name:
        score += 10.0
        reasons.append("🏆型態:多頭排列")

    # B. 成交量結構
    if any("滾量換手" in t for t in vtags):
        score += 22.0
        reasons.append("🚀滾量換手量價齊揚")
    elif any("帶量長紅" in t or "帶量突破" in t for t in vtags):
        score += 18.0
        reasons.append("🔥帶量突破實質換手")
    elif vol_ratio >= 1.2:
        score += 10.0
        reasons.append(f"量能增溫({vol_ratio:.1f}x)")
    elif vol_ratio <= 0.85 and price >= ma20.iloc[-1]:
        score += 12.0
        reasons.append(f"📉回測量縮守穩({vol_ratio:.1f}x)")

    if any("天量阻力牆" in t for t in vtags):
        score -= 14.0
        reasons.append("⚠️臨前高天量阻力(-14分)")

    # C. MACD 動態指標
    if any("零軸上強勢多頭" in t for t in mtags):
        score += 20.0
        reasons.append("🚀MACD:多頭紅柱擴大")
    elif any("二次金叉" in t or "零軸上金叉" in t for t in mtags):
        score += 18.0
        reasons.append("✨MACD:零軸上金叉")
    elif any("綠柱收斂" in t for t in mtags):
        score += 12.0
        reasons.append("💡MACD:綠柱收斂空方衰退")
    elif any("死亡交叉" in t for t in mtags):
        score -= 16.0
    elif any("翻綠" in t for t in mtags):
        score -= 10.0

    # D. 月線發動與防守區 (安全墊)
    if 1.0 <= bias_20 <= 8.5 and s20 > 0.1:
        score += 20.0
        reasons.append(f"月線黃金發動區(+{bias_20:.1f}%)")
    elif -2.0 <= bias_20 < 1.0 and s20 >= 0:
        score += 16.0
        reasons.append(f"貼近月線安全成本區(+{bias_20:.1f}%)")
    elif -10.0 <= bias_20 < -2.0:
        score += 10.0
        reasons.append(f"低檔打底負乖離反彈({bias_20:.1f}%)")
    elif bias_20 > 9.5:
        score -= (bias_20 - 9.5) * 1.5

    # E. K棒微觀扣分制 (平滑扣分，不直接處死)
    if intraday_pct < -2.2 and close_loc < 0.30:
        score -= 15.0
        reasons.append(f"⚠️開高走低長黑(-15分,實體{intraday_pct:.1f}%)")
    elif any("假突破" in t or "誘多出貨" in t for t in ktags):
        score -= 12.0
        reasons.append("⚠️假突破上影線(-12分)")
    elif today_pct > 1.0:
        score += 14.0
        reasons.append(f"逆勢抗跌(+{today_pct:.1f}%)")
    elif today_pct >= 0:
        score += 8.0
        reasons.append("抗跌守穩")

    # F. 籌碼總量
    if trust_buy_days >= 3:
        score += 14.0
        reasons.append(f"投信買超({trust_buy_days}/5日)")
    elif inst_buy_days >= 3:
        score += 8.0
        reasons.append(f"法人回補({inst_buy_days}/5日)")

    # G. 實戰流動性與資金佔用折價 (Liquidity Discount)
    if price >= 5000.0:
        score -= 18.0 # 超高價千金股滑價與深度風險折價
    elif price >= 3000.0:
        score -= 12.0
    elif price >= 1000.0:
        score -= 6.0

    stop_loss = round(float(ma20.iloc[-1]) * 0.985, 2)
    risk_pct = max((price - stop_loss) / price * 100, 1.0)
    target_price = round(price * (1 + min(max(risk_pct * 2.2, 12.0), 30.0) / 100), 2)
    rr_ratio = round((target_price - price) / max(price - stop_loss, 0.1), 1)

    return {
        'code': stock_info['code'],
        'name': stock_info['name'],
        'category': stock_info['category'],
        'semantic_tags': stock_info.get('semantic_tags', []),
        'price': price,
        'today_pct': round(today_pct, 2),
        's5': round(s5, 2),
        's20': round(s20, 2),
        'bias_20': round(bias_20, 1),
        'rsi14': round(rsi14, 1),
        'vol_ratio': round(vol_ratio, 1),
        'score': round(score, 1),
        'reasons': reasons,
        'stop_loss': stop_loss,
        'target_price': target_price,
        'rr_ratio': rr_ratio,
        'above_5ma': bool(price >= ma5.iloc[-1]),
        'date': kline[-1].get('date', '最新')
    }

def call_gemini_search(prompt, api_key, models_priority=['gemini-2.5-flash', 'gemini-flash-latest', 'gemini-2.5-flash-lite']):
    """透過 Google Search Grounding 即時聯網搜尋最新月盈年盈營收、季報EPS、法說會利多與法人目標價"""
    for m in models_priority:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "tools": [{"googleSearch": {}}],
            "generationConfig": {"temperature": 0.1}
        }
        try:
            res = requests.post(url, json=payload, timeout=30)
            if res.status_code == 200:
                data = res.json()
                text = data['candidates'][0]['content']['parts'][0]['text']
                return text, m
            elif res.status_code in (503, 429):
                print(f"      [Gemini {m}] 伺服器負載高({res.status_code})，切換備用模型...", flush=True)
                continue
            else:
                print(f"      [Gemini {m}] HTTP {res.status_code}: {res.text[:120]}", flush=True)
        except Exception as e:
            print(f"      [Gemini {m}] 請求異常: {e}", flush=True)
            continue
    return None, None

# =========================================================================
# 🏛️ 【全自動台股產業分類體系與語意本體庫 (Taxonomy & Concept Ontology)】
# -------------------------------------------------------------------------
# AI 擔任規則制定者與裁判，自動為個股進行標準化產業歸類與同義詞對齊。
# 徹底解決 RAM vs 記憶體、CCL vs PCB材料、CoWoS vs 封測之斷層。
# =========================================================================

STOCK_TAXONOMY_REGISTRY = {
    # 半導體 - 晶圓代工與先進製程
    "2330": ("晶圓代工", ["台積電", "先進製程", "2nm", "3nm", "CoWoS", "晶圓代工", "AI晶片"]),
    "2303": ("晶圓代工", ["聯電", "成熟製程", "晶圓代工"]),

    # 半導體 - IC設計與ASIC/IP
    "2454": ("IC設計", ["聯發科", "手機SoC", "天璣", "AI ASIC", "車用晶片", "IC設計"]),
    "3034": ("IC設計", ["聯詠", "驅動IC", "OLED", "ASIC", "車用", "IC設計"]),
    "3443": ("IC設計", ["創意", "ASIC", "設計服務", "台積電體系", "HBM", "矽智財", "IP"]),
    "3545": ("IC設計", ["敦泰", "觸控IC", "驅動IC", "車用觸控", "IC設計"]),
    "3661": ("IC設計", ["世芯-KY", "ASIC", "CSP", "AI加速器", "3nm", "HPC", "矽智財", "IP"]),
    "4919": ("IC設計", ["新唐", "MCU", "微控制器", "BMC", "伺服器控制晶片", "IC設計"]),
    "8227": ("IC設計", ["巨有科技", "ASIC", "設計服務", "台積電DCA", "矽智財", "IP"]),

    # 半導體 - 記憶體與儲存
    "2344": ("記憶體", ["華邦電", "記憶體", "RAM", "DRAM", "NOR Flash", "利基型DRAM"]),
    "2408": ("記憶體", ["南亞科", "記憶體", "RAM", "DRAM", "DDR4", "DDR5"]),
    "3006": ("記憶體", ["晶豪科", "記憶體", "RAM", "DRAM", "利基型DRAM", "SPI NAND", "Flash"]),
    "5289": ("記憶體", ["宜鼎", "記憶體", "工控記憶體", "RAM", "DRAM", "SSD", "邊緣AI"]),
    "5386": ("記憶體", ["青雲", "記憶體", "RAM", "記憶體模組", "顯卡代理"]),
    "6265": ("記憶體", ["方土昶", "記憶體", "RAM", "記憶體通路", "Flash"]),
    "6531": ("記憶體", ["愛普", "記憶體", "VHM", "3D晶圓堆疊", "PSRAM", "客製化記憶體", "AI推理"]),
    "8299": ("記憶體", ["群聯", "記憶體", "NAND", "Flash", "SSD控制晶片", "PCIe Gen5", "aiDAPTIV+"]),

    # 半導體 - 封測與先進封裝
    "2449": ("封測", ["京元電子", "封測", "IC測試", "晶圓測試", "先進封裝", "CoWoS", "AI晶片封測"]),
    "3374": ("封測", ["精材", "封測", "晶圓級封裝", "WLCSP", "CIS封測", "台積電體系"]),
    "6147": ("封測", ["頎邦", "封測", "驅動IC封測", "凸塊", "Bumping", "COF"]),
    "6239": ("封測", ["力成", "封測", "記憶體封測", "先進封裝", "扇出型封裝", "FOPLP"]),
    "8150": ("封測", ["南茂", "封測", "記憶體封測", "DDIC封測", "驅動IC封測"]),

    # 半導體 - 設備與測試介面
    "3055": ("半導體設備", ["蔚華科", "半導體設備", "測試設備", "檢測", "封裝設備"]),
    "6187": ("半導體設備", ["萬潤", "半導體設備", "CoWoS設備", "先進封裝設備", "點膠機", "貼合設備"]),
    "6217": ("測試介面", ["中探針", "探針", "測試治具", "連接器", "測試介面"]),
    "6223": ("測試介面", ["旺矽", "探針卡", "Probe Card", "垂直探針卡", "VPC", "測試介面", "AI晶片"]),
    "6515": ("測試介面", ["穎崴", "測試座", "Test Socket", "探針卡", "垂直探針卡", "AI晶片測試", "測試介面"]),
    "6683": ("測試介面", ["雍智科技", "測試載板", "探針卡模組", "IC測試載板", "測試介面"]),
    "7769": ("半導體設備", ["鴻勁", "分選機", "Handler", "ATC溫控", "CoWoS測試", "先進封裝設備"]),

    # 半導體 - 矽晶圓與材料
    "3532": ("矽晶圓", ["台勝科", "矽晶圓", "8吋矽晶圓", "12吋矽晶圓", "半導體材料"]),
    "5483": ("矽晶圓", ["中美晶", "矽晶圓", "太陽能", "半導體特化", "化合物半導體"]),
    "6182": ("矽晶圓", ["合晶", "矽晶圓", "重摻矽晶圓", "車用半導體材料"]),
    "6488": ("矽晶圓", ["環球晶", "矽晶圓", "12吋矽晶圓", "碳化矽", "SiC", "全球前三大"]),

    # 電腦與硬體 - AI伺服器與系統組裝
    "2324": ("伺服器", ["仁寶", "伺服器", "AI伺服器", "ODM", "系統組裝", "筆電代工"]),
    "3231": ("伺服器", ["緯創", "伺服器", "AI伺服器", "ODM", "GPU基板", "GB200", "系統組裝"]),
    "6669": ("伺服器", ["緯穎", "伺服器", "AI伺服器", "雲端伺服器", "白牌伺服器", "ASIC伺服器", "CSP"]),

    # 電腦與硬體 - 伺服器機構與滑軌
    "2059": ("伺服器機構", ["川湖", "滑軌", "導軌", "伺服器導軌", "AI伺服器滑軌", "機架機構"]),
    "3693": ("伺服器機構", ["營邦", "伺服器機箱", "機櫃", "水冷機箱", "雲端機架", "伺服器機構"]),
    "6584": ("伺服器機構", ["南俊國際", "滑軌", "導軌", "伺服器導軌", "AWS滑軌", "伺服器機構"]),
    "6805": ("伺服器機構", ["富世達", "軸承", "鉸鏈", "摺疊鉸鏈", "伺服器滑軌快扣", "伺服器機構"]),

    # 電腦與硬體 - 散熱模組與液冷
    "2486": ("散熱", ["一詮", "散熱", "均熱片", "導線架", "高階散熱", "水冷"]),
    "3017": ("散熱", ["奇鋐", "散熱", "水冷", "液冷", "3D VC", "水冷板", "散熱風扇", "CDU"]),
    "3324": ("散熱", ["雙鴻", "散熱", "水冷", "液冷", "水冷板", "CDU", "液冷系統"]),
    "3653": ("散熱", ["健策", "散熱", "均熱片", "ILM扣件", "伺服器扣件", "散熱模組"]),
    "8996": ("散熱", ["高力", "散熱", "水冷", "液冷", "熱交換器", "分歧管", "水冷板"]),

    # 電腦與硬體 - 品牌電腦與板卡
    "2357": ("電腦板卡", ["華碩", "AI PC", "主機板", "顯示卡", "電競", "伺服器", "筆電"]),
    "2377": ("電腦板卡", ["微星", "AI PC", "主機板", "顯示卡", "電競筆電", "工業電腦", "Edge AI"]),
    "2395": ("工業電腦", ["研華", "工業電腦", "IPC", "Edge AI", "邊緣運算", "工業物聯網", "自動化"]),

    # 電子零組件 - 銅箔基板與PCB材料
    "1815": ("PCB材料", ["富喬", "玻纖布", "Low-Dk", "PCB材料", "銅箔基板材料"]),
    "2383": ("銅箔基板", ["台光電", "CCL", "銅箔基板", "無鹵板", "AI伺服器UBB", "PCB材料"]),
    "6213": ("銅箔基板", ["聯茂", "CCL", "銅箔基板", "高速基板", "PCB材料"]),
    "6274": ("銅箔基板", ["台燿", "CCL", "銅箔基板", "極低損耗材料", "800G交換機", "PCB材料"]),
    "8021": ("PCB材料", ["尖點", "鑽針", "PCB鑽針", "鍍膜耗材", "PCB加工"]),
    "8039": ("PCB材料", ["台虹", "FCCL", "軟性銅箔基板", "PCB材料"]),
    "8358": ("PCB材料", ["金居", "銅箔", "電解銅箔", "RG系列", "高速銅箔", "PCB材料"]),

    # 電子零組件 - PCB印刷電路板
    "2368": ("PCB", ["金像電", "PCB", "印刷電路板", "AI伺服器板", "高層板", "多層板"]),
    "4958": ("PCB", ["臻鼎-KY", "PCB", "軟板", "FPC", "載板", "HDI", "印刷電路板"]),

    # 電子零組件 - IC載板
    "3037": ("IC載板", ["欣興", "載板", "IC載板", "ABF載板", "BT載板", "CoWoS載板"]),
    "3189": ("IC載板", ["景碩", "載板", "IC載板", "ABF載板", "BT載板"]),
    "8046": ("IC載板", ["南電", "載板", "IC載板", "ABF載板", "BT載板", "網通載板"]),

    # 電子零組件 - 被動元件
    "2327": ("被動元件", ["國巨", "被動元件", "MLCC", "晶片電阻", "電感", "AI電源"]),
    "2492": ("被動元件", ["華新科", "被動元件", "MLCC", "晶片電阻", "低溫共燒陶瓷"]),
    "3026": ("被動元件", ["禾伸堂", "被動元件", "MLCC", "高壓MLCC", "伺服器電源電容", "陶瓷電容"]),
    "6173": ("被動元件", ["信昌電", "被動元件", "MLCC", "大尺寸MLCC", "介電陶瓷粉末"]),
    "6207": ("被動元件", ["雷科", "被動元件", "被動元件包材", "雷射修阻機", "CoWoS設備"]),
    "6449": ("被動元件", ["鈺邦", "被動元件", "固態電容", "捲繞型電容", "AI伺服器主板"]),

    # 電子零組件 - 功率半導體
    "2481": ("功率元件", ["強茂", "功率元件", "MOSFET", "二極體", "車用電子", "SiC"]),
    "5425": ("功率元件", ["台半", "功率元件", "車用二極體", "MOSFET", "工控"]),
    "8261": ("功率元件", ["富鼎", "功率元件", "MOSFET", "高壓MOSFET", "鴻海體系"]),

    # 電子零組件 - 光學鏡頭
    "3008": ("光學", ["大立光", "光學", "鏡頭", "手機鏡頭", "潛望式鏡頭", "塑膠鏡片"]),
    "3362": ("光學", ["先進光", "光學", "鏡頭", "筆電鏡頭", "車用鏡頭", "指紋辨識"]),
    "3406": ("光學", ["玉晶光", "光學", "鏡頭", "蘋果鏡頭", "VR/AR透鏡", "Pancake"]),
    "3441": ("光學", ["聯一光", "光學", "鏡頭", "光學毛胚", "玻璃鏡片", "車用鏡片"]),

    # 電子零組件 - 高速連接器與線纜
    "3665": ("連接線器", ["貿聯-KY", "連接線器", "連接器", "高速傳輸線", "線束", "特斯拉", "輝達供應鏈"]),

    # 電子零組件 - 電源供應與能源
    "2301": ("電源供應", ["光寶科", "電源供應", "電源", "PSU", "伺服器電源", "鈦金級電源", "液冷機櫃"]),
    "2308": ("電源供應", ["台達電", "電源供應", "電源", "PSU", "伺服器電源", "液冷散熱系統", "儲能"]),

    # 通訊與次世代傳輸 - 光通訊與矽光子CPO
    "2426": ("光通訊CPO", ["鼎元", "光通訊", "CPO", "矽光子", "感測元件", "富采集團"]),
    "2455": ("光通訊CPO", ["全新", "光通訊", "CPO", "砷化鎵磊晶", "VCSEL", "PA", "矽光子"]),
    "3081": ("光通訊CPO", ["聯亞", "光通訊", "CPO", "矽光子", "磊晶片", "雷射二極體"]),
    "3234": ("光通訊CPO", ["光環", "光通訊", "CPO", "光收發模組", "雷射晶粒"]),
    "3363": ("光通訊CPO", ["上詮", "光通訊", "CPO", "矽光子", "光纖陣列", "台積電供應鏈"]),
    "3450": ("光通訊CPO", ["聯鈞", "光通訊", "CPO", "矽光子", "雷射封裝", "光通訊封測"]),

    # 通訊與次世代傳輸 - 網通設備與交換器
    "2345": ("網通", ["智邦", "網通", "交換器", "Switch", "400G", "800G交換器", "白牌網通", "光通訊"]),

    # 通訊與次世代傳輸 - 衛星通訊與射頻
    "3105": ("低軌衛星", ["穩懋", "低軌衛星", "砷化鎵代工", "PA", "功率放大器", "射頻元件"]),
    "3491": ("低軌衛星", ["昇達科", "低軌衛星", "毫米波元件", "衛星天線", "衛星地面站"]),

    # 綠能與儲能系統 - 儲能與BMS
    "4931": ("儲能BMS", ["新盛力", "儲能BMS", "BMS", "電池模組", "伺服器BBU", "手工具電池"]),
    "5309": ("儲能BMS", ["系統電", "儲能BMS", "BMS", "儲能櫃", "TPMS", "工控電池"]),
    "6781": ("儲能BMS", ["AES-KY", "儲能BMS", "BMS", "伺服器BBU", "備援電池", "二輪電動車"]),

    # 生技醫療 - 新藥
    "6446": ("生技醫療", ["藥華藥", "生技醫療", "新藥", "Besremi", "罕見疾病藥", "生技權值"]),

    # 金融保險 - 核心金控與銀行
    "2881": ("金融保險", ["富邦金", "金融保險", "金控", "富邦人壽", "台北富邦銀行", "富邦證券", "獲利王", "高股息", "壽險金控"]),
    "2882": ("金融保險", ["國泰金", "金融保險", "金控", "國泰人壽", "國泰世華", "壽險金控", "高股息"]),
    "2891": ("金融保險", ["中信金", "金融保險", "金控", "中國信託", "台灣人壽", "銀行金控", "高股息"]),
    "2884": ("金融保險", ["玉山金", "金融保險", "金控", "玉山銀行", "財富管理", "優質金控"]),
    "2886": ("金融保險", ["兆豐金", "金融保險", "金控", "官股金控", "外匯龍頭", "高股息"]),
    "2885": ("金融保險", ["元大金", "金融保險", "金控", "證券龍頭", "ETF發行", "證券手續費"]),

    # 重電與綠能電網
    "1519": ("重電綠能", ["華城", "重電綠能", "變壓器", "外銷美國", "強韌電網", "AI電力需求"]),
    "1513": ("重電綠能", ["中興電", "重電綠能", "GIS", "氣體絕緣開關", "強韌電網", "氫能"]),
    "1503": ("重電綠能", ["士電", "重電綠能", "變壓器", "外銷", "強韌電網", "綠能"]),
    "1514": ("重電綠能", ["亞力", "重電綠能", "配電盤", "台積電擴廠供電", "強韌電網"]),

    # 航運與海空物流
    "2603": ("航運", ["長榮", "航運", "貨櫃航運", "海洋聯盟", "SCFI", "歐洲線", "美線"]),
    "2609": ("航運", ["陽明", "航運", "貨櫃航運", "THE聯盟", "SCFI", "運價反彈"]),
    "2615": ("航運", ["萬海", "航運", "貨櫃航運", "近洋線", "美西線", "運價"]),
    "2618": ("航運", ["長榮航", "航運", "航空", "客運復甦", "航空貨運", "AI伺服器空運"])
}

CATEGORY_DIR_NORMALIZER = {
    "cpo": "光通訊CPO",
    "ic設計": "IC設計",
    "pcb材料": "PCB材料",
    "pcb": "PCB",
    "伺服器": "伺服器",
    "散熱": "散熱",
    "封測": "封測",
    "設備": "半導體設備",
    "工業電腦": "工業電腦",
    "被動元件": "被動元件",
    "網通": "網通",
    "載板": "IC載板",
    "探針": "測試介面",
    "晶圓代工": "晶圓代工",
    "記憶體": "記憶體",
    "軸承摺疊": "伺服器機構",
    "連接線器": "連接線器",
    "電源供應": "電源供應",
    "儲能bms": "儲能BMS",
    "低軌衛星": "低軌衛星",
    "光學": "光學",
    "功率元件": "功率元件",
    "矽晶圓": "矽晶圓",
    "生技醫療": "生技醫療",
    "金融": "金融保險",
    "金融保險": "金融保險",
    "金控": "金融保險",
    "航運": "航運",
    "重電": "重電綠能",
    "重電綠能": "重電綠能",
    "營建": "營建"
}

SECTOR_SYNONYM_MAP = {
    "記憶體": ["RAM", "DRAM", "NAND", "FLASH", "ROM", "記憶體模組", "利基型DRAM", "快閃記憶體"],
    "被動元件": ["MLCC", "電容", "電阻", "電感", "保護元件", "高容值電容", "固態電容"],
    "銅箔基板": ["CCL", "銅箔基板", "無鹵基板", "銅箔", "玻纖布", "PCB材料"],
    "PCB材料": ["CCL", "銅箔基板", "玻纖布", "銅箔", "鑽針", "FCCL"],
    "PCB": ["PCB", "印刷電路板", "硬板", "軟板", "FPC", "多層板", "HDI"],
    "IC載板": ["載板", "ABF", "BT", "IC載板", "先進封裝載板"],
    "IC設計": ["ASIC", "IP", "矽智財", "晶片設計", "MCU", "SOC", "IC設計"],
    "散熱": ["水冷", "液冷", "散熱模組", "CDU", "水冷板", "熱管", "熱板", "風扇", "散熱水冷"],
    "伺服器": ["SERVER", "ODM", "OEM", "白牌伺服器", "機架", "AI伺服器", "GPU基板"],
    "伺服器機構": ["滑軌", "導軌", "伺服器機箱", "機櫃", "水冷機箱", "軸承", "快扣"],
    "光通訊CPO": ["CPO", "矽光子", "光通訊", "光收發", "800G", "1.6T", "光模組", "光纖"],
    "網通": ["交換器", "SWITCH", "800G", "400G", "路由器", "網通設備"],
    "封測": ["先進封裝", "COWOS", "FOPLP", "測試", "晶圓測試", "OSAT", "封裝", "SOIC"],
    "測試介面": ["探針", "探針卡", "PROBE CARD", "測試座", "SOCKET", "VPC", "垂直探針卡"],
    "半導體設備": ["設備", "COWOS設備", "封裝設備", "分選機", "HANDLER", "點膠機"],
    "電源供應": ["電源", "PSU", "電源供應器", "伺服器電源", "變壓器", "逆變器", "UPS"],
    "儲能BMS": ["電池", "鋰電池", "儲能", "BMS", "BBU", "備援電池", "儲能櫃"],
    "低軌衛星": ["低軌衛星", "LEO", "衛星天線", "地面站", "毫米波", "射頻", "PA"],
    "電腦板卡": ["AI PC", "主機板", "顯示卡", "電競", "筆電", "PC"],
    "工業電腦": ["IPC", "EDGE AI", "邊緣運算", "工業物聯網", "工控電腦"],
    "金融保險": ["金控", "銀行", "壽險", "證券", "金融", "保險", "高股息", "殖利率", "降息受惠", "獲利王"],
    "重電綠能": ["重電", "強韌電網", "變壓器", "綠能", "儲能", "電網", "電機機械"],
    "航運": ["貨櫃", "散裝", "航空", "海運", "SCFI", "BDI", "運價"]
}

SECTOR_CONCEPT_ONTOLOGY = {
    "AI伺服器/ODM": {
        "categories": {"伺服器", "伺服器機構", "電腦板卡", "電源供應", "連接線器"},
        "keywords": {"伺服器", "ODM", "GB200", "GB300", "BLACKWELL", "AI PC", "機櫃", "滑軌", "川湖", "廣達", "鴻海", "緯穎", "緯創"}
    },
    "散熱水冷": {
        "categories": {"散熱"},
        "keywords": {"散熱", "水冷", "液冷", "水冷板", "CDU", "快接頭", "3D VC", "奇鋐", "雙鴻", "健策", "高力", "建準", "一詮"}
    },
    "高階被動元件": {
        "categories": {"被動元件"},
        "keywords": {"被動元件", "MLCC", "電容", "電阻", "電感", "國巨", "華新科", "禾伸堂", "鈺邦", "信昌電"}
    },
    "先進封裝CoWoS/設備": {
        "categories": {"封測", "半導體設備", "測試介面", "晶圓代工", "IC載板"},
        "keywords": {"封測", "先進封裝", "COWOS", "SOIC", "FOPLP", "台積電", "京元電子", "萬潤", "弘塑", "辛耘", "穎崴", "旺矽", "鴻勁"}
    },
    "光通訊CPO/矽光子": {
        "categories": {"光通訊CPO", "網通"},
        "keywords": {"CPO", "矽光子", "光通訊", "光收發", "800G", "1.6T", "光模組", "智邦", "光聖", "上詮", "聯鈞", "華星光", "聯亞"}
    },
    "工業電腦Edge AI": {
        "categories": {"工業電腦", "電腦板卡"},
        "keywords": {"工業電腦", "IPC", "EDGE AI", "邊緣運算", "研華", "微星", "華碩", "威強電"}
    },
    "記憶體": {
        "categories": {"記憶體"},
        "keywords": {"記憶體", "RAM", "DRAM", "NAND", "FLASH", "晶豪科", "南亞科", "華邦電", "愛普", "群聯"}
    },
    "銅箔基板與PCB": {
        "categories": {"銅箔基板", "PCB材料", "PCB", "IC載板"},
        "keywords": {"CCL", "銅箔基板", "PCB", "ABF", "台光電", "台燿", "聯茂", "金像電", "欣興"}
    },
    "金融保險/金控": {
        "categories": {"金融保險", "金融股", "金控"},
        "keywords": {"金融", "金控", "銀行", "壽險", "富邦金", "國泰金", "中信金", "玉山金", "兆豐金", "元大金", "高股息", "降息", "股利"}
    },
    "重電綠能/電網": {
        "categories": {"重電綠能", "電機機械", "電線電纜"},
        "keywords": {"重電", "綠能", "強韌電網", "華城", "士電", "中興電", "亞力", "大亞", "變壓器", "台電"}
    },
    "航運/海空運": {
        "categories": {"航運"},
        "keywords": {"航運", "貨櫃", "散裝", "航空", "長榮", "陽明", "萬海", "長榮航", "華航", "SCFI", "運價"}
    }
}

def classify_stock(code: str, name: str, original_category: str = None) -> tuple[str, list[str]]:
    """
    自動化產業分類與語意標籤判定器 (AI 裁判引擎)
    優先順序：
    1. 權威代碼庫 (STOCK_TAXONOMY_REGISTRY) 直接判定
    2. 台股大盤前綴編碼規則 (TWSE/TPEx Prefix Rules: 28xx 金融、26xx 航運、15xx 重電等)
    3. 中文名稱語意特徵規則 (*金, *銀行, *證券, *航運, *重電, *藥)
    4. 資料夾名稱正規化 (CATEGORY_DIR_NORMALIZER) 與同義詞拓展
    5. 保留原始有效分類，或收斂為通用分類
    """
    code_str = str(code).strip()
    name_str = str(name).strip()
    raw_cat = (original_category or "").strip()

    # 1. 權威代碼註冊表優先
    if code_str in STOCK_TAXONOMY_REGISTRY:
        cat, tags = STOCK_TAXONOMY_REGISTRY[code_str]
        expanded_tags = list(tags)
        if cat in SECTOR_SYNONYM_MAP:
            expanded_tags.extend(SECTOR_SYNONYM_MAP[cat])
        return cat, list(set(expanded_tags))

    # 2. 台股族群前綴標準規則 (TWSE/TPEx Prefix Rules)
    if len(code_str) == 4 and code_str.isdigit():
        p2 = code_str[:2]
        if p2 == "28":
            tags = [name_str, "金融保險", "金控", "銀行", "壽險", "高股息", "殖利率", "降息受惠"]
            return "金融保險", list(set(tags))
        elif p2 == "26":
            tags = [name_str, "航運", "貨櫃", "散裝", "航空", "運價", "SCFI"]
            return "航運", list(set(tags))
        elif p2 == "15":
            tags = [name_str, "重電綠能", "電機機械", "強韌電網", "變壓器"]
            return "重電綠能", list(set(tags))
        elif p2 == "16":
            tags = [name_str, "電線電纜", "線纜", "強韌電網", "銅價"]
            return "電線電纜", list(set(tags))
        elif p2 == "20":
            tags = [name_str, "鋼鐵", "鋼材", "原物料"]
            return "鋼鐵", list(set(tags))
        elif p2 in ("25", "55"):
            tags = [name_str, "營建", "建案", "資產"]
            return "營建", list(set(tags))

    # 3. 中文名稱語意特徵規則 (Name Semantic Pattern Rules)
    if any(k in name_str for k in ("金控", "銀行", "證券", "保險", "人壽", "期貨")) or (name_str.endswith("金") and len(name_str) <= 4):
        tags = [name_str, "金融保險", "金控", "銀行", "壽險", "高股息", "殖利率", "降息受惠"]
        return "金融保險", list(set(tags))
    elif any(k in name_str for k in ("航運", "航空", "海運", "貨櫃")):
        tags = [name_str, "航運", "貨櫃", "散裝", "航空", "運價"]
        return "航運", list(set(tags))
    elif any(k in name_str for k in ("重電", "綠能", "變壓器")):
        tags = [name_str, "重電綠能", "強韌電網", "變壓器"]
        return "重電綠能", list(set(tags))
    elif any(k in name_str for k in ("生技", "新藥", "醫藥", "生醫")):
        tags = [name_str, "生技醫療", "新藥", "生技權值"]
        return "生技醫療", list(set(tags))

    # 4. 資料夾名稱標準化映射
    norm_key = raw_cat.lower()
    if norm_key and norm_key not in ("reports", "未分類", "新報表", "none"):
        if norm_key in CATEGORY_DIR_NORMALIZER:
            std_cat = CATEGORY_DIR_NORMALIZER[norm_key]
            tags = [std_cat, raw_cat, name_str]
            if std_cat in SECTOR_SYNONYM_MAP:
                tags.extend(SECTOR_SYNONYM_MAP[std_cat])
            return std_cat, list(set(tags))
        return raw_cat, [raw_cat, name_str]

    return "電子零組件", [name_str, "電子零組件"]

def match_stock_to_hot_sectors(cand: dict, hot_sectors: list) -> tuple[dict | None, float]:
    """
    語意本體風口契合度智能比對器 (Concept Ontology Matcher)
    以多維度概念本體、同義詞拓展庫與重大法說催化劑加權比對，
    徹底取代原本粗暴且容易漏失的純字串包含比對。
    """
    category = cand.get('category', '')
    tags = {t.upper() for t in cand.get('semantic_tags', []) if t}
    tags.add(category.upper())
    name = cand.get('name', '').upper()
    code = cand.get('code', '')

    best_sector = None
    max_score = 0.0

    for sec in hot_sectors:
        sec_name = sec.get('sector_name', '')
        sec_tags = {t.upper() for t in sec.get('related_tags', []) if t}
        sec_tags.add(sec_name.upper())
        catalysts = sec.get('catalysts', '').upper()

        score = 0.0

        # 維度 1：產業本體概念庫比對 (最高權重 40~100分)
        for concept_name, concept in SECTOR_CONCEPT_ONTOLOGY.items():
            c_name_up = concept_name.upper()
            if c_name_up in sec_name.upper() or sec_name.upper() in c_name_up or any(k in sec_name.upper() for k in concept["keywords"]):
                if category in concept["categories"]:
                    score += 40.0
                if any(k in tags for k in concept["keywords"]):
                    score += 25.0
                if name in concept["keywords"] or code in concept["keywords"]:
                    score += 35.0

        # 維度 2：同義詞與語意標籤交集比對 (每個命中標籤 +15分)
        overlap = tags.intersection(sec_tags)
        if overlap:
            score += len(overlap) * 15.0

        # 維度 3：族群名稱與類別模糊包含 (+20分)
        for t in sec_tags:
            if t and (t in category.upper() or category.upper() in t or t in name or name in t):
                score += 20.0
                break

        # 維度 4：重大催化劑內文明確提及個股名稱 (+25分)
        if name and name in catalysts:
            score += 25.0

        if score > max_score and score >= 20.0:
            max_score = score
            best_sector = sec

    return best_sector, max_score

SECTOR_CACHE_FILE = ROOT_DIR / "market_hot_sectors_cache.json"

def fetch_market_hot_sectors(api_key, as_of_date):
    """【步驟 1：先產業後個股】透過 Google Search Grounding 即時聯網搜尋當前台股市場最受資金追捧的強勢族群與法說動能"""
    if SECTOR_CACHE_FILE.exists():
        try:
            cached = json.loads(SECTOR_CACHE_FILE.read_text(encoding="utf-8"))
            if cached.get('as_of_date') == as_of_date and cached.get('hot_sectors'):
                return cached.get('overview', ''), cached.get('hot_sectors', [])
        except Exception:
            pass

    prompt = f"""【系統角色與任務】
你是一名台股外資頂級量化操盤手與產業研究總監。基準審查日期為：{as_of_date}。
請透過 Google Search 即時查核當前（{as_of_date} 當週）台股盤面最新受市場大資金（外資/投信/主力）追捧的「核心強勢產業族群、重大法說會利多與主流題材」：
請找出目前市場資金最青睞、動能最強的 5 至 8 個核心產業族群（例如：AI伺服器/ODM、先進封裝CoWoS/設備、散熱水冷、光通訊CPO/矽光子、ASIC/IP設計、工業電腦Edge AI、高階被動元件、重電綠能等）。

【熱度評分標準 (heat_level 嚴格量化，嚴禁全員5星)】
- 5 星 (爆發性主線)：族群出現集體大漲或成交金額佔大盤前三名，具重大產業急單或國際大廠（如NVIDIA、蘋果、台積電）關鍵實質催化劑。
- 4 星 (強勢輪動線)：族群有多檔個股站穩均線起漲，法說會展望正向，外資投信連續買超。
- 3 星 (潛在發酵線)：低檔轉機或少數龍頭突圍，題材初期萌芽。
(必須依照真實盤面資金流向給予 3~5 分的分級)

【輸出規格】
請以繁體中文直接輸出標準 JSON 格式（可包含 ```json 代碼區塊），禁止任何引言或結尾廢話，結構如下：
{{
  "market_overview": "一句話總結今日台股市場焦點與資金主軸（50字以內）",
  "hot_sectors": [
    {{
      "sector_name": "產業族群名稱",
      "heat_level": 5,
      "catalysts": "最新重大法說會重點、關鍵大訂單或產業爆發動能（杜絕籠統空話）",
      "related_tags": ["相關標籤或次產業1", "次產業2"]
    }}
  ]
}}
"""
    overview = "台股資金高度聚焦於 AI 伺服器供應鏈、先進封裝、散熱水冷與高速運算週邊。"
    hot_sectors = [
        {"sector_name": "AI伺服器/代工", "heat_level": 5, "catalysts": "Blackwell/GB300放量出貨，CSP大廠資本支出上修", "related_tags": ["伺服器", "代工", "ODM", "AI"]},
        {"sector_name": "先進封裝(CoWoS/設備)", "heat_level": 5, "catalysts": "台積電CoWoS產能滿載，檢測與封裝設備需求爆發", "related_tags": ["設備", "CoWoS", "半導體設備", "測試"]},
        {"sector_name": "散熱水冷/液冷", "heat_level": 5, "catalysts": "高熱功耗TDP推動水冷板、CDU與快接頭全面滲透", "related_tags": ["散熱", "水冷", "液冷"]},
        {"sector_name": "光通訊(CPO/矽光子)", "heat_level": 5, "catalysts": "800G/1.6T高速光收發模組放量，CPO架構進入商用驗證", "related_tags": ["CPO", "光通訊", "網通"]},
        {"sector_name": "ASIC/IP設計", "heat_level": 4, "catalysts": "CSP自研晶片專案放量，NRE與IP授權營收逐季創高", "related_tags": ["IC設計", "ASIC", "IP", "矽智財"]},
        {"sector_name": "工業電腦/Edge AI", "heat_level": 4, "catalysts": "邊緣運算與智慧製造需求擴大，IPC廠商轉型AI應用", "related_tags": ["工業電腦", "IPC", "Edge AI"]},
        {"sector_name": "被動元件/高階電容", "heat_level": 4, "catalysts": "AI高階電源NP0 MLCC與固態電容需求急增，交期拉長", "related_tags": ["被動元件", "MLCC", "電容"]}
    ]

    text, _ = call_gemini_search(prompt, api_key)
    if text:
        try:
            m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
            json_str = m.group(1) if m else text.strip()
            # 容錯處理：消除可能存在的尾隨逗號
            json_str = re.sub(r',\s*([\}\]])', r'\1', json_str)
            data = json.loads(json_str)
            if data.get('hot_sectors'):
                hot_sectors = data['hot_sectors']
            if data.get('market_overview'):
                overview = data['market_overview']
        except Exception:
            pass

    try:
        SECTOR_CACHE_FILE.write_text(json.dumps({
            'as_of_date': as_of_date,
            'overview': overview,
            'hot_sectors': hot_sectors
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    return overview, hot_sectors

FUNDAMENTAL_CACHE_FILE = ROOT_DIR / "stock_fundamental_cache.json"
def load_fundamental_cache():
    if FUNDAMENTAL_CACHE_FILE.exists():
        try:
            return json.loads(FUNDAMENTAL_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def save_fundamental_cache(cache):
    try:
        FUNDAMENTAL_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

def audit_stock_with_gemini(candidate, api_key, as_of_date):
    code = candidate['code']
    cache = load_fundamental_cache()

    # 1. 優先取用快取（必須具備法人目標價與月盈年盈資訊，若缺少則重新聯網查核）
    if code in cache:
        item = cache[code]
        if item.get('target_price') and item.get('monthly_rev'):
            return {
                'monthly_rev': item.get('monthly_rev', ''),
                'earnings': item.get('earnings', ''),
                'catalyst': item.get('catalyst', ''),
                'target_price': item.get('target_price', ''),
                'disposition': item.get('disposition', ''),
                'is_trap': False,
                'confidence_bonus': 10,
                '_used_model': 'Google Search Grounding (Cached)'
            }

    # 2. 即時聯網 Google Search Grounding 深度查核五大關鍵事實
    prompt = f"""
【審計任務】
你是極度嚴苛的台股避險基金首席審計官。基準審計日：{as_of_date}。
請透過 Google Search 即時查核台股「{candidate['code']} {candidate['name']}」（產業分類：{candidate['category']}）最新之客觀公開數據、重大訊息與法人目標價：

【防偽與時效鐵律（嚴格執行）】
1. [月營收]：必須為「最新公告月份」（如前月或當月最新自結營收），必須包含數字、MoM 與 YoY。若未查得請填「最新公告YoY查核中」。
2. [獲利季報]：必須為「最新一季」公開資訊觀測站已公佈財報之 EPS 與毛利率。
3. [法人目標價]：★嚴格僅採納距今「90天內」國內外券商之最新研究報告！★嚴禁引用超過3個月前的過期報告。若近期無券商出具報告，請填「近三個月無公開機構目標價」。
4. [處置狀態]：★僅確認「{as_of_date} 當日」是否由證交所/櫃買中心公告執行分盤撮合處置！若正常交易請填「正常交易」，切勿將歷史處置紀錄誤判為現行處置！

【請嚴格以下列五行格式精確輸出，每行以指定標籤開頭，禁止任何 Markdown 語法標籤（如 ** 或 #）與引言】：
月盈年盈：[何月份單月營收數字、MoM月增率、YoY年增率，例: 7月營收193.42億元(MoM+8.93%, YoY+14.5%)]
最新獲利：[最新一季財報EPS、毛利率與累計獲利，例: Q2 EPS 3.55元，毛利率16.0%]
法說利多：[最新法說會核心要點、關鍵大訂單或擴產動能，25字以內]
法人目標價：[外資或國內大型券商90天內最新研究報告之目標價與評等，例: 某外資目標價210元(買進) 或 近三個月無公開機構目標價]
處置狀態：[正常交易 或 處置分盤撮合]
"""
    text, used_model = call_gemini_search(prompt, api_key)
    if text:
        rev = ""
        earn = ""
        cat = ""
        target_price = ""
        disposition = ""

        for line in text.splitlines():
            clean = line.strip().replace('*', '').replace('#', '').strip('- ').strip()
            if not clean or len(clean) < 3:
                continue
            if ('月盈' in clean or '年盈' in clean or '營收' in clean or 'MoM' in clean or 'YoY' in clean) and not rev:
                rev = re.sub(r'^(?:月盈年盈|月盈率|年盈率|營收|最新營收|單月營收)[：:]\s*', '', clean).replace('|', '/').strip()
            elif ('EPS' in clean or '毛利' in clean or '獲利' in clean or '淨利' in clean) and not earn:
                earn = re.sub(r'^(?:最新獲利|獲利表現|獲利|財報獲利)[：:]\s*', '', clean).replace('|', '/').strip()
            elif ('法說' in clean or '訂單' in clean or '大單' in clean or '利多' in clean or '產能' in clean) and not cat:
                cat = re.sub(r'^(?:法說利多|法說重點|法說會|實質利多|利多消息|利多)[：:]\s*', '', clean).replace('|', '/').strip()
            elif ('目標價' in clean or '評等' in clean or '外資' in clean or '券商' in clean) and not target_price:
                target_price = re.sub(r'^(?:法人目標價|目標價|法人評等)[：:]\s*', '', clean).replace('|', '/').strip()
            elif '處置' in clean and not disposition:
                if '處置分盤撮合' in clean or '列入處置' in clean or '處置股' in clean:
                    if '正常交易' not in clean and '未列入' not in clean and '解除' not in clean:
                        disposition = "列入處置股(分盤撮合/流動性急凍)"

        junk = ['以基本面訂單為主', '基本面良好', '待後續', '無顯著']
        for j in junk:
            if j in rev: rev = ""
            if j in earn: earn = ""
            if j in cat: cat = ""
            if j in target_price: target_price = ""

        # 寫入快取持久化
        cache[code] = {
            'name': candidate['name'],
            'monthly_rev': rev,
            'earnings': earn,
            'catalyst': cat,
            'target_price': target_price,
            'disposition': disposition,
            'updated_at': as_of_date
        }
        save_fundamental_cache(cache)

        return {
            'monthly_rev': rev,
            'earnings': earn,
            'catalyst': cat,
            'target_price': target_price,
            'disposition': disposition,
            '_used_model': used_model
        }
    return None

def evaluate_holistic_score(cand, hot_sectors):
    """
    【步驟 4：全維度多因子融合評估矩陣】
    嚴格遵守：先有產業新聞與個股基本面事實，再做評估排榜！
    融合因子：
    1. 基礎技術量化分 (均線/量能/MACD)
    2. 產業風口契合度加分 (Sector Wind Bonus, +15~+25)
    3. 營收成長力加分 (Revenue Growth Bonus, +8~+18)
    4. 獲利品質加分 (EPS & Margin Bonus, +6~+14)
    5. 法說會與重大動態加分 (Briefing Catalyst Bonus, +8~+18)
    6. 法人目標價上檔空間加分 (Target Price Upside Bonus, +8~+16)
    """
    score = float(cand.get('score', 50.0))
    reasons = list(cand.get('reasons', []))
    category = cand.get('category', '')
    name = cand.get('name', '')
    monthly_rev = cand.get('monthly_rev', '')
    earnings = cand.get('earnings', '')
    catalyst = cand.get('catalyst', '')
    target_price_str = cand.get('target_price', '')
    price = cand.get('price', 1.0)

    matched_sector, match_strength = match_stock_to_hot_sectors(cand, hot_sectors)

    if matched_sector:
        heat = matched_sector.get('heat_level', 4)
        sec_bonus = 22.0 if heat >= 5 else 16.0
        if match_strength >= 50.0:
            sec_bonus += 3.0 # 高度契合強勢概念本體額外加成
        score += sec_bonus
        cand['matched_sector'] = matched_sector.get('sector_name', '')
        reasons.insert(0, f"🔥踩中市場主流風口:【{matched_sector.get('sector_name', '')}】(+{sec_bonus:.0f}分)")
    else:
        score -= 6.0 # 未在主流產業風口微幅折價

    # B. 營收動能評估 (MoM / YoY)
    rev_bonus = 0.0
    if monthly_rev:
        yoy_m = re.search(r'YoY\s*([+-]?\d+(?:\.\d+)?)\s*%', monthly_rev, re.IGNORECASE)
        mom_m = re.search(r'MoM\s*([+-]?\d+(?:\.\d+)?)\s*%', monthly_rev, re.IGNORECASE)
        yoy_val = float(yoy_m.group(1)) if yoy_m else None
        mom_val = float(mom_m.group(1)) if mom_m else None

        if yoy_val is not None:
            if yoy_val >= 50.0:
                rev_bonus += 16.0
            elif yoy_val >= 20.0:
                rev_bonus += 12.0
            elif yoy_val >= 0.0:
                rev_bonus += 6.0
            elif yoy_val < -10.0:
                rev_bonus -= 10.0

        if mom_val is not None:
            if mom_val >= 10.0:
                rev_bonus += 8.0
            elif mom_val >= 3.0:
                rev_bonus += 5.0
            elif mom_val < -15.0:
                rev_bonus -= 6.0

        score += rev_bonus

    # C. 獲利品質評估 (EPS / 毛利)
    earn_bonus = 0.0
    if earnings:
        if '創歷史新高' in earnings or '歷史新高' in earnings:
            earn_bonus += 14.0
        eps_m = re.search(r'EPS\s*(\d+(?:\.\d+)?)', earnings, re.IGNORECASE)
        if eps_m:
            eps_val = float(eps_m.group(1))
            if eps_val >= 10.0:
                earn_bonus += 12.0
            elif eps_val >= 3.0:
                earn_bonus += 8.0
            elif eps_val >= 1.0:
                earn_bonus += 5.0
        score += earn_bonus

    # D. 法說會與實質利多 (Catalysts)
    cat_bonus = 0.0
    if catalyst:
        strong_kw = ['大單', '滿載', '擴產', '倍增', '新廠', '三位數', '強勁', '優於預期', '能見度']
        hit_count = sum(1 for kw in strong_kw if kw in catalyst)
        if hit_count >= 2:
            cat_bonus += 18.0
        elif hit_count >= 1:
            cat_bonus += 12.0
        else:
            cat_bonus += 6.0
        score += cat_bonus

    # E. 法人目標價潛在上檔空間 (Analyst Upside)
    target_bonus = 0.0
    if target_price_str:
        tp_nums = [float(x) for x in re.findall(r'(\d+(?:\.\d+)?)元', target_price_str)]
        if tp_nums:
            max_tp = max(tp_nums)
            upside_pct = (max_tp - price) / price * 100
            if upside_pct >= 30.0:
                target_bonus += 16.0
                reasons.append(f"🎯法人目標價溢價空間巨大(+{upside_pct:.0f}%)")
            elif upside_pct >= 15.0:
                target_bonus += 10.0
                reasons.append(f"🎯法人目標價具上檔空間(+{upside_pct:.0f}%)")
            elif upside_pct < -5.0:
                target_bonus -= 10.0
        score += target_bonus

    cand['holistic_score'] = round(score, 1)
    cand['holistic_reasons'] = reasons
    return cand

def generate_ai_evolution_log(top_picks, hot_sectors, as_of_date, api_key, model_label):
    if not api_key:
        return

    sec_summary = "\n".join([
        f"- 【{s.get('sector_name', '')}】(熱度: {s.get('heat_level', 4)}星): {s.get('catalysts', '')}"
        for s in hot_sectors[:5]
    ])

    stocks_summary = "\n".join([
        f"- {s['code']} {s['name']} ({s['category']}): 終極實戰評分 {s['holistic_score']}分, 收盤價 {s['price']:.2f}元, 今日漲跌 {s['today_pct']:+5.2f}%, 營收: {s.get('monthly_rev', '')}, 目標價: {s.get('target_price', '')}, 特徵: {'；'.join(s['holistic_reasons'][:2])}"
        for s in top_picks
    ])

    prompt = f"""【系統角色與職責】
你是一名管理百億台幣的多因子量化對沖基金資深投資總監（CIO）。
基準覆盤日期：{as_of_date}。
核心評估原則：【先搜尋產業面新聞與法說會，再對候選股全面調研，最後綜合所有資訊評估排定榜單。嚴格杜絕先有榜單才找新聞之後見之明！】

【核心覆盤背景與資料集】
1. 今日 Google Search 掃描之市場主流強勢產業風口：
{sec_summary}

2. 經量化模型篩選、基本面事實審計與全維度加權排定之【AI 實戰勝率榜】嚴選名單：
{stocks_summary}

【思考與推理步驟 (Chain-of-Thought Guidance)】
- Step 1: 檢視今日資金是真突破（伴隨實質業績與法說成長）還是高檔題材投機拉抬？
- Step 2: 逐檔標的審視選股邏輯，檢驗榜首標的是否具備「基本面爆發 (YoY/EPS) + 技術守穩 + 目標價溢價」三位一體之共振特徵？
- Step 3: 揭露潛在風險與動態校準建議，明確標示高檔乖離過大、隔日沖獲利了結或均線防守點位。

【輸出要求】
請直接輸出專業、冷靜、數據導向的繁體中文 Markdown 報告（嚴格禁止使用 ```markdown 代碼塊包裹全文，直接輸出內文）：
### 一、今日台股主流產業風口與資金焦點剖析
### 二、勝率榜核心個股 Top-Down 選股邏輯驗證
### 三、量化交易風控警示與進化校準方向
"""
    text, used_model = call_gemini_rest(prompt, api_key)
    if text:
        clean_text = text.strip()
        if clean_text.startswith("```markdown"):
            clean_text = clean_text[len("```markdown"):].strip()
        elif clean_text.startswith("```"):
            clean_text = clean_text[3:].strip()
        if clean_text.endswith("```"):
            clean_text = clean_text[:-3].strip()

        header = "# 📖 AI 量化實戰每日覆盤與自我進化日記\n\n> 累積實戰經驗、天天反思漏洞、動態校準因子，結合客觀事實與 AI 深度情報，打造實戰勝率最高之決策體系。\n\n"
        content = f"{header}## 📅 【實戰覆盤檢討書】— {as_of_date} 盤後深度覆盤（Gemini {model_label} 先產業後榜單全維度版）\n\n{clean_text}\n"
        EVOLUTION_LOG_MD.write_text(content, encoding='utf-8')
        print(f"📝 客觀覆盤日記已更新至：{EVOLUTION_LOG_MD.name}", flush=True)

def write_evolution_ranking_md(selected_list, hot_sectors, market_overview, as_of_date, model_label):
    count = len(selected_list)
    sec_title = f"## 👑 【AI 獨有實戰勝率榜】（嚴選 {count} 檔・{model_label} 先產業後個股全維度版）"
    sec_sub = f"> 實戰鐵律：起漲賺錢、勝率第一。**先搜尋市場主流產業風口與重大新聞，再依營收/獲利/法說/目標價全維度評估排定榜單**，寧缺毋濫！"

    sec_pills = " ｜ ".join([f"**{s.get('sector_name', '')}** ({s.get('catalysts', '')[:25]}...)" for s in hot_sectors[:4]])

    lines = [
        '# 👑 台股 AI 獨有實戰勝率榜 (AI Self-Evolving Master Watchlist)', '',
        f'> 資料截止日：{as_of_date}。驅動核心：{model_label}。體系核心：**先產業新聞與法說會掃描 ➔ 候選池全維度調研 ➔ 綜合加權排定榜單**。', '',
        '### 🌐 【今日盤面主流強勢產業風口】',
        f'> **市場資金焦點**：{market_overview}',
        f'> **核心焦點族群**：{sec_pills}', '',
        '---', '',
        sec_title,
        sec_sub, '',
        '| 排名 | 股票代號 | 股票名稱 | 類群 | 收盤價 | 今日漲跌 | 實戰評分 | 月盈年盈(營收) | 季報獲利(EPS) | 法說重點與實質利多 | 法人目標價 | 核心技術起漲特徵 |',
        '|:---:|:---:|:---|:---|---:|---:|---:|:---|:---|:---|:---|:---|'
    ]
    for i, r in enumerate(selected_list, 1):
        clean_reasons = []
        for reas in r.get('holistic_reasons', r.get('reasons', [])):
            c = re.sub(r'[💎⚠️]?【風報比[^】]*】', '', reas).strip()
            # 排除已獨立成欄的基本面或警示字串
            if any(c.startswith(k) for k in ['📊', '💰', '📢', '🎯', '🚨']):
                continue
            if c: clean_reasons.append(c)
        feat = " ； ".join(clean_reasons[:3]) or "多頭結構守穩"
        pct_str = f"{r['today_pct']:+5.2f}%"
        rev_str = r.get('monthly_rev') or '查核中'
        earn_str = r.get('earnings') or '查核中'
        cat_str = r.get('catalyst') or '依技術面強勢為主'
        target_str = r.get('target_price') or '法人評估中'
        display_score = r.get('holistic_score', r['score'])

        lines.append(f"| **{i}** | `{r['code']}` | **{r['name']}** | {r['category']} | {r['price']:.2f} | {pct_str} | **{display_score}** | {rev_str} | {earn_str} | {cat_str} | {target_str} | {feat} |")

    OUTPUT_EVO_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"📄 獨有勝率榜單已輸出至：{OUTPUT_EVO_MD.name}", flush=True)

def main():
    t_start = time.perf_counter()
    print("=" * 75, flush=True)
    print("👑 啟動 AI 獨有實戰勝率與自我進化引擎 (evolution_engine.py)", flush=True)
    print("📌 體系紀律：【先搜尋產業面新聞與法說會 ➔ 全面調研候選股 ➔ 綜合資訊評估排榜】", flush=True)
    print("=" * 75, flush=True)

    api_key = get_gemini_api_key()
    model_label = "Gemini 2.5 Flash"
    if api_key:
        masked_key = f"{api_key[:8]}...{api_key[-4:]}"
        print(f"🔑 成功載入 GEMINI_API_KEY ({masked_key})，啟用 Google Search 即時聯網查核 [{model_label}]", flush=True)
    else:
        print("⚠️ 未檢測到 GEMINI_API_KEY，將以本機量化指標漏斗模式運作。", flush=True)

    html_files = list(REPORTS_DIR.rglob("*.html"))
    if not html_files:
        print("❌ 未在 reports/ 找到任何 HTML 報告檔案。", flush=True)
        return

    infos = []
    for f in html_files:
        inf = parse_html_report(f)
        if inf:
            # 🤖 自動產業分類與語意本體庫判定 (AI 擔任規則制定者與裁判，自動精準收納)
            std_cat, sem_tags = classify_stock(inf['code'], inf['name'], inf.get('category'))
            inf['category'] = std_cat
            inf['semantic_tags'] = sem_tags
            infos.append(inf)

    unique_infos = {inf['code']: inf for inf in infos}
    # 自動抓取最新資料日期 (相容 YYYY-MM-DD 或 MM/DD)
    sample_dates = [inf.get('kline', [{}])[-1].get('date') for inf in infos if inf.get('kline')]
    valid_dates = [d for d in sample_dates if d]
    if valid_dates:
        latest_d = max(valid_dates)
        if "-" in latest_d:
            parts = latest_d.split("-")
            as_of_date = f"{parts[1]}/{parts[2]}" if len(parts) >= 3 else latest_d
        else:
            as_of_date = latest_d
    else:
        as_of_date = "09/04"

    # =========================================================================
    # 🌐 【步驟 1：先產業後個股】即時聯網搜尋當前台股主流強勢族群與法說會動態
    # =========================================================================
    print(f"\n🌐 [Step 1] 先行聯網掃描今日 ({as_of_date}) 台股核心強勢產業風口與法說焦點...", flush=True)
    overview, hot_sectors = fetch_market_hot_sectors(api_key, as_of_date) if api_key else ("", [])
    print(f"  📌 今日盤面資金主軸：{overview}", flush=True)
    print("  🔥 當前核心強勢族群：", ", ".join([s.get('sector_name', '') for s in hot_sectors[:5]]), flush=True)

    # =========================================================================
    # 🔍 【步驟 2：初篩技術候選池】雙軌漏斗模型 (攻擊動能軌 + 蓄勢回測守穩軌)
    # =========================================================================
    print("\n🔍 [Step 2] 構建雙軌候選池 (兼顧「短線攻擊動能」與「回測量縮起漲」)...", flush=True)
    candidates = []
    for c, inf in unique_infos.items():
        res = calculate_evolution_score(inf)
        if res:
            candidates.append(res)

    # ── 雙軌候選漏斗 (Dual-Track Candidate Funnel) ──
    # 軌道 1：攻擊動能軌 (Momentum Track) - 站在 5MA 之上且技術攻擊分前 8 檔
    momentum_pool = sorted(
        [r for r in candidates if r['above_5ma'] and r['score'] >= 75.0],
        key=lambda x: x['score'],
        reverse=True
    )[:8]

    # 軌道 2：蓄勢回測/量縮守穩起漲軌 (Consolidation & Dip Track) - 回測守穩、量縮整理或投信連續進駐 (前 8 檔)
    seen_codes = {r['code'] for r in momentum_pool}
    dip_candidates = [
        r for r in candidates 
        if r['code'] not in seen_codes and (
            any("回測" in reas or "守穩" in reas or "投信" in reas or "VCP" in reas for reas in r['reasons'])
            or (-2.0 <= r['bias_20'] <= 6.5)
        )
    ]
    dip_pool = sorted(dip_candidates, key=lambda x: x['score'], reverse=True)[:8]

    pre_audit_pool = momentum_pool + dip_pool
    if not pre_audit_pool:
        pre_audit_pool = sorted(candidates, key=lambda x: x['score'], reverse=True)[:12]

    print(f"  👉 進入全維度深度調研池：共 {len(pre_audit_pool)} 檔標的 (攻擊動能 {len(momentum_pool)} 檔 + 蓄勢守穩 {len(dip_pool)} 檔)", flush=True)

    # =========================================================================
    # 🤖 【步驟 3：個股基本面與新聞全維度調研】(在排定榜單前先調研完畢！)
    # =========================================================================
    print("\n🤖 [Step 3] 全面聯網調研候選池個股之月盈年盈、獲利EPS、法說會與法人目標價...", flush=True)
    audited_candidates = []
    actual_model_used = model_label
    if api_key:
        for cand in pre_audit_pool:
            print(f"  -> 🔍 聯網查核 {cand['code']} {cand['name']} ({cand['category']})...", flush=True)
            audit = audit_stock_with_gemini(cand, api_key, as_of_date)
            time.sleep(1.5)
            if audit:
                if audit.get('_used_model'):
                    actual_model_used = audit.get('_used_model')
                if audit.get('is_trap'):
                    print(f"  🚫 排除客觀風險股: {cand['code']} {cand['name']}", flush=True)
                    continue

                cand['monthly_rev'] = audit.get('monthly_rev', '').strip()
                cand['earnings'] = audit.get('earnings', '').strip()
                cand['catalyst'] = audit.get('catalyst', '').strip()
                cand['target_price'] = audit.get('target_price', '').strip()
                disp_desc = audit.get('disposition', '').strip()

                if disp_desc:
                    cand['reasons'] = [r for r in cand['reasons'] if "回測量縮" not in r]
                    cand['score'] -= 27.0
                    cand['reasons'].append(f"🚨{disp_desc}")

                audited_candidates.append(cand)
    else:
        audited_candidates = pre_audit_pool

    # =========================================================================
    # ⚖️ 【步驟 4：融合產業風口、基本面成長、法說與目標價之全維度多因子評估】
    # =========================================================================
    print("\n⚖️ [Step 4] 綜合產業風口、營收動能、法說焦點、法人目標價與技術面，執行全維度評估...", flush=True)
    evaluated_candidates = []
    for cand in audited_candidates:
        cand = evaluate_holistic_score(cand, hot_sectors)
        evaluated_candidates.append(cand)

    # =========================================================================
    # 👑 【步驟 5：排定最終名次，輸出 AI 獨有實戰勝率榜】
    # =========================================================================
    print("\n👑 [Step 5] 依據全維度綜合評估得分 (Holistic Score)，正式排定最終榜單名次...", flush=True)
    # 此刻才排定最終名次！
    final_qualified = sorted(evaluated_candidates, key=lambda x: x['holistic_score'], reverse=True)[:8]

    count = len(final_qualified)
    print(f"\n👑 【AI 獨有實戰勝率榜】（嚴選 {count} 檔・全維度綜合評估版）", flush=True)
    print(f"{'名次':<4} {'代號':<6} {'名稱':<8} {'類群':<8} {'收盤價':<9} {'今日漲跌':<10} {'月盈年盈營收':<22} {'終極實戰評分'}", flush=True)
    print("-" * 80, flush=True)
    for i, r in enumerate(final_qualified, 1):
        rev_brief = (r.get('monthly_rev', '')[:20] + '..') if len(r.get('monthly_rev', '')) > 20 else r.get('monthly_rev', '—')
        print(f"#{i:<3} {r['code']:<6} {r['name']:<8} {r['category']:<8} {r['price']:<9.2f} {r['today_pct']:+6.2f}%    {rev_brief:<22} {r['holistic_score']}")

    write_evolution_ranking_md(final_qualified, hot_sectors, overview, as_of_date, actual_model_used)
    generate_ai_evolution_log(final_qualified, hot_sectors, as_of_date, api_key, actual_model_used)

    try:
        import export_mobile_site
        export_mobile_site.export_four_rankings()
        print("📱 已自動同步更新手機版與看板資料庫 (rankings.json)。", flush=True)
    except Exception:
        pass

    t_cost = time.perf_counter() - t_start
    print(f"\n⏱️ 運算、審核與覆盤總耗時：{t_cost:.2f} 秒", flush=True)
    print("=" * 75, flush=True)

if __name__ == "__main__":
    main()

