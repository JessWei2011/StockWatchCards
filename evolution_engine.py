#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
========================================================================================
🏆 AI 獨有實戰勝率與自我進化引擎 (evolution_engine.py) - 旗艦多維平衡版
----------------------------------------------------------------------------------------
【實事求是與科學量化核心】：
1. 處置股統一扣分但仍可入榜；月乖離過熱或過弱保留排除規則。
2. 漸進式綜合評分矩陣：
   - 攻守兼備：同時納入「放量突破起漲」與「量縮良性回測月線守穩」。
   - 開高走低長黑採平滑扣分制，絕不因單日震盪盲目錯殺優質回測買點。
3. 風報比（R/R）：個股前醒目標註，不作死門檻硬剔除，由操盤手決策。
4. 驅動核心：直連 Google REST API (Gemini 3.8 Flash / 備援架構)，秒級情報審查。
========================================================================================
"""

import sys
import os
import re
import json
import time
import hashlib
from datetime import date, datetime
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
CHATGPT_RANKING_MD = ROOT_DIR / "stock_winrate_ranking.md"
GEMINI_RANKING_MD = ROOT_DIR / "stock_winrate_ranking_gemini.md"
EVOLUTION_LOG_MD = ROOT_DIR / "evolution_log.md"
RANKING_HISTORY_DIR = ROOT_DIR / "ranking_history"
LLM_HANDOFF_FILE = ROOT_DIR / "llm_manual_handoff.json"

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

GEMINI_MODELS_PRIORITY = ('gemini-3.8-flash', 'gemini-3.7-flash', 'gemini-3.5-flash-lite')


class ManualLLMResponseRequired(RuntimeError):
    pass


def _manual_prompt_id(prompt):
    return hashlib.sha256(prompt.encode('utf-8')).hexdigest()[:16]


def consume_manual_llm_response(prompt):
    if not LLM_HANDOFF_FILE.exists():
        return None
    try:
        item = json.loads(LLM_HANDOFF_FILE.read_text(encoding='utf-8'))
    except Exception:
        return None
    prompt_id = _manual_prompt_id(prompt)
    if item.get('prompt_id') != prompt_id or item.get('status') != 'ready':
        return None
    response = str(item.get('response') or '').strip()
    if not response:
        return None
    item['status'] = 'consumed'
    item['consumed_at'] = datetime.now().isoformat(timespec='seconds')
    LLM_HANDOFF_FILE.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"📥 已載入 Gemini 網頁版人工回覆 [{prompt_id}]", flush=True)
    return response


def request_manual_llm_response(prompt):
    prompt_id = _manual_prompt_id(prompt)
    is_sector_prompt = '"hot_sectors"' in prompt and '"market_overview"' in prompt
    kind = '產業風口查核' if is_sector_prompt else '候選股批次查核'
    item = {
        'status': 'awaiting_response', 'prompt_id': prompt_id, 'kind': kind,
        'step': 1 if is_sector_prompt else 2, 'total_steps': 2,
        'prompt': prompt, 'response': '',
        'created_at': datetime.now().isoformat(timespec='seconds'),
    }
    if not is_sector_prompt:
        # 供網頁端核對 Gemini 是否逐檔回覆，避免漏答仍被當成完成。
        item['expected_codes'] = list(dict.fromkeys(
            re.findall(r'"code"\s*:\s*"(\d{4,6})"', prompt)
        ))
    LLM_HANDOFF_FILE.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"📝 MANUAL_HANDOFF_REQUIRED：{kind}；請在網頁複製 Prompt，貼回 JSON 回覆 [{prompt_id}]", flush=True)
    raise ManualLLMResponseRequired(prompt_id)


def gemini_error_summary(response):
    """擷取 Google 可公開的錯誤原因，不把 API key 或完整請求網址寫入紀錄。"""
    try:
        error = response.json().get('error', {})
        message = str(error.get('message') or '').strip()
        details_text = json.dumps(error.get('details') or [], ensure_ascii=False)
        retry = re.search(r'"retryDelay"\s*:\s*"([^"]+)"', details_text)
        metric = re.search(r'"quotaMetric"\s*:\s*"([^"]+)"', details_text)
        parts = [message[:260]] if message else []
        if metric:
            parts.append(f"quota={metric.group(1).rsplit('/', 1)[-1]}")
        if retry:
            parts.append(f"建議等待={retry.group(1)}")
        return '；'.join(parts) or f'HTTP {response.status_code}'
    except Exception:
        return f'HTTP {getattr(response, "status_code", "未知")}'


def call_gemini_rest(prompt, api_key, models_priority=GEMINI_MODELS_PRIORITY):
    for m in models_priority:
        print(f'⏳ LLM 覆盤：等待 {m} 回覆（逾時後嘗試備援）', flush=True)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"thinkingConfig": {"thinkingLevel": "low"}}
        }
        try:
            res = requests.post(url, json=payload, timeout=20)
            if res.status_code == 200:
                data = res.json()
                text = data['candidates'][0]['content']['parts'][0]['text']
                return text, m
            elif res.status_code in (503, 429):
                print(f"      [Gemini {m}] HTTP {res.status_code}: {gemini_error_summary(res)}", flush=True)
                continue
        except Exception:
            continue
    return None, None

def apply_disposition_penalty(candidate, description):
    """所有來源共用處置扣分；重複查核不重複扣分，正常交易可撤銷舊標記。"""
    candidate.setdefault('score_before_disposition', candidate['score'])
    candidate.setdefault('reasons_before_disposition', list(candidate['reasons']))
    candidate['score'] = candidate['score_before_disposition']
    candidate['reasons'] = list(candidate['reasons_before_disposition'])
    candidate['disposition'] = description
    if description:
        candidate['score'] -= 27.0
        # 分盤造成的量縮不能取得自然回測加分。
        if any('回測量縮守穩' in r for r in candidate['reasons']):
            candidate['score'] -= 12.0
        candidate['reasons'] = [r for r in candidate['reasons'] if '回測量縮守穩' not in r]
        candidate['reasons'].append(f'🚨{description}（處置扣27分）')
    return candidate


def report_session_date(value, generated_at):
    """以報表產生日期補足無年份的 K 線日期，避免跨年誤用快取。"""
    value = str(value).strip()
    if re.fullmatch(r'\d{4}[-/]\d{2}[-/]\d{2}', value):
        return date.fromisoformat(value.replace('/', '-')).isoformat()
    anchor = date.fromisoformat(generated_at)
    month, day = map(int, value.split('/'))
    session = date(anchor.year, month, day)
    if session > anchor:
        session = date(anchor.year - 1, month, day)
    return session.isoformat()


def select_qualified_candidates(candidates, threshold=180.0, limit=10):
    return sorted((c for c in candidates if c['holistic_score'] >= threshold),
                  key=lambda c: c['holistic_score'], reverse=True)[:limit]


def select_actionable_candidates(candidates, threshold=105.0, limit=10, watch_limit=5):
    """正式起漲優先；不足時補充尚待觸發的觀察候選，避免空榜卻不冒充買進訊號。"""
    active_stages = {'剛突破起漲', '突破後量縮回測', '壓縮蓄勢待突破'}
    active = sorted((c for c in candidates
                     if c.get('setup_stage') in active_stages and c['holistic_score'] >= threshold),
                    key=lambda c: c['holistic_score'], reverse=True)
    for candidate in active:
        candidate['selection_tier'] = '正式起漲候選'
    remaining = max(0, min(watch_limit, limit - len(active)))
    watch = sorted((c for c in candidates
                    if c.get('setup_stage') == '趨勢中段／訊號未明'
                    and c.get('score', 0) >= 70
                    and -8.0 <= c.get('pivot_distance', -99) <= 2.0
                    and c.get('today_pct', 99) < 7.0 and c.get('ret5', 99) < 13.0),
                   key=lambda c: (c.get('holistic_score', 0),
                                  c.get('pre_audit_score', c.get('score', 0))), reverse=True)[:remaining]
    for candidate in watch:
        candidate['selection_tier'] = '觀察候選（尚待突破觸發）'
    return (active + watch)[:limit]


def apply_universe_relative_strength(candidates):
    """以 reports 股票池的 3/5 日報酬分位衡量相對強度，不再把單純上漲稱為逆勢。"""
    if not candidates:
        return candidates
    frame = pd.DataFrame([{'ret3': c['ret3'], 'ret5': c['ret5']} for c in candidates])
    percentiles = (frame.rank(pct=True, method='average')['ret3']
                   + frame.rank(pct=True, method='average')['ret5']) / 2
    for candidate, percentile in zip(candidates, percentiles):
        candidate['universe_rs_percentile'] = round(float(percentile) * 100, 1)
        delta = 10.0 if percentile >= 0.80 else 6.0 if percentile >= 0.60 else -6.0 if percentile <= 0.20 else 0.0
        reason = f"📊股票池相對強度PR{percentile*100:.0f}({delta:+.0f}分)"
        if 'score_before_disposition' in candidate:
            candidate['score_before_disposition'] += delta
            candidate['reasons_before_disposition'].append(reason)
            apply_disposition_penalty(candidate, candidate.get('disposition', ''))
        else:
            candidate['score'] += delta
            candidate['reasons'].append(reason)
        candidate['score'] = round(candidate['score'], 1)
    return candidates


def write_ranking_snapshot(as_of_date, all_candidates, selected, hot_sectors):
    """保存每次訊號與分項資料，供未來 3/5 交易日驗證，避免事後改寫歷史。"""
    RANKING_HISTORY_DIR.mkdir(exist_ok=True)
    generated_at = datetime.now().astimezone().isoformat(timespec='seconds')
    stamp = datetime.now().strftime('%H%M%S')
    payload = {
        'schema_version': 1, 'as_of_date': as_of_date, 'generated_at': generated_at,
        'holding_horizon': '3-5 trading days', 'entry_assumption': 'next trading day open',
        'hot_sectors': hot_sectors, 'selected_codes': [c['code'] for c in selected],
        'candidates': all_candidates,
    }
    path = RANKING_HISTORY_DIR / f'{as_of_date}_{stamp}.json'
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    try:
        display_path = path.relative_to(ROOT_DIR)
    except ValueError:
        display_path = path
    print(f'🧾 評分證據快照已保存：{display_path}', flush=True)
    return path


def parse_json_object(text):
    if not text:
        return None
    match = re.search(r'```(?:json)?\s*(\{.*\})\s*```', text, re.DOTALL)
    raw = match.group(1) if match else text.strip()
    raw = re.sub(r',\s*([}\]])', r'\1', raw)
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except (TypeError, json.JSONDecodeError):
        return None


def evidence_is_recent(item, as_of_date, max_age_days=30):
    """只有截止日前、具來源網址且在有效期內的事件可影響分數。"""
    try:
        event_date = date.fromisoformat(str(item.get('event_date', '')))
        cutoff = date.fromisoformat(as_of_date)
    except (TypeError, ValueError):
        return False
    url = str(item.get('source_url', ''))
    return url.startswith(('https://', 'http://')) and 0 <= (cutoff - event_date).days <= max_age_days


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
    if bias_20 > 22.0:
        return None
    if bias_20 < -18.0:
        return None

    # ── 3~5 交易日起漲評分：先判斷階段，再評估趨勢、量價與籌碼 ──
    score = 50.0
    reasons = []
    ret3 = (price / float(close_s.iloc[-4]) - 1) * 100 if n >= 4 else 0.0
    ret5 = (price / float(close_s.iloc[-6]) - 1) * 100 if n >= 6 else 0.0
    ret10 = (price / float(close_s.iloc[-11]) - 1) * 100 if n >= 11 else 0.0
    prior20_high = float(high_s.iloc[-21:-1].max()) if n >= 21 else float(high_s.iloc[:-1].max())
    prev_prior20_high = float(high_s.iloc[-22:-2].max()) if n >= 22 else prior20_high
    pivot_distance = (price / prior20_high - 1) * 100 if prior20_high else 0.0
    prev_pct = (prev_close / float(close_s.iloc[-3]) - 1) * 100 if n >= 3 else 0.0
    tr = pd.concat([(high_s-low_s), (high_s-close_s.shift()).abs(), (low_s-close_s.shift()).abs()], axis=1).max(axis=1)
    atr14 = float(tr.rolling(14).mean().iloc[-1])
    atr_pct = atr14 / price * 100 if price else 0.0
    range5 = (float(high_s.iloc[-5:].max()) / max(float(low_s.iloc[-5:].min()), 1e-9)-1)*100
    range10 = (float(high_s.iloc[-10:].max()) / max(float(low_s.iloc[-10:].min()), 1e-9)-1)*100

    extended = today_pct >= 7.0 or ret5 >= 13.0 or bias_20 >= 11.0 or pivot_distance >= 8.0
    repair_bounce = prev_pct <= -3.0 and today_pct >= 2.0 and price <= float(high_s.iloc[-2])
    fresh_breakout = (price > prior20_high and prev_close <= prev_prior20_high
                      and 0.3 <= today_pct <= 6.0 and vol_ratio >= 1.15)
    recent_breakout = any(float(close_s.iloc[i]) > float(high_s.iloc[max(0, i-20):i].max())
                          for i in range(max(20, n-7), n-1))
    breakout_retest = (recent_breakout and abs(pivot_distance) <= 3.0 and price >= prior20_high*0.985
                       and today_pct <= 4.0 and vol_ratio <= 1.15 and close_loc >= 0.45)
    coiling = (range10 <= 10.0 and range5 <= range10*0.75 and -3.5 <= pivot_distance <= 1.0
               and abs(ret5) <= 6.0 and vol_ratio <= 1.05 and price >= ma20.iloc[-1])

    if extended:
        stage, stage_score = '漲幅延伸／追價風險', -22.0
    elif repair_bounce:
        stage, stage_score = '修復反彈／尚待突破確認', -12.0
    elif fresh_breakout:
        stage, stage_score = '剛突破起漲', 32.0
    elif breakout_retest:
        stage, stage_score = '突破後量縮回測', 28.0
    elif coiling:
        stage, stage_score = '壓縮蓄勢待突破', 25.0
    else:
        stage, stage_score = '趨勢中段／訊號未明', 0.0
    score += stage_score
    reasons.append(f"🎯階段:{stage}({stage_score:+.0f}分)")

    # 趨勢最多 +15，量價最多 +12，MACD 最多 +10，避免同一漲勢重複加權。
    if price >= ma5.iloc[-1] > ma10.iloc[-1] > ma20.iloc[-1] and s20 > 0:
        score += 15.0; reasons.append('📈短中均線多頭(+15分)')
    elif price >= ma20.iloc[-1] and s20 >= 0:
        score += 8.0; reasons.append('🛡️站穩上升月線(+8分)')
    else:
        score -= 10.0; reasons.append('⚠️月線趨勢未確認(-10分)')

    volume_risk = any(k in t for t in vtags for k in ('爆歷史天量收黑', '量價頂背離', '天量阻力牆'))
    if volume_risk:
        score -= 18.0; reasons.append('⚠️量價出貨／壓力訊號(-18分)')
    elif fresh_breakout:
        score += 12.0; reasons.append(f'🔥突破量能確認({vol_ratio:.1f}x,+12分)')
    elif (breakout_retest or coiling) and vol_ratio <= 0.85:
        score += 10.0; reasons.append(f'💎結構內量縮({vol_ratio:.1f}x,+10分)')
    elif 0.8 <= vol_ratio <= 1.5:
        score += 4.0

    if any('金叉' in t or '綠柱收斂' in t for t in mtags):
        score += 10.0; reasons.append('✨MACD轉強(+10分)')
    elif any('強勢多頭' in t for t in mtags):
        score += 6.0
    elif any('死亡交叉' in t or '翻綠' in t for t in mtags):
        score -= 12.0; reasons.append('⚠️MACD轉弱(-12分)')

    if intraday_pct < -2.2 and close_loc < 0.30:
        score -= 15.0; reasons.append(f'⚠️開高走低長黑(-15分)')
    elif any('假突破' in t or '誘多出貨' in t for t in ktags):
        score -= 15.0; reasons.append('⚠️假突破上影線(-15分)')

    if trust_buy_days >= 3:
        score += 10.0; reasons.append(f"投信買超({trust_buy_days}/5日,+10分)")
    elif inst_buy_days >= 3:
        score += 6.0; reasons.append(f"法人回補({inst_buy_days}/5日,+6分)")

    support = max(float(ma20.iloc[-1]), prior20_high*0.975 if stage in ('剛突破起漲','突破後量縮回測') else float(ma10.iloc[-1]))
    stop_loss = round(min(price * 0.99, support - atr14 * 0.5), 2)
    technical_target_price = round(prior20_high + atr14 * 2.0, 2) if prior20_high > price else round(price + atr14*2.0, 2)
    rr_ratio = round((technical_target_price-price)/max(price-stop_loss, 0.1), 1)

    candidate = {
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
        'ret3': round(ret3, 1), 'ret5': round(ret5, 1), 'ret10': round(ret10, 1),
        'atr_pct': round(atr_pct, 1), 'pivot_price': round(prior20_high, 2),
        'pivot_distance': round(pivot_distance, 1), 'range5': round(range5, 1),
        'range10': round(range10, 1), 'setup_stage': stage,
        'score': round(score, 1),
        'reasons': reasons,
        'stop_loss': stop_loss,
        'target_price': '',
        'technical_target_price': technical_target_price,
        'rr_ratio': rr_ratio,
        'above_5ma': bool(price >= ma5.iloc[-1]),
        'session_date': stock_info.get('session_date', ''),
        'date': kline[-1].get('date', '最新')
    }
    return apply_disposition_penalty(candidate, '本機報表標示處置（待當日查核）' if is_disposal else '')

def call_gemini_search(prompt, api_key, models_priority=GEMINI_MODELS_PRIORITY):
    """透過 Google Search Grounding 即時聯網搜尋最新月盈年盈營收、季報EPS、法說會利多與法人目標價"""
    manual_response = consume_manual_llm_response(prompt)
    if manual_response:
        return manual_response, 'Gemini 3.8 網頁版人工查核'
    for m in models_priority:
        print(f'⏳ LLM 搜尋：等待 {m} 回覆（逾時後嘗試備援）', flush=True)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "tools": [{"googleSearch": {}}],
            "generationConfig": {"thinkingConfig": {"thinkingLevel": "low"}}
        }
        try:
            res = requests.post(url, json=payload, timeout=30)
            if res.status_code == 200:
                data = res.json()
                text = data['candidates'][0]['content']['parts'][0]['text']
                return text, m
            elif res.status_code in (503, 429):
                print(f"      [Gemini {m}] HTTP {res.status_code}: {gemini_error_summary(res)}", flush=True)
                continue
            else:
                try:
                    detail = res.json().get('error', {}).get('message', '')
                except Exception:
                    detail = res.text
                print(f"      [Gemini {m}] HTTP {res.status_code}: {detail[:300]}", flush=True)
        except Exception as e:
            print(f"      [Gemini {m}] 請求異常: {e}", flush=True)
            continue
    request_manual_llm_response(prompt)

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
            if (cached.get('schema_version') == 2
                    and cached.get('as_of_date') == as_of_date
                    and isinstance(cached.get('hot_sectors'), list)):
                return cached.get('overview', ''), cached.get('hot_sectors', [])
        except Exception:
            pass

    prompt = f"""【系統角色與任務】
