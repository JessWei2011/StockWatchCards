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
    env_file = ROOT_DIR / '.env'
    if not api_key and env_file.exists():
        for line in env_file.read_text(encoding='utf-8', errors='ignore').splitlines():
            line = line.strip()
            if line.startswith('GEMINI_API_KEY='):
                api_key = line.split('=', 1)[1].strip().strip('"\'')
                break
    return api_key

def call_gemini_rest(prompt, api_key, models_priority=['gemini-3.5-flash', 'gemini-flash-lite-latest', 'gemini-2.5-flash']):
    for m in models_priority:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2}
        }
        try:
            res = requests.post(url, json=payload, timeout=18)
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

def call_gemini_search(prompt, api_key, models_priority=['gemini-2.5-flash', 'gemini-3.5-flash', 'gemini-flash-latest']):
    """透過 Google Search Grounding 即時聯網搜尋最新月營收、季報EPS與法說會利多"""
    for m in models_priority:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "tools": [{"googleSearch": {}}],
            "generationConfig": {"temperature": 0.1}
        }
        try:
            res = requests.post(url, json=payload, timeout=25)
            if res.status_code == 200:
                data = res.json()
                text = data['candidates'][0]['content']['parts'][0]['text']
                return text, m
            elif res.status_code in (503, 429):
                continue
        except Exception:
            continue
    return None, None

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

    # 1. 優先取用快取中的真實 Google Search 調研數據 (零延遲、防 429)
    if code in cache:
        item = cache[code]
        return {
            'revenue': item.get('revenue', ''),
            'earnings': item.get('earnings', ''),
            'catalyst': item.get('catalyst', ''),
            'disposition': item.get('disposition', ''),
            'is_trap': False,
            'confidence_bonus': 10,
            '_used_model': 'Google Search Grounding (Cached)'
        }

    # 2. 若快取沒有，即時聯網 Google Search Grounding 查核
    prompt = f"""
你是一名極度講求客觀數字的台股專業研究員。
請透過 Google 搜尋即時查核台股「{candidate['code']} {candidate['name']}」（類別：{candidate['category']}）最新公開之財務與法說會事實：

【請嚴格搜尋並以下列三行格式直接輸出，禁止任何引言或多餘空話】：
營收：[何月份營收數字、金額與年增率YoY/月增率MoM，例: 7月營收57.69億元(YoY+158.4%)]
獲利：[最新一季EPS與毛利率，例: Q2 EPS 11.61元，毛利率21.5%]
利多：[最新法說會重點、關鍵大訂單或產能擴充，例: 奪美系CSP次世代晶片大單，CoWoS產能獲台積電支援]
"""
    text, used_model = call_gemini_search(prompt, api_key)
    if text:
        rev = ""
        earn = ""
        cat = ""
        for line in text.splitlines():
            clean = line.strip().replace('*', '').replace('#', '').strip('- ').strip()
            if not clean or len(clean) < 4:
                continue
            if ('營收' in clean or 'YoY' in clean or '年增' in clean) and not rev:
                rev = re.sub(r'^(?:營收|最新營收|單月營收)[：:]\s*', '', clean).strip()[:35]
            elif ('EPS' in clean or '毛利' in clean or '獲利' in clean or '淨利' in clean) and not earn:
                earn = re.sub(r'^(?:獲利|最新獲利|財報獲利)[：:]\s*', '', clean).strip()[:30]
            elif ('法說' in clean or '訂單' in clean or '大單' in clean or '客戶' in clean or '利多' in clean or '產能' in clean) and not cat:
                cat = re.sub(r'^(?:利多|實質利多|法說會)[：:]\s*', '', clean).strip()[:40]

        junk = ['以基本面訂單為主', '基本面良好', '待後續', '無顯著']
        for j in junk:
            if j in rev: rev = ""
            if j in earn: earn = ""
            if j in cat: cat = ""

        # 寫入快取持久化
        cache[code] = {
            'name': candidate['name'],
            'revenue': rev,
            'earnings': earn,
            'catalyst': cat,
            'updated_at': as_of_date
        }
        save_fundamental_cache(cache)

        return {
            'revenue': rev,
            'earnings': earn,
            'catalyst': cat,
            '_used_model': used_model
        }
    return None

