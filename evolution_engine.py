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

    prompt = """你是一名台股頂級操盤手與產業研究總監。
請透過 Google Search 即時查核台股當前盤面最新最熱門的「核心強勢產業族群、熱門法說會動態與主流資金焦點」：
請找出目前市場資金最青睞、最熱門的 5 至 8 個核心產業題材（例如：AI伺服器/代工、先進封裝CoWoS/測試設備、散熱水冷、光通訊CPO/矽光子、ASIC/IP設計、工業電腦Edge AI、高階被動元件、電源供應等）。

請以繁體中文直接輸出標準 JSON 格式（包含 ```json 代碼區塊），結構如下：
{
  "market_overview": "一句話總結今日台股市場焦點與資金主軸",
  "hot_sectors": [
    {
      "sector_name": "產業族群名稱",
      "heat_level": 5, 
      "catalysts": "最新重大法說會重點、關鍵大訂單或產業爆發動能",
      "related_tags": ["相關標籤或次產業1", "次產業2"]
    }
  ]
}
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
你是一名極度講求客觀數字的台股專業研究員與頂級操盤手。
請透過 Google 搜尋即時查核台股「{candidate['code']} {candidate['name']}」（類別：{candidate['category']}）最新公開之財務、法說會、重大利多與法人機構目標價：

【請嚴格搜尋並以下列五行格式精確輸出，禁止任何引言、客套或多餘文字】：
月盈年盈：[何月份單月營收數字、MoM月增率、YoY年增率，例: 7月營收193.42億元(MoM+8.93%, YoY-0.02%)]
最新獲利：[最新一季財報EPS、毛利率與累計獲利，例: Q2 EPS 3.55元，毛利率16.0%]
法說利多：[最新法說會核心要點、關鍵大訂單或擴產動能，例: AI伺服器營收預計年增50-100%，新廠2026年中投產]
法人目標價：[外資、投信或國內大型券商最新研究報告之目標價與評等，例: 外資共識目標價155元，最高目標價210元(買進)]
處置狀態：[正常交易 或 列入處置分盤撮合]
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
                if '列入處置' in clean or '處置股' in clean:
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

    # A. 產業風口契合度評估
    matched_sector = None
    for sec in hot_sectors:
        sec_name = sec.get('sector_name', '')
        tags = sec.get('related_tags', [])
        if any(t in category or t in name for t in [sec_name] + tags):
            matched_sector = sec
            break

    if matched_sector:
        heat = matched_sector.get('heat_level', 4)
        sec_bonus = 22.0 if heat >= 5 else 16.0
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

    prompt = f"""
你是一名注重客觀事實與科學紀律的資深交易總監。
基準日期：{as_of_date}。
核心評估原則：【先搜尋產業面新聞與法說會，再對候選股全面調研，最後綜合所有資訊評估排定榜單。嚴格杜絕先有榜單才找新聞之後見之明！】

今日 Google Search 掃描之市場主流強勢產業風口：
{sec_summary}

依據全維度多因子（產業風口 + 月盈年盈營收 + 獲利EPS + 法說重點 + 法人目標價 + 技術籌碼）最終嚴選出的勝率榜標的：
{stocks_summary}

請為實戰覆盤日記 (evolution_log.md) 撰寫專業客觀的檢討報告（繁體中文 Markdown）：
包含：
1. 【今日台股主流產業風口與資金焦點】：以 Step 1 搜尋出的產業新聞與法說會動態為核心，分析資金為何集中於此。
2. 【自上而下 (Top-Down) 嚴選邏輯檢討】：說明為何榜單個股能從產業風口中脫穎而出，並同時具備營收年增、季報獲利與法人目標價保護。
3. 【演算法動態進化原則】：強調先產業新聞、再基本面調研、最後全維度綜合排名的科學性，杜絕主觀與粗暴盲選。