你是一名台股外資頂級量化操盤手與產業研究總監。基準審查日期為：{as_of_date}。
請透過 Google Search 即時查核當前（{as_of_date} 當週）台股盤面最新受市場大資金（外資/投信/主力）追捧的「核心強勢產業族群、重大法說會利多與主流題材」：
目標是預測未來 3 至 5 個交易日，不要把已集體急漲、只有舊聞重述、或利多已充分反映的族群列為風口。允許找不到任何合格族群並回傳空陣列。

【熱度評分標準 (heat_level 嚴格量化，嚴禁全員5星)】
- 5 星 (剛加速主線)：近 1 至 5 個交易日資金廣度與成交金額同步升溫，且有 14 天內新實質催化劑；已連續急漲者不得給 5 星。
- 4 星 (強勢輪動線)：族群有多檔個股站穩均線起漲，法說會展望正向，外資投信連續買超。
- 3 星 (潛在發酵線)：低檔轉機或少數龍頭突圍，題材初期萌芽。
(必須依照真實盤面資金流向給予 3~5 分的分級)

【輸出規格】
每個族群至少提供一筆可開啟的來源網址。event_date 必須是事件首次公開日期，不是文章轉載日期。請直接輸出 JSON：
為降低多次搜尋的差異，來源固定依序採用：公開資訊觀測站／證交所／櫃買中心／公司官網與法說資料，再採用具日期的財經媒體。相同事件有衝突時採較前順位來源；同順位採發布時間較晚者。族群排序依 heat_level、資金廣度、催化劑日期由高至低，仍同分時依 sector_name 排序。
請務必執行 Google Search。最後只能輸出 prompt 指定的完整 JSON，不要 Markdown、不要前言、不要引用標記放在 JSON 外面。
{{
  "market_overview": "一句話總結今日台股市場焦點與資金主軸（50字以內）",
  "hot_sectors": [
    {{
      "sector_name": "產業族群名稱",
      "heat_level": 5,
      "stage": "emerging或accelerating或extended",
      "catalysts": "最新重大法說會重點、關鍵大訂單或產業爆發動能（杜絕籠統空話）",
      "event_date": "YYYY-MM-DD",
      "source_url": "https://來源網址",
      "related_tags": ["相關標籤或次產業1", "次產業2"]
    }}
  ]
}}
"""
    overview = "產業新聞查核未完成，無已驗證風口。"
    hot_sectors = []

    text, _ = call_gemini_search(prompt, api_key)
    if text:
        try:
            m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
            json_str = m.group(1) if m else text.strip()
            # 容錯處理：消除可能存在的尾隨逗號
            json_str = re.sub(r',\s*([\}\]])', r'\1', json_str)
            data = json.loads(json_str)
            raw_sectors = data.get('hot_sectors', [])
            hot_sectors = [s for s in raw_sectors
                           if isinstance(s, dict)
                           and s.get('stage') in ('emerging', 'accelerating')
                           and evidence_is_recent(s, as_of_date, 14)
                           and isinstance(s.get('heat_level'), (int, float))
                           and 3 <= s['heat_level'] <= 5]
            if data.get('market_overview'):
                overview = data['market_overview']
        except Exception:
            pass

    try:
        SECTOR_CACHE_FILE.write_text(json.dumps({
            'schema_version': 2,
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


AUDIT_BLOCKS = ('monthly_revenue', 'earnings', 'catalyst', 'analyst_target', 'disposition')


def is_complete_audit_payload(data):
    """所有區塊可含 null，但不可省略，避免將 Gemini 漏答誤認為已完成。"""
    return isinstance(data, dict) and all(isinstance(data.get(key), dict) for key in AUDIT_BLOCKS)


def normalize_audit_payload(data, as_of_date, used_model):
    if not is_complete_audit_payload(data):
        return None
    revenue = data.get('monthly_revenue') if isinstance(data.get('monthly_revenue'), dict) else {}
    earnings_data = data.get('earnings') if isinstance(data.get('earnings'), dict) else {}
    catalyst_data = data.get('catalyst') if isinstance(data.get('catalyst'), dict) else {}
    target_data = data.get('analyst_target') if isinstance(data.get('analyst_target'), dict) else {}
    disposition_data = data.get('disposition') if isinstance(data.get('disposition'), dict) else {}
    rev = (f"{revenue.get('period')}營收{revenue.get('amount_text')}"
           f"(MoM{revenue.get('mom_pct'):+g}%, YoY{revenue.get('yoy_pct'):+g}%)"
           if all(revenue.get(k) is not None for k in ('period','amount_text','mom_pct','yoy_pct')) else '')
    earn = (f"{earnings_data.get('period')} EPS {earnings_data.get('eps')}元"
            + (f"，EPS YoY {earnings_data.get('eps_yoy_pct'):+g}%" if isinstance(earnings_data.get('eps_yoy_pct'), (int,float)) else '')
            + (f"，毛利率{earnings_data.get('gross_margin_pct')}%" if isinstance(earnings_data.get('gross_margin_pct'), (int,float)) else '')
            if earnings_data.get('period') and isinstance(earnings_data.get('eps'), (int,float)) else '')
    target_price = (f"90日內法人目標價中位數{target_data.get('median_price'):g}元"
                    f"({target_data.get('rating') or '未提供評等'}, {int(target_data.get('sample_size') or 0)}筆)"
                    if isinstance(target_data.get('median_price'), (int,float)) else '')
    status = disposition_data.get('status')
    disposition_checked = status in ('normal', 'disposition') and evidence_is_recent(disposition_data, as_of_date, 0)
    return {
        'monthly_rev': rev, 'earnings': earn,
        'catalyst': str(catalyst_data.get('summary') or ''), 'target_price': target_price,
        'disposition': "列入處置股(分盤撮合/流動性急凍)" if disposition_checked and status == 'disposition' else '',
        'disposition_checked': disposition_checked, 'audit_data': data, '_used_model': used_model
    }


def same_day_legacy_result(item, as_of_date):
    if not isinstance(item, dict):
        return None
    cached_date = str(item.get('updated_at', '')).replace('/', '-')
    if cached_date != as_of_date and cached_date != as_of_date[5:]:
        return None
    return {
        'monthly_rev': item.get('monthly_rev', item.get('revenue', '')),
        'earnings': item.get('earnings', ''), 'catalyst': item.get('catalyst', ''),
        'target_price': item.get('target_price', ''), 'audit_data': {},
        'disposition': item.get('disposition', ''), 'disposition_checked': False,
        '_legacy_fallback': True, '_used_model': '同交易日舊格式快取（未計分）'
    }


def recent_legacy_result(item, as_of_date, max_age_days=7):
    """當日 LLM 被配額擋住時，保留最近有效資料供顯示；資料不參與今日加分。"""
    same_day = same_day_legacy_result(item, as_of_date)
    if same_day:
        return same_day
    if not isinstance(item, dict):
        return None
    raw_date = str(item.get('updated_at', '')).replace('/', '-')
    try:
        if re.fullmatch(r'\d{2}-\d{2}', raw_date):
            raw_date = f"{as_of_date[:4]}-{raw_date}"
        age = (date.fromisoformat(as_of_date) - date.fromisoformat(raw_date)).days
    except (TypeError, ValueError):
        return None
    if age < 0 or age > max_age_days:
        return None
    result = {
        'monthly_rev': item.get('monthly_rev', item.get('revenue', '')),
        'earnings': item.get('earnings', ''), 'catalyst': item.get('catalyst', ''),
        'target_price': item.get('target_price', ''), 'audit_data': {},
        'disposition': '', 'disposition_checked': False,
        '_legacy_fallback': True,
        '_used_model': f'最近有效查核 {raw_date}（今日未更新、未計分）',
        '_cached_date': raw_date,
    }
    return result if any(result.get(k) for k in ('monthly_rev', 'earnings', 'catalyst', 'target_price')) else None

def audit_stock_with_gemini(candidate, api_key, as_of_date):
    code = candidate['code']
    cache = load_fundamental_cache()
    legacy_item = cache.get(code) if isinstance(cache.get(code), dict) else None

    # 1. 優先取用快取（必須具備法人目標價與月盈年盈資訊，若缺少則重新聯網查核）
    if code in cache:
        item = cache[code]
        if (item.get('schema_version') == 2 and item.get('updated_at') == as_of_date):
            print(f'💾 {code} 使用同交易日查核快取', flush=True)
            return {
                'monthly_rev': item.get('monthly_rev', ''),
                'earnings': item.get('earnings', ''),
                'catalyst': item.get('catalyst', ''),
                'target_price': item.get('target_price', ''),
                'audit_data': item.get('audit_data', {}),
                'disposition': item.get('disposition', ''),
                'disposition_checked': item.get('disposition_checked', False),
                'is_trap': False,
                'confidence_bonus': 10,
                '_used_model': 'Google Search Grounding (Cached)'
            }

    # 2. 即時聯網 Google Search Grounding 深度查核五大關鍵事實
    prompt = f"""