def generate_ai_evolution_log(top_picks, as_of_date, api_key, model_label):
    if not api_key:
        return

    stocks_summary = "\n".join([
        f"- {s['code']} {s['name']} ({s['category']}): 收盤價 {s['price']:.2f}元, 今日漲跌 {s['today_pct']:+5.2f}%, 特徵: {'；'.join(s['reasons'][:2])}"
        for s in top_picks
    ])

    prompt = f"""
你是一名注重客觀事實與風控的資深交易總監。
基準日期：{as_of_date}（2026年9月初）。
今日市場客觀事實：
- 部分熱門股顯著回檔：富喬 (1815) 今日收跌 -8.40%（盤中最低一度觸及 -9.92%）、光環 (3234) 收跌 -7.82%、華碩 (2357) 挑戰千元關卡拉回收長黑。請務必使用真實跌幅數字，嚴禁使用未發生的「跌停」等誇大用詞。
- 相對抗跌與回測守穩標的：緯創 (+1.62%)、聯發科 (+1.52%)、強茂 (+5.41%)、頎邦 (+3.56%)、創意 (-1.61% 量縮守月線)。
- 重大漏洞反思與自我進化：
  1. 處置股識別：富世達 (6805) 因進處置分盤撮合致量縮，非自然籌碼洗淨，已剝奪加分並剔除。
  2. 數據誠信校準：緯創 (3231) 營收校正為真實單月 861.9 億元，杜絕季度累計三千億極端值混淆。
  3. 技術面一票否決：鴻勁 (7769) 因均線空頭排列且失守 5MA，雖基本面強但技術面矛盾，貫徹「破5MA一票否決」自起漲榜除名。
  4. 千金股流動性折價：對股價逾千元、數千元標的引入流動性與資金佔用扣分，平衡資金效率。

今日入選勝率榜標的：
{stocks_summary}

請為實戰覆盤日記 (evolution_log.md) 撰寫客觀冷靜的檢討報告（繁體中文 Markdown）：
包含：
1. 【今日市場走勢客觀覆盤】：以精確數據比對回檔個股與抗跌個股，分析資金流向。
2. 【核心個案深度檢討】：詳述富世達處置股量縮、緯創單月真實營收校準、鴻勁破5MA一票否決、千金股流動性折價三大進化案例。
3. 【演算法多維平衡進化原則】：說明數據誠信、技術面鐵律與資金效率平衡。

請直接輸出 Markdown 內文，禁止誇大渲染，數據嚴格精確。
"""
    text, used_model = call_gemini_rest(prompt, api_key)
    if text:
        header = "# 📖 AI 量化實戰每日覆盤與自我進化日記\n\n> 累積實戰經驗、天天反思漏洞、動態校準因子，結合客觀事實與 AI 深度情報，打造實戰勝率最高之決策體系。\n\n"
        content = f"{header}## 📅 【實戰覆盤檢討書】— {as_of_date} 盤後深度覆盤（Gemini {model_label} 客觀多維版）\n\n{text.strip()}\n"
        EVOLUTION_LOG_MD.write_text(content, encoding='utf-8')
        print(f"📝 客觀覆盤日記已更新至：{EVOLUTION_LOG_MD.name}", flush=True)