請直接輸出 Markdown 內文，數據嚴格精確，禁止誇大渲染。
"""
    text, used_model = call_gemini_rest(prompt, api_key)
    if text:
        header = "# 📖 AI 量化實戰每日覆盤與自我進化日記\n\n> 累積實戰經驗、天天反思漏洞、動態校準因子，結合客觀事實與 AI 深度情報，打造實戰勝率最高之決策體系。\n\n"
        content = f"{header}## 📅 【實戰覆盤檢討書】— {as_of_date} 盤後深度覆盤（Gemini {model_label} 先產業後榜單全維度版）\n\n{text.strip()}\n"
        EVOLUTION_LOG_MD.write_text(content, encoding='utf-8')
        print(f"📝 客觀覆盤日記已更新至：{EVOLUTION_LOG_MD.name}", flush=True)

def write_evolution_ranking_md(selected_list, defensive_list, hot_sectors, market_overview, as_of_date, model_label):
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

    lines.extend([
        '', '---', '',
        '## 🛡️ 【穩健防守輔助序列】',
        '> 供大盤重度拉回時搭配參考之超低乖離防守池。', '',
        '| 排名 | 股票代號 | 股票名稱 | 類群 | 收盤價 | 今日漲跌 | 穩健評分 | 核心防守特徵 |',
        '|:---:|:---:|:---|:---|---:|---:|---:|:---|'
    ])
    for i, r in enumerate(defensive_list, 1):
        pct_str = f"{r['today_pct']:+5.2f}%"
        feat = f"防守點{r['stop_loss']}元；月乖離+{r['bias_20']}%"
        lines.append(f"| **{i}** | `{r['code']}` | **{r['name']}** | {r['category']} | {r['price']:.2f} | {pct_str} | **{r['score']}** | {feat} |")

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
            infos.append(inf)

    unique_infos = {inf['code']: inf for inf in infos}
    as_of_date = "09/03"

    # =========================================================================
    # 🌐 【步驟 1：先產業後個股】即時聯網搜尋當前台股主流強勢族群與法說會動態
    # =========================================================================
    print("\n🌐 [Step 1] 先行聯網掃描今日台股核心強勢產業風口與法說焦點...", flush=True)
    overview, hot_sectors = fetch_market_hot_sectors(api_key, as_of_date) if api_key else ("", [])
    print(f"  📌 今日盤面資金主軸：{overview}", flush=True)
    print("  🔥 當前核心強勢族群：", ", ".join([s.get('sector_name', '') for s in hot_sectors[:5]]), flush=True)

    # =========================================================================
    # 🔍 【步驟 2：初篩技術候選池】選出具備短線起漲基礎之潛力股票群
    # =========================================================================
    print("\n🔍 [Step 2] 構建具備短線起漲條件之候選潛力池 (排除破5MA與過熱股)...", flush=True)
    candidates = []
    for c, inf in unique_infos.items():
        res = calculate_evolution_score(inf)
        if res:
            candidates.append(res)

    # 基礎門檻：未跌破 5MA，短線多頭結構健全
    momentum_candidates = [r for r in candidates if r['above_5ma'] and r['score'] >= 80.0]
    # 先依基礎技術分取前 12 檔進入「深度調研池」
    pre_audit_pool = sorted(momentum_candidates, key=lambda x: x['score'], reverse=True)[:12]
    if not pre_audit_pool:
        pre_audit_pool = sorted([r for r in candidates if r['above_5ma']], key=lambda x: x['score'], reverse=True)[:8]

    print(f"  👉 進入全維度深度調研池：共 {len(pre_audit_pool)} 檔標的", flush=True)

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

    # 穩健防守池
    defensive_pool = sorted(candidates, key=lambda x: (abs(x['bias_20'] - 3.0), -x['score']))[:6]

    count = len(final_qualified)
    print(f"\n👑 【AI 獨有實戰勝率榜】（嚴選 {count} 檔・全維度綜合評估版）", flush=True)
    print(f"{'名次':<4} {'代號':<6} {'名稱':<8} {'類群':<8} {'收盤價':<9} {'今日漲跌':<10} {'月盈年盈營收':<22} {'終極實戰評分'}", flush=True)
    print("-" * 80, flush=True)
    for i, r in enumerate(final_qualified, 1):
        rev_brief = (r.get('monthly_rev', '')[:20] + '..') if len(r.get('monthly_rev', '')) > 20 else r.get('monthly_rev', '—')
        print(f"#{i:<3} {r['code']:<6} {r['name']:<8} {r['category']:<8} {r['price']:<9.2f} {r['today_pct']:+6.2f}%    {rev_brief:<22} {r['holistic_score']}")

    write_evolution_ranking_md(final_qualified, defensive_pool, hot_sectors, overview, as_of_date, actual_model_used)
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