【審計任務】
你是極度嚴苛的台股避險基金首席審計官。基準審計日：{as_of_date}。
請透過 Google Search 即時查核台股「{candidate['code']} {candidate['name']}」（產業分類：{candidate['category']}）最新之客觀公開數據、重大訊息與法人目標價：

【防偽與時效規則】
每項資料附原始來源網址與事件首次公開日期；找不到就填 null，禁止猜測。所有資料必須在 {as_of_date} 當日收盤前已公開。
催化劑同時搜尋利多與利空，direction 只能是 positive、neutral、negative；new_information 只有 30 天內首次出現且改變未來展望才可為 true。
目標價只填 90 天內報告的中位數或唯一一筆數值，禁止取最高價；同時回傳樣本數與評等。
處置狀態只確認 {as_of_date} 當日。
來源固定依序採用：公開資訊觀測站／證交所／櫃買中心／公司官網與法說資料，再採用具日期的財經媒體。資料衝突時採較前順位來源；同順位採發布時間較晚者。不得因搜尋結果排序改變而改用較舊資料。
五個資料區塊都必須保留；查不到時保留區塊並將值填 null。

請務必執行 Google Search。最後只能輸出 prompt 指定的完整 JSON，不要 Markdown、不要前言、不要引用標記放在 JSON 外面。
請只輸出以下 JSON：
{{
 "monthly_revenue": {{"period":null,"amount_text":null,"mom_pct":null,"yoy_pct":null,"event_date":null,"source_url":null}},
 "earnings": {{"period":null,"eps":null,"eps_yoy_pct":null,"gross_margin_pct":null,"event_date":null,"source_url":null}},
 "catalyst": {{"summary":null,"direction":"neutral","new_information":false,"event_date":null,"source_url":null}},
 "analyst_target": {{"median_price":null,"rating":null,"sample_size":0,"event_date":null,"source_url":null}},
 "disposition": {{"status":"normal或disposition或unknown","event_date":"{as_of_date}","source_url":null}}
}}
"""
    text, used_model = call_gemini_search(prompt, api_key)
    data = parse_json_object(text)
    if data:
        result = normalize_audit_payload(data, as_of_date, used_model)
    else:
        result = None
    if result:
        cache[code] = {
            'schema_version': 2,
            'name': candidate['name'],
            'monthly_rev': result['monthly_rev'], 'earnings': result['earnings'],
            'catalyst': result['catalyst'], 'target_price': result['target_price'],
            'disposition': result['disposition'],
            'disposition_checked': result['disposition_checked'],
            'audit_data': data,
            'updated_at': as_of_date
        }
        save_fundamental_cache(cache)
        return result
    # 新查核失敗時可沿用同一交易日舊資料作畫面參考，但因缺少來源結構，絕不參與加分。
    return same_day_legacy_result(legacy_item, as_of_date)


def audit_candidates_with_gemini(candidates, api_key, as_of_date, chunk_size=10):
    """批次查核以降低 RPM/配額壓力；同交易日舊格式資料只供顯示且不計分。"""
    cache = load_fundamental_cache()
    results = {}
    pending = []
    for candidate in candidates:
        item = cache.get(candidate['code']) if isinstance(cache.get(candidate['code']), dict) else None
        if item and item.get('schema_version') == 2 and item.get('updated_at') == as_of_date:
            result = normalize_audit_payload(item.get('audit_data', {}), as_of_date,
                                             'Google Search Grounding (Cached)')
            if result:
                results[candidate['code']] = result
                print(f"💾 {candidate['code']} 使用同交易日結構化快取", flush=True)
                continue
        pending.append(candidate)

    total_chunks = (len(pending) + chunk_size - 1) // chunk_size
    for chunk_index, start in enumerate(range(0, len(pending), chunk_size), 1):
        chunk = pending[start:start + chunk_size]
        stock_list = [{'code': c['code'], 'name': c['name'], 'category': c['category']} for c in chunk]
        print(f"  -> 🔍 批次聯網查核 {', '.join(c['code'] for c in chunk)} [{chunk_index}/{total_chunks}批]...", flush=True)
        prompt = f"""你是嚴格的台股事實審計員。截止日為 {as_of_date}，一次查核以下股票：
{json.dumps(stock_list, ensure_ascii=False)}
只能使用截止日收盤前已公開資料；每項附事件首次公開日與原始來源網址，未知填 null。催化劑須同查利多與利空。
目標價只填截止日前90天內報告的中位數或唯一值，禁止取最高值。處置只確認截止日當日。
為降低多次搜尋的差異，來源固定依序採用：公開資訊觀測站／證交所／櫃買中心／公司官網與法說資料，再採用具日期的財經媒體。相同事件有衝突時採較前順位來源；同順位採發布時間較晚者。不得因搜尋結果排序改變而改用較舊資料。
stocks 必須逐一回覆上列每個股票代號，順序相同、不可遺漏、不可增加；五個資料區塊都必須保留，查不到資料時保留該區塊並將值填 null。
請務必執行 Google Search。最後只能輸出 prompt 指定的完整 JSON，不要 Markdown、不要前言、不要引用標記放在 JSON 外面。
只輸出 JSON，格式為：
{{"stocks":[{{"code":"股票代號","monthly_revenue":{{"period":null,"amount_text":null,"mom_pct":null,"yoy_pct":null,"event_date":null,"source_url":null}},"earnings":{{"period":null,"eps":null,"eps_yoy_pct":null,"gross_margin_pct":null,"event_date":null,"source_url":null}},"catalyst":{{"summary":null,"direction":"positive或neutral或negative","new_information":false,"event_date":null,"source_url":null}},"analyst_target":{{"median_price":null,"rating":null,"sample_size":0,"event_date":null,"source_url":null}},"disposition":{{"status":"normal或disposition或unknown","event_date":"{as_of_date}","source_url":null}}}}]}}
"""
        text, used_model = call_gemini_search(prompt, api_key)
        payload = parse_json_object(text)
        returned = payload.get('stocks', []) if isinstance(payload, dict) else []
        by_code = {str(item.get('code')): item for item in returned if isinstance(item, dict) and item.get('code')}
        for candidate in chunk:
            code = candidate['code']
            result = normalize_audit_payload(by_code.get(code), as_of_date, used_model)
            if result:
                results[code] = result
                cache[code] = {
                    'schema_version': 2, 'name': candidate['name'],
                    'monthly_rev': result['monthly_rev'], 'earnings': result['earnings'],
                    'catalyst': result['catalyst'], 'target_price': result['target_price'],
                    'disposition': result['disposition'],
                    'disposition_checked': result['disposition_checked'],
                    'audit_data': result['audit_data'], 'updated_at': as_of_date
                }
            else:
                legacy = same_day_legacy_result(cache.get(code), as_of_date)
                if legacy:
                    results[code] = legacy
        save_fundamental_cache(cache)
    return results

def evaluate_holistic_score(cand, hot_sectors):
    """
    【步驟 4：全維度多因子融合評估矩陣】
    嚴格遵守：先有產業新聞與個股基本面事實，再做評估排榜！
    融合因子：技術起漲階段、股票池相對強度、已驗證產業風口、
    營收/EPS 成長、30 日內新催化與 90 日內法人目標價中位數。
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
    audit_data = cand.get('audit_data', {})

    matched_sector, match_strength = match_stock_to_hot_sectors(cand, hot_sectors)

    if matched_sector:
        heat = matched_sector.get('heat_level', 3)
        sec_bonus = {5: 22.0, 4: 16.0, 3: 9.0}.get(int(heat), 0.0)
        if match_strength >= 50.0:
            sec_bonus += 3.0 # 高度契合強勢概念本體額外加成
        score += sec_bonus
        cand['matched_sector'] = matched_sector.get('sector_name', '')
        reasons.insert(0, f"🔥踩中市場主流風口:【{matched_sector.get('sector_name', '')}】(+{sec_bonus:.0f}分)")
    else:
        if hot_sectors:
            score -= 6.0 # 已找到市場風口但個股未契合時才折價；搜尋失敗不懲罰個股。

    # B. 營收動能評估 (MoM / YoY)
    rev_bonus = 0.0
    revenue_data = audit_data.get('monthly_revenue', {})
    if evidence_is_recent(revenue_data, cand.get('session_date', cand.get('date_iso', '')), 45):
        yoy_val = revenue_data.get('yoy_pct')
        mom_val = revenue_data.get('mom_pct')

        if isinstance(yoy_val, (int, float)):
            if yoy_val >= 50.0:
                rev_bonus += 16.0
            elif yoy_val >= 20.0:
                rev_bonus += 12.0
            elif yoy_val >= 0.0:
                rev_bonus += 6.0
            elif yoy_val < -10.0:
                rev_bonus -= 10.0

        if isinstance(mom_val, (int, float)):
            if mom_val >= 10.0:
                rev_bonus += 8.0
            elif mom_val >= 3.0:
                rev_bonus += 5.0
            elif mom_val < -15.0:
                rev_bonus -= 6.0

        score += rev_bonus

    # C. 獲利品質評估 (EPS / 毛利)
    earn_bonus = 0.0
    earnings_data = audit_data.get('earnings', {})
    if evidence_is_recent(earnings_data, cand.get('session_date', cand.get('date_iso', '')), 150):
        eps_val = earnings_data.get('eps')
        eps_yoy = earnings_data.get('eps_yoy_pct')
        if isinstance(eps_val, (int,float)) and eps_val < 0:
            earn_bonus -= 10.0
        if isinstance(eps_yoy, (int,float)):
            if eps_yoy >= 30: earn_bonus += 12.0
            elif eps_yoy >= 10: earn_bonus += 7.0
            elif eps_yoy <= -20: earn_bonus -= 10.0
        score += earn_bonus

    # D. 法說會與實質利多 (Catalysts)
    cat_bonus = 0.0
    catalyst_data = audit_data.get('catalyst', {})
    if evidence_is_recent(catalyst_data, cand.get('session_date', cand.get('date_iso', '')), 30):
        direction = catalyst_data.get('direction')
        is_new = catalyst_data.get('new_information') is True
        if direction == 'positive' and is_new:
            cat_bonus = 15.0
            reasons.append('📢30日內新正向催化(+15分)')
        elif direction == 'negative':
            cat_bonus = -15.0
            reasons.append('⚠️30日內負向事件(-15分)')
        score += cat_bonus

    # E. 法人目標價潛在上檔空間 (Analyst Upside)
    target_bonus = 0.0
    target_data = audit_data.get('analyst_target', {})
    if evidence_is_recent(target_data, cand.get('session_date', cand.get('date_iso', '')), 90):
        median_tp = target_data.get('median_price')
        if isinstance(median_tp, (int,float)) and int(target_data.get('sample_size') or 0) >= 1:
            upside_pct = (median_tp - price) / price * 100
            if upside_pct >= 30.0:
                target_bonus += 10.0
                reasons.append(f"🎯法人目標價溢價空間巨大(+{upside_pct:.0f}%)")
            elif upside_pct >= 15.0:
                target_bonus += 6.0
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