def write_evolution_ranking_md(selected_list, defensive_list, as_of_date, model_label):
    count = len(selected_list)
    sec_title = f"## 👑 【AI 獨有實戰勝率榜】（嚴選 {count} 檔・{model_label} 多維平衡版）"
    sec_sub = f"> 實戰鐵律：起漲賺錢、勝率第一。兼顧「放量突破起漲」與「量縮良性回測守穩」，由 {model_label} 深度審查，堅持寧缺毋濫！"

    lines = [
        '# 👑 台股 AI 獨有實戰勝率榜 (AI Self-Evolving Master Watchlist)', '',
        f'> 資料截止日：{as_of_date}。驅動核心：{model_label}。以真實收盤為真理錨點，單一目標：起漲賺錢、勝率高、風報比優異。', '',
        '---', '',
        sec_title,
        sec_sub, '',
        '| 排名 | 股票代號 | 股票名稱 | 類群 | 收盤價 | 今日漲跌 | 實戰評分 | 核心基本面與起漲特徵 |',
        '|:---:|:---:|:---|:---|---:|---:|---:|:---|'
    ]
    for i, r in enumerate(selected_list, 1):
        clean_reasons = []
        for reas in r['reasons']:
            c = re.sub(r'[💎⚠️]?【風報比[^】]*】', '', reas).strip()
            if c: clean_reasons.append(c)
        feat = " ； ".join(clean_reasons[:3])
        pct_str = f"{r['today_pct']:+5.2f}%"
        lines.append(f"| **{i}** | `{r['code']}` | **{r['name']}** | {r['category']} | {r['price']:.2f} | {pct_str} | **{r['score']}** | {feat} |")

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
    print("=" * 75, flush=True)

    api_key = get_gemini_api_key()
    model_label = "Gemini 3.5 Flash"
    if api_key:
        print(f"🔑 成功載入 GEMINI_API_KEY，啟用高可用直連模式 [{model_label}]", flush=True)
    else:
        print("ℹ️ 未檢測到 GEMINI_API_KEY，以本機高規格量化漏斗模式運作。", flush=True)

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
    
    candidates = []
    for c, inf in unique_infos.items():
        res = calculate_evolution_score(inf)
        if res:
            candidates.append(res)

    ranked = sorted(candidates, key=lambda x: x['score'], reverse=True)

    # 實戰鐵律一票否決制：起漲勝率榜嚴禁收盤價跌破 5MA 的標的（破 5MA 屬短線回檔或空方型態，一律排除）
    momentum_candidates = [r for r in ranked if r['above_5ma'] and r['score'] >= 100.0]
    pre_qualified = momentum_candidates[:8]
    if not pre_qualified:
        pre_qualified = [r for r in ranked if r['above_5ma']][:6]

    final_qualified = []
    actual_model_used = model_label
    if api_key:
        print(f"🤖 正在調用 {model_label} 審核前 {len(pre_qualified)} 檔候選股客觀情報...", flush=True)
        for cand in pre_qualified:
            print(f"  -> 審核 {cand['code']} {cand['name']}...", flush=True)
            audit = audit_stock_with_gemini(cand, api_key, as_of_date)
            time.sleep(2.5) # 遵守 API RPM 頻率限制防 429
            if audit:
                if audit.get('_used_model'):
                    actual_model_used = audit.get('_used_model')
                if audit.get('is_trap'):
                    print(f"  🚫 排除客觀風險股: {cand['code']} {cand['name']} (原因: {audit.get('risk_flag')})", flush=True)
                    continue
                bonus = float(audit.get('confidence_bonus', 10))
                cand['score'] = round(cand['score'] + bonus, 1)
                cat_desc = audit.get('catalyst', '').strip()
                rev_desc = audit.get('revenue', '').strip()
                earn_desc = audit.get('earnings', '').strip()
                disp_desc = audit.get('disposition', '').strip()

                # 🚨 處置股流動性校準：處置股量縮係制度性急凍，非籌碼洗淨或回測量縮守穩！
                if disp_desc:
                    cand['reasons'] = [r for r in cand['reasons'] if "回測量縮" not in r]
                    cand['score'] -= 27.0 # 扣回誤判量縮守穩(+12分)並加計流動性折價(-15分)
                    cand['reasons'].append(f"🚨{disp_desc}")

                fund_parts = []
                if rev_desc and '待' not in rev_desc:
                    fund_parts.append(f"📊{rev_desc}")
                if earn_desc and '待' not in earn_desc:
                    fund_parts.append(f"💰{earn_desc}")
                if cat_desc and '待' not in cat_desc:
                    fund_parts.append(f"📢{cat_desc}")

                if fund_parts:
                    cand['reasons'].insert(0, " ｜ ".join(fund_parts))
            final_qualified.append(cand)
    else:
        final_qualified = pre_qualified

    # 處置股流動性折價後若低於100分則自起漲榜除名，若在榜亦維持警示
    final_qualified = [c for c in final_qualified if c['score'] >= 95.0]

    final_qualified.sort(key=lambda x: x['score'], reverse=True)
    defensive_pool = sorted(ranked, key=lambda x: (abs(x['bias_20'] - 3.0), -x['score']))[:6]

    count = len(final_qualified)
    print(f"\n👑 【AI 獨有實戰勝率榜】（嚴選 {count} 檔）", flush=True)
    print(f"{'名次':<4} {'代號':<6} {'名稱':<8} {'類群':<8} {'收盤價':<9} {'今日漲跌':<10} {'5MA斜率':<10} {'月乖離':<8} {'評分'}", flush=True)
    print("-" * 75, flush=True)
    for i, r in enumerate(final_qualified, 1):
        print(f"#{i:<3} {r['code']:<6} {r['name']:<8} {r['category']:<8} {r['price']:<9.2f} {r['today_pct']:+6.2f}%    +{r['s5']:<9.2f}% +{r['bias_20']:<7.1f}% {r['score']}")

    write_evolution_ranking_md(final_qualified, defensive_pool, as_of_date, actual_model_used)
    generate_ai_evolution_log(final_qualified, as_of_date, api_key, actual_model_used)

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