def load_cross_ranking_memberships():
    """讀取另外兩份榜單的入榜代號，僅供交叉驗證顯示，不影響 AI 榜分數。"""
    memberships = {'chatgpt': set(), 'gemini': set()}
    for key, path in (('chatgpt', CHATGPT_RANKING_MD), ('gemini', GEMINI_RANKING_MD)):
        try:
            text = path.read_text(encoding='utf-8')
        except OSError:
            continue
        memberships[key] = set(re.findall(r'`(\d{4,6})`', text))
    return memberships


def write_evolution_ranking_md(selected_list, hot_sectors, market_overview, as_of_date, model_label):
    count = len(selected_list)
    active_count = sum(r.get('selection_tier') == '正式起漲候選' for r in selected_list)
    watch_count = count - active_count
    sec_title = f"## 👑 【AI 獨有實戰勝率榜】（正式 {active_count} 檔・觀察 {watch_count} 檔・{model_label}）"
    sec_sub = ("> 正式候選已通過起漲型態與分數門檻；**觀察候選尚未突破觸發價，不能視為進場訊號**。"
               "LLM 查核失敗時保留技術排序，舊資料只顯示、不計分。")

    sec_pills = " ｜ ".join([f"**{s.get('sector_name', '')}** ({s.get('catalysts', '')[:25]}...)" for s in hot_sectors[:4]])

    comparison = load_cross_ranking_memberships()
    both_count = sum(r['code'] in comparison['chatgpt'] and r['code'] in comparison['gemini']
                     for r in selected_list)
    either_count = sum(r['code'] in comparison['chatgpt'] or r['code'] in comparison['gemini']
                       for r in selected_list)
    lines = [
        '# 👑 台股 AI 獨有實戰勝率榜 (AI Self-Evolving Master Watchlist)', '',
        f'> 資料截止日：{as_of_date}。驅動核心：{model_label}。體系核心：**先產業新聞與法說會掃描 ➔ 候選池全維度調研 ➔ 綜合加權排定榜單**。', '',
        '### 🌐 【今日盤面主流強勢產業風口】',
        f'> **市場資金焦點**：{market_overview}',
        f'> **核心焦點族群**：{sec_pills}', '',
        '---', '',
        sec_title,
        sec_sub, '',
        f'> **三榜交叉驗證**：本榜 {count} 檔中，{both_count} 檔同時進入 ChatGPT 與 Gemini 榜；{either_count} 檔至少進入其中一榜。交叉結果只顯示、不加分。', '',
        '| 排名 | 股票代號 | 股票名稱 | 類群 | 收盤價 | 今日漲跌 | 實戰評分 | 其他榜單 | 月盈年盈(營收) | 季報獲利(EPS) | 法說重點與實質利多 | 法人目標價 | 核心技術起漲特徵 |',
        '|:---:|:---:|:---|:---|---:|---:|---:|:---|:---|:---|:---|:---|:---|'
    ]
    for i, r in enumerate(selected_list, 1):
        clean_reasons = []
        if r.get('selection_tier'):
            clean_reasons.append(f"{r['selection_tier']}・突破觸發價{r.get('pivot_price', 0):.2f}元")
        if r.get('verification_status'):
            clean_reasons.append(f"情報狀態:{r['verification_status']}")
        for reas in r.get('holistic_reasons', r.get('reasons', [])):
            c = re.sub(r'[💎⚠️]?【風報比[^】]*】', '', reas).strip()
            # 排除已獨立成欄的基本面或警示字串
            if any(c.startswith(k) for k in ['📊', '💰', '📢', '🎯', '🚨']):
                continue
            if c: clean_reasons.append(c)
        feat = " ； ".join(clean_reasons[:3]) or "多頭結構守穩"
        pct_str = f"{r['today_pct']:+5.2f}%"
        status = r.get('verification_status', '')
        verified = status == 'LLM證據已查核'
        missing_label = '未查得可驗證資料' if verified else 'LLM查核未完成'
        rev_str = r.get('monthly_rev') or missing_label
        earn_str = r.get('earnings') or missing_label
        cat_str = r.get('catalyst') or missing_label
        target_str = r.get('target_price') or ('90日內未查得公開法人目標價' if verified else 'LLM查核未完成')
        in_chatgpt = r['code'] in comparison['chatgpt']
        in_gemini = r['code'] in comparison['gemini']
        cross_label = ('ChatGPT＋Gemini' if in_chatgpt and in_gemini else
                       'ChatGPT' if in_chatgpt else 'Gemini' if in_gemini else '兩榜皆無')
        display_score = r.get('holistic_score', r['score'])

        lines.append(f"| **{i}** | `{r['code']}` | **{r['name']}** | {r['category']} | {r['price']:.2f} | {pct_str} | **{display_score}** | {cross_label} | {rev_str} | {earn_str} | {cat_str} | {target_str} | {feat} |")

    OUTPUT_EVO_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"📄 獨有勝率榜單已輸出至：{OUTPUT_EVO_MD.name}", flush=True)

def main():
    t_start = time.perf_counter()
    print("=" * 75, flush=True)
    print("👑 啟動 AI 獨有實戰勝率與自我進化引擎 (evolution_engine.py)", flush=True)
    print("📌 體系紀律：【先搜尋產業面新聞與法說會 ➔ 全面調研候選股 ➔ 綜合資訊評估排榜】", flush=True)
    print("=" * 75, flush=True)

    api_key = get_gemini_api_key()
    model_label = "Gemini Search Grounding"
    if api_key:
        print(f"🔑 成功載入 GEMINI_API_KEY，啟用 Google Search 即時聯網查核 [{model_label}]", flush=True)
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
            generated = re.search(r'產生時間[：:]\s*(\d{4}-\d{2}-\d{2})',
                                  f.read_text(encoding='utf-8', errors='ignore'))
            try:
                inf['session_date'] = report_session_date(
                    inf['kline'][-1]['date'], generated.group(1) if generated else '')
            except (ValueError, IndexError, KeyError):
                print(f'⚠️ 跳過無法確認交易日期的報表：{f.name}', flush=True)
                continue
            # 🤖 自動產業分類與語意本體庫判定 (AI 擔任規則制定者與裁判，自動精準收納)
            std_cat, sem_tags = classify_stock(inf['code'], inf['name'], inf.get('category'))
            inf['category'] = std_cat
            inf['semantic_tags'] = sem_tags
            infos.append(inf)

    if not infos:
        print('❌ 沒有可確認交易日期的報表。', flush=True)
        return
    as_of_date = max(inf['session_date'] for inf in infos)
    # 排名只比較同一交易日；舊報表不可冒充最新報價。
    unique_infos = {inf['code']: inf for inf in infos if inf['session_date'] == as_of_date}

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
    apply_universe_relative_strength(candidates)

    # 先讓已驗證產業風口參與候選排序，避免熱門產業的早期蓄勢股被純技術漏斗提前淘汰。
    for candidate in candidates:
        sector, strength = match_stock_to_hot_sectors(candidate, hot_sectors)
        heat = int(sector.get('heat_level', 0)) if sector else 0
        candidate['pre_audit_score'] = candidate['score'] + ({5: 18, 4: 12, 3: 6}.get(heat, 0))
        candidate['pre_matched_sector'] = sector.get('sector_name', '') if sector else ''
        candidate['sector_match_strength'] = strength

    momentum_pool = sorted(
        [r for r in candidates if r['setup_stage'] in ('剛突破起漲', '突破後量縮回測')],
        key=lambda x: x['pre_audit_score'], reverse=True)[:8]
    seen_codes = {r['code'] for r in momentum_pool}
    dip_pool = sorted(
        [r for r in candidates if r['code'] not in seen_codes and r['setup_stage'] == '壓縮蓄勢待突破'],
        key=lambda x: x['pre_audit_score'], reverse=True)[:8]
    seen_codes.update(r['code'] for r in dip_pool)
    watch_pool = sorted(
        [r for r in candidates if r['code'] not in seen_codes
         and r['setup_stage'] == '趨勢中段／訊號未明'
         and r['score'] >= 70 and -8.0 <= r['pivot_distance'] <= 2.0
         and r['today_pct'] < 7.0 and r['ret5'] < 13.0],
        key=lambda x: x['pre_audit_score'], reverse=True)[:5]
    pre_audit_pool = momentum_pool + dip_pool + watch_pool

    print(f"  👉 進入全維度深度調研池：共 {len(pre_audit_pool)} 檔標的 (正式起漲 {len(momentum_pool)} 檔 + 蓄勢 {len(dip_pool)} 檔 + 觀察 {len(watch_pool)} 檔)", flush=True)

    # =========================================================================
    # 🤖 【步驟 3：個股基本面與新聞全維度調研】(在排定榜單前先調研完畢！)
    # =========================================================================
    print("\n🤖 [Step 3] 全面聯網調研候選池個股之月盈年盈、獲利EPS、法說會與法人目標價...", flush=True)
    audited_candidates = []
    actual_model_used = model_label
    if api_key:
        audits = audit_candidates_with_gemini(pre_audit_pool, api_key, as_of_date)
        verified_count = sum(not a.get('_legacy_fallback') for a in audits.values())
        legacy_count = sum(bool(a.get('_legacy_fallback')) for a in audits.values())
        print(f"  📊 查核資料狀態：今日完成 {verified_count} 檔、同日舊資料 {legacy_count} 檔、完全缺失 {len(pre_audit_pool)-len(audits)} 檔", flush=True)
        for audit_index, cand in enumerate(pre_audit_pool, 1):
            print(f"  -> 📋 整理 {cand['code']} {cand['name']} [{audit_index}/{len(pre_audit_pool)}]", flush=True)
            audit = audits.get(cand['code'])
            if not audit:
                cand['verification_status'] = 'LLM查核失敗，僅技術排序'
                cand['reasons'].append('⚠️LLM情報未完成（不加基本面分）')
                audited_candidates.append(cand)
                continue
            if audit.get('_used_model') and not audit.get('_legacy_fallback'):
                actual_model_used = audit.get('_used_model')
            cand['monthly_rev'] = audit.get('monthly_rev', '').strip()
            cand['earnings'] = audit.get('earnings', '').strip()
            cand['catalyst'] = audit.get('catalyst', '').strip()
            cand['target_price'] = audit.get('target_price', '').strip()
            cand['audit_data'] = audit.get('audit_data', {})
            cand['session_date'] = as_of_date
            cand['verification_status'] = ((audit.get('_used_model') or '沿用舊查核（僅顯示、不計分）')
                                           if audit.get('_legacy_fallback') else 'LLM證據已查核')
            disp_desc = audit.get('disposition', '').strip()
            if disp_desc or audit.get('disposition_checked'):
                apply_disposition_penalty(cand, disp_desc)
            audited_candidates.append(cand)
    else:
        audited_candidates = pre_audit_pool
        for cand in audited_candidates:
            cand['verification_status'] = '未設定API，僅技術排序'
            cand['reasons'].append('⚠️未設定LLM API（不加基本面分）')

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
    # 新評分尺度的技術起漲合格門檻為 105 分；LLM 證據只負責加減分，不得因 API 失敗刪除候選。
    # 凡通過者全部列出；若超過 10 檔僅取最優秀 TOP 10；絕不硬湊，也絕不錯殺合格優秀者！
    # =========================================================================
    print("\n👑 [Step 5] 依據全維度綜合評估得分 (Holistic Score)，正式排定最終榜單名次...", flush=True)
    QUALIFIED_THRESHOLD = 105.0
    final_qualified = select_actionable_candidates(evaluated_candidates, QUALIFIED_THRESHOLD)

    count = len(final_qualified)
    active_count = sum(r.get('selection_tier') == '正式起漲候選' for r in final_qualified)
    print(f"\n👑 【AI 獨有實戰勝率榜】（正式 {active_count} 檔・觀察 {count-active_count} 檔・起漲門檻 {QUALIFIED_THRESHOLD} 分）", flush=True)
    print(f"{'名次':<4} {'代號':<6} {'名稱':<8} {'類群':<8} {'收盤價':<9} {'今日漲跌':<10} {'月盈年盈營收':<22} {'終極實戰評分'}", flush=True)
    print("-" * 80, flush=True)
    for i, r in enumerate(final_qualified, 1):
        rev_brief = (r.get('monthly_rev', '')[:20] + '..') if len(r.get('monthly_rev', '')) > 20 else r.get('monthly_rev', '—')
        print(f"#{i:<3} {r['code']:<6} {r['name']:<8} {r['category']:<8} {r['price']:<9.2f} {r['today_pct']:+6.2f}%    {rev_brief:<22} {r['holistic_score']}")

    write_ranking_snapshot(as_of_date, evaluated_candidates, final_qualified, hot_sectors)
    write_evolution_ranking_md(final_qualified, hot_sectors, overview, as_of_date, actual_model_used)
    print('📝 [Step 6] 榜單已寫入，正在產生 LLM 覆盤日記...', flush=True)
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
    try:
        main()
    except ManualLLMResponseRequired:
        print("⏸️ 已暫停評分，等待 Gemini 網頁版回覆；既有正式榜單未被覆寫。", flush=True)
        raise SystemExit(2)

