#!python3.11
"""
台股分析工具 - 精簡Token版 (上市/上櫃 完美整合版 + 處置期間判斷)
用法: python tw_analysis.py 6182
"""
import sys, time, warnings, os, re, json, logging, glob, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
warnings.filterwarnings("ignore")
logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    import yfinance as yf
    import pandas as pd
    import numpy as np
    import requests
    import matplotlib
    matplotlib.use("Agg")
    matplotlib.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "Microsoft YaHei", "SimHei"]
    matplotlib.rcParams["axes.unicode_minus"] = False
    import mplfinance as mpf
except ImportError as e:
    print(f"❌ 缺少套件: {e}")
    print("請執行: pip install yfinance pandas numpy requests urllib3 matplotlib mplfinance")
    input("按 Enter 鍵結束…")
    sys.exit(1)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.twse.com.tw/",
}

DAYS_LOOKBACK = 20  # 三大法人/融資融券回看交易日數（約1個月）
KLINE_DISPLAY_DAYS = 50  # K線顯示天數（約10週，指標計算仍用完整6個月資料）

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
HOLDERS_MARKET_CACHE = os.path.join(CACHE_DIR, "holders_market.csv")
REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
HOLDERS_WEEKS = 8  # 大戶持股比例趨勢顯示週數
HOLDERS_BACKFILL_WEEKS = 3  # 快取不足時，額外回補的過去週數(一次性，補齊後不再重複查詢)
DEFAULT_BATCH_WORKERS = 3
MAX_BATCH_WORKERS = 4
_chart_render_lock = threading.Lock()


def find_existing_report_dir(sid):
    """在整個 reports/ (含用 reports_manager 手動分類過的子資料夾)裡找這檔股票現有報表
    在哪個資料夾，重新產生時才會存回原本的位置，而不是每次都固定丟回根目錄——不然使用者
    手動分類過的舊版報表會變孤兒留在原地，根目錄又多一份新產生的重複資料。
    """
    matches = glob.glob(os.path.join(REPORTS_DIR, "**", f"{sid}_*.html"), recursive=True)
    if not matches:
        return REPORTS_DIR
    matches.sort(key=os.path.getmtime, reverse=True)
    return os.path.dirname(matches[0])

def load_cache(sid):
    """讀取本地已存的三大法人/融資融券/大戶持股資料（依日期快取，過去資料不會變動可安心重用）"""
    path = os.path.join(CACHE_DIR, f"{sid}.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                data.setdefault("inst", {})
                data.setdefault("margin", {})
                data.setdefault("holders", {})
                data.setdefault("market", {})
                return data
        except Exception:
            pass
    return {"inst": {}, "margin": {}, "holders": {}, "market": {}}

def save_cache(sid, cache):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(os.path.join(CACHE_DIR, f"{sid}.json"), "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def _latest_expected_friday():
    d = datetime.today()
    while d.weekday() != 4:
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")

_holders_market_memory = {}
_holders_market_lock = threading.Lock()

def fetch_holders_market():
    with _holders_market_lock:
        return _fetch_holders_market_locked()

def _fetch_holders_market_locked():
    """下載/讀取全市場集保戶股權分散表(每週五更新一次，同一週內重複查詢不會重新下載)"""
    expected = _latest_expected_friday()

    if expected in _holders_market_memory:
        return _holders_market_memory[expected]

    if os.path.exists(HOLDERS_MARKET_CACHE):
        try:
            with open(HOLDERS_MARKET_CACHE, "r", encoding="utf-8-sig") as f:
                lines = f.read().splitlines()
            if len(lines) > 1 and lines[1].split(",")[0].strip() == expected:
                _holders_market_memory[expected] = lines
                return lines
        except Exception:
            pass

    try:
        r = requests.get("https://opendata.tdcc.com.tw/getOD.ashx?id=1-5", headers=HEADERS, timeout=30)
        if r.status_code == 200 and r.text.strip():
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(HOLDERS_MARKET_CACHE, "w", encoding="utf-8") as f:
                f.write(r.text)
            lines = r.text.splitlines()
            _holders_market_memory[expected] = lines
            return lines
    except Exception:
        pass

    if os.path.exists(HOLDERS_MARKET_CACHE):
        try:
            with open(HOLDERS_MARKET_CACHE, "r", encoding="utf-8-sig") as f:
                lines = f.read().splitlines()
            _holders_market_memory[expected] = lines
            return lines
        except Exception:
            pass
    return []

def _build_holders_snapshot(levels, date, total_level_idx):
    """依持股分級(12=400張+,13=600張+,14=800張+,15=1000張+)彙總大戶/散戶比例"""
    def pct_sum(*idxs):
        return round(sum(levels.get(i, {}).get("pct", 0.0) for i in idxs), 2)

    big400 = pct_sum(12, 13, 14, 15)
    big600 = pct_sum(13, 14, 15)
    big800 = pct_sum(14, 15)
    big1000 = pct_sum(15)
    retail = round(100 - big400, 2)
    total_holders = levels.get(total_level_idx, {}).get("count", 0)
    return {
        "date": date,
        "big400_pct": big400, "big600_pct": big600, "big800_pct": big800, "big1000_pct": big1000,
        "retail_pct": retail, "total_holders": total_holders,
    }

def parse_holders_row(lines, sid):
    """從集保戶股權分散表(全市場CSV，僅最新一期)篩出目標股票的持股分級"""
    levels = {}
    date = None
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) < 6 or parts[1].strip() != sid:
            continue
        date = parts[0].strip()
        try:
            levels[int(parts[2])] = {"count": parse_int(parts[3]), "pct": float(parts[5])}
        except Exception:
            continue

    if not date:
        return None
    return _build_holders_snapshot(levels, date, total_level_idx=17)

def parse_holders_html(html, date):
    """解析集保結算所查詢頁面回傳的個股持股分級HTML表格"""
    rows = re.findall(
        r'<td align="center">(\d+)</td>\s*<td align="center">[^<]*</td>\s*'
        r'<td align="right">([\d,]+)</td>\s*<td align="right">[\d,]+</td>\s*<td align="right">([\d.]+)</td>',
        html,
    )
    levels = {}
    for idx_str, count_str, pct_str in rows:
        try:
            levels[int(idx_str)] = {"count": parse_int(count_str), "pct": float(pct_str)}
        except Exception:
            continue
    if not levels:
        return None
    return _build_holders_snapshot(levels, date, total_level_idx=16)

def fetch_holders_history_html(sid, dates):
    """透過集保結算所查詢頁面(session+CSRF token)一次性補抓過去週別的大戶持股資料"""
    results = {}
    try:
        s = requests.Session()
        r1 = s.get("https://www.tdcc.com.tw/portal/zh/smWeb/qryStock", headers=HEADERS, timeout=15)
        token_m = re.search(r'name="SYNCHRONIZER_TOKEN"\s+value="([^"]+)"', r1.text)
        available = sorted(set(re.findall(r'value="(\d{8})"', r1.text)))
        if not token_m or not available:
            return results
        token = token_m.group(1)

        for d in dates:
            if d not in available:
                continue
            payload = {
                "SYNCHRONIZER_TOKEN": token,
                "SYNCHRONIZER_URI": "/portal/zh/smWeb/qryStock",
                "scaDate": d, "stockNo": sid, "stockName": "",
                "sqlMethod": "StockNo", "method": "submit", "firDate": available[0],
            }
            try:
                r2 = s.post("https://www.tdcc.com.tw/portal/zh/smWeb/qryStock", data=payload, headers=HEADERS, timeout=15)
                if r2.status_code == 200:
                    snap = parse_holders_html(r2.text, d)
                    if snap:
                        results[d] = snap
                    # 每次回應會換發新token，下一次請求要用新的，否則會拿到異常內容
                    new_token_m = re.search(r'name="SYNCHRONIZER_TOKEN"\s+value="([^"]+)"', r2.text)
                    if new_token_m:
                        token = new_token_m.group(1)
            except Exception:
                pass
            time.sleep(0.8)
    except Exception:
        pass
    return results

# ── 技術指標 ──────────────────────────────────────────
def rsi(s, n=14):
    d = s.diff()
    g = d.clip(lower=0).ewm(com=n-1, min_periods=n).mean()
    l = (-d.clip(upper=0)).ewm(com=n-1, min_periods=n).mean()
    return (100 - 100/(1+g/l)).round(1)

def macd(s):
    e12 = s.ewm(span=12, adjust=False).mean()
    e26 = s.ewm(span=26, adjust=False).mean()
    m = e12 - e26
    sig = m.ewm(span=9, adjust=False).mean()
    return m.round(3), sig.round(3), (m-sig).round(3)

def kdj(h, l, c, n=9):
    lo = l.rolling(n).min()
    hi = h.rolling(n).max()
    rsv = (c-lo)/(hi-lo).replace(0, np.nan)*100
    k = rsv.ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    return k.round(1), d.round(1), (3*k-2*d).round(1)

def bollinger(s, n=20, k=2):
    mid = s.rolling(n).mean()
    std = s.rolling(n).std()
    return (mid + k*std).round(2), mid.round(2), (mid - k*std).round(2)

def build_chart(sid, name, df, ma5, ma10, ma20, ma60, bu, bm, bl, n_tail, out_path):
    """畫K線圖(台股慣例：紅漲綠跌)，含均線/布林通道/成交量，供AI視覺判讀型態用"""
    plot_df = df.tail(n_tail)

    marketcolors = mpf.make_marketcolors(
        up="#e04444", down="#3aa65a", edge="inherit", wick="inherit", volume="inherit"
    )
    style = mpf.make_mpf_style(
        base_mpf_style="nightclouds", marketcolors=marketcolors, gridstyle=":",
        rc={"font.sans-serif": ["Microsoft JhengHei", "Microsoft YaHei", "SimHei"], "axes.unicode_minus": False},
    )

    addplots = [
        mpf.make_addplot(ma5.tail(n_tail), color="#e0c46a", width=1.1),
        mpf.make_addplot(ma10.tail(n_tail), color="#7fb3ff", width=1.1),
        mpf.make_addplot(ma20.tail(n_tail), color="#c58fff", width=1.1),
        mpf.make_addplot(ma60.tail(n_tail), color="#999999", width=1.1),
        mpf.make_addplot(bu.tail(n_tail), color="#666666", width=0.8, linestyle="--"),
        mpf.make_addplot(bl.tail(n_tail), color="#666666", width=0.8, linestyle="--"),
    ]

    try:
        mpf.plot(
            plot_df, type="candle", style=style, addplot=addplots, volume=True,
            title=f"\n{sid} {name}", figsize=(12, 7),
            savefig=dict(fname=out_path, dpi=140, bbox_inches="tight"),
        )
        return True
    except Exception:
        return False

# ── 共用工具 ──────────────────────────────────────────
_twse_response_cache = {}
_twse_response_lock = threading.Lock()

def _twse_cache_key(url, params):
    return (url, json.dumps(params, ensure_ascii=False, sort_keys=True, default=str))

def twse_response_cached(url, params):
    return _twse_cache_key(url, params) in _twse_response_cache

def twse_get(url, params):
    with _twse_response_lock:
        return _twse_get_locked(url, params)

def _twse_get_locked(url, params):
    # 法人與融資端點回傳的是「當日全市場」資料。批次更新時同一日期只下載一次，
    # 後續個股直接共用記憶體回應，避免 35 檔上市股重複抓取完全相同的內容。
    cache_key = _twse_cache_key(url, params)
    if cache_key in _twse_response_cache:
        return _twse_response_cache[cache_key]
    for _ in range(2):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=12)
            if r.status_code == 200 and r.text.strip():
                data = r.json()
                _twse_response_cache[cache_key] = data
                return data
        except:
            pass
        time.sleep(1.2)
    return None

_otc_company_names_cache = None
_otc_company_names_lock = threading.Lock()

def fetch_otc_company_names():
    with _otc_company_names_lock:
        return _fetch_otc_company_names_locked()

def _fetch_otc_company_names_locked():
    """抓上櫃(TPEx)全市場代號->中文公司名稱對照表，一次抓整份、快取在記憶體裡整個
    process 共用(同一次執行如果跑多檔上櫃股票，不用每檔都重新下載一次)。

    原本用的 `tpex.org.tw/zh/api/codeQuery` 這個端點已經失效了——會回 200 但內容其實是
    TPEx 自己的 404 錯誤頁面(不是真的查無資料，是網址本身跳掉了)，導致解析 JSON 失敗、
    被 except 吃掉，最後整個退回用 yfinance 的英文公司全名，這就是上櫃股常常抓到英文
    名稱、對不上一般認知台股名稱的真正原因。改用這個還在正常運作的 TPEx open data
    每日收盤行情端點，裡面本來就含全市場代號+中文名稱，穩定得多。
    """
    global _otc_company_names_cache
    if _otc_company_names_cache is not None:
        return _otc_company_names_cache
    names = {}
    try:
        otc_hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        r = requests.get(
            "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
            headers=otc_hdrs, timeout=15, verify=False
        )
        if r.status_code == 200:
            for row in r.json():
                code = row.get("SecuritiesCompanyCode")
                cname = row.get("CompanyName")
                if code and cname:
                    names[code] = cname
    except Exception:
        pass
    _otc_company_names_cache = names
    return names

def resolve_by_name(query):
    """用中文股票名稱查詢代號（TWSE codeQuery 同時涵蓋上市與上櫃）"""
    try:
        res = requests.get(
            "https://www.twse.com.tw/zh/api/codeQuery",
            params={"query": query}, headers=HEADERS, timeout=8
        )
        if res.status_code != 200:
            return None, None
        suggestions = res.json().get("suggestions", [])
    except Exception:
        return None, None

    candidates = []
    for sug in suggestions:
        parts = sug.split("\t")
        if len(parts) < 2:
            continue
        code, name = parts[0].strip(), parts[1].strip()
        if code and code[:1].isdigit():
            candidates.append((code, name))

    for code, name in candidates:
        if name == query:
            return code, name
    for code, name in candidates:
        if query in name:
            return code, name
    return None, None

def trading_dates(n=10):
    dates, d = [], datetime.today()
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d.strftime("%Y%m%d"))
        d -= timedelta(days=1)
    return dates

def fmt_date(d):
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"

def to_roc_date(d):
    return f"{int(d[:4])-1911}/{d[4:6]}/{d[6:8]}"

def parse_int(v):
    try:
        return int(str(v).replace(",", "").replace("+", "").replace(" ", "") or 0)
    except:
        return 0

def to_lot(v):
    return round(parse_int(v) / 1000)

def short_mmdd(dt_obj):
    return f"{dt_obj.month}/{dt_obj.day}"

def short_mmdd_file(dt_obj):
    return f"{dt_obj.month:02d}{dt_obj.day:02d}"

def parse_roc_slash_date(text):
    try:
        y, m, d = str(text).strip().split("/")
        return datetime(int(y) + 1911, int(m), int(d))
    except:
        return None

def extract_disposition_period(text):
    if not text:
        return None, None

    s = str(text).strip()
    s = s.replace("至", "~").replace("－", "-").replace("—", "-").replace("–", "-").replace("～", "~")

    m1 = re.search(r'(\d{2,3}/\d{1,2}/\d{1,2})\s*[~\-]\s*(\d{2,3}/\d{1,2}/\d{1,2})', s)
    if m1:
        d1 = parse_roc_slash_date(m1.group(1))
        d2 = parse_roc_slash_date(m1.group(2))
        return d1, d2

    m2 = re.search(r'(\d{2,3}/\d{1,2}/\d{1,2})\s*[~\-]\s*(\d{1,2}/\d{1,2})', s)
    if m2:
        d1 = parse_roc_slash_date(m2.group(1))
        if d1:
            try:
                mm, dd = m2.group(2).split("/")
                d2 = datetime(d1.year, int(mm), int(dd))
                return d1, d2
            except:
                pass

    return None, None

def sanitize_filename(text):
    text = str(text).strip()
    for ch in r'\/:*?"<>|':
        text = text.replace(ch, "_")
    text = text.replace("\n", "_").replace("\r", "_").replace("\t", "_")
    return text.strip(" ._")

def esc(v):
    return (str(v).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))

# ── HTML 報表產生 ──────────────────────────────────────
HTML_STYLE = """
body { font-family: "Segoe UI", "Microsoft JhengHei", sans-serif; background:#0f1115; color:#e6e6e6; margin:0; padding:24px; }
.wrap { max-width: 960px; margin: 0 auto; }
h1 { font-size: 1.4em; margin-bottom: 4px; }
.sub { color:#9aa0a6; margin-bottom: 20px; }
.card { background:#1a1d24; border:1px solid #2a2e37; border-radius:8px; padding:16px 20px; margin-bottom:16px; }
.card h2 { font-size:1.05em; margin:0 0 10px 0; color:#7fb3ff; border-bottom:1px solid #2a2e37; padding-bottom:6px; }
.badge { display:inline-block; padding:2px 10px; border-radius:12px; font-weight:bold; font-size:0.9em; }
.badge.warn { background:#4a2a2a; color:#ff8080; }
.badge.ok { background:#1f3a2a; color:#7ee787; }
table { border-collapse: collapse; width:100%; font-size:0.9em; }
th, td { padding:5px 8px; text-align:right; border-bottom:1px solid #23262e; white-space:nowrap; }
th:first-child, td:first-child { text-align:left; }
th { color:#9aa0a6; font-weight:normal; }
/* 台股配色：正值／買超為紅，負值／賣超為綠。 */
.pos { color:#ff8080; }
.neg { color:#7ee787; }
.kv { display:flex; flex-wrap:wrap; gap:8px 24px; }
.kv div { min-width:120px; }
.kv b { color:#9aa0a6; font-weight:normal; }
.ma-order { font-family: Consolas, monospace; color:#e0c46a; }
footer { color:#666; font-size:0.8em; text-align:center; margin-top:24px; }
"""

def num_span(v, fmt="{:+,}"):
    cls = "pos" if v > 0 else ("neg" if v < 0 else "")
    return f'<span class="{cls}">{fmt.format(v)}</span>'

def build_html(sid, name, dispo, info, df, ma5, ma10, ma20, ma60, ri, mc, ms, mh, K, D, J, bu, bm, bl, inst, mg, holders_hist=None, chart_filename=None):
    last = -1
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M')

    parts = []
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    w52h, w52l = info.get("fiftyTwoWeekHigh"), info.get("fiftyTwoWeekLow")
    pe, pb, dy, mcap = info.get("trailingPE"), info.get("priceToBook"), info.get("dividendYield"), info.get("marketCap")
    if price: parts.append(("價", f"{price:.1f}"))
    if w52h and w52l: parts.append(("52週高/低", f"{w52h:.1f} / {w52l:.1f}"))
    if pe: parts.append(("PE", f"{pe:.1f}"))
    if pb: parts.append(("PB", f"{pb:.2f}"))
    if dy: parts.append(("殖利率", f"{dy:.1f}%"))
    if mcap: parts.append(("市值", f"{mcap/1e8:.0f}億"))
    basic_html = "".join(f'<div><b>{esc(k)}：</b>{esc(v)}</div>' for k, v in parts)

    dispo_badge = ""
    if dispo["is_disposition"]:
        dispo_badge = f'<span class="badge warn">⚠ 處置中 {esc(short_mmdd(dispo["start"]))}~{esc(short_mmdd(dispo["end"]))}</span>'
    elif dispo["start"]:
        dispo_badge = f'<span class="badge ok">非處置期（最近公告 {esc(short_mmdd(dispo["start"]))}~{esc(short_mmdd(dispo["end"]))}）</span>'
    else:
        dispo_badge = '<span class="badge ok">處置期間：否</span>'

    tech_html = ""
    kline_rows_html = ""
    if not df.empty:
        ma_dict = {"MA5": ma5.iloc[last], "MA10": ma10.iloc[last], "MA20": ma20.iloc[last], "MA60": ma60.iloc[last]}
        order = ">".join(k for k, _ in sorted(ma_dict.items(), key=lambda x: -x[1]))
        tech_html = f"""
        <div class="kv" style="margin-bottom:10px;">
            <div><b>MA5：</b>{ma5.iloc[last]:.2f}</div>
            <div><b>MA10：</b>{ma10.iloc[last]:.2f}</div>
            <div><b>MA20：</b>{ma20.iloc[last]:.2f}</div>
            <div><b>MA60：</b>{ma60.iloc[last]:.2f}</div>
            <div><b>RSI：</b>{ri.iloc[last]}</div>
            <div><b>MACD：</b>{mc.iloc[last]} / Signal {ms.iloc[last]} / Hist {mh.iloc[last]}</div>
            <div><b>KDJ：</b>K={K.iloc[last]} D={D.iloc[last]} J={J.iloc[last]}</div>
            <div><b>BOLL：</b>上={bu.iloc[last]:.2f} 中={bm.iloc[last]:.2f} 下={bl.iloc[last]:.2f}</div>
        </div>
        <div><b>均線排列：</b><span class="ma-order">{esc(order)}</span></div>
        """

        n_tail = min(KLINE_DISPLAY_DAYS, len(df))
        tail = df.tail(n_tail)
        rows = []
        for i, (dt, row) in enumerate(tail.iterrows()):
            idx = -(n_tail - i)
            rows.append(
                f"<tr><td>{dt.strftime('%m/%d')}</td><td>{row.Open:.1f}</td><td>{row.High:.1f}</td>"
                f"<td>{row.Low:.1f}</td><td>{row.Close:.1f}</td><td>{row.Volume/1e6:.1f}M</td>"
                f"<td>{ma5.iloc[idx]:.1f}</td><td>{ri.iloc[idx]}</td><td>{mh.iloc[idx]}</td>"
                f"<td>{K.iloc[idx]}</td><td>{D.iloc[idx]}</td>"
                f"<td>{bu.iloc[idx]:.1f}</td><td>{bm.iloc[idx]:.1f}</td><td>{bl.iloc[idx]:.1f}</td></tr>"
            )
        kline_rows_html = f"""
        <table>
            <tr><th>日期</th><th>開</th><th>高</th><th>低</th><th>收</th><th>量</th>
                <th>MA5</th><th>RSI</th><th>MACD_hist</th><th>K</th><th>D</th>
                <th>BOLL上</th><th>BOLL中</th><th>BOLL下</th></tr>
            {''.join(rows)}
        </table>
        """

    inst_html = ""
    if inst:
        rows = [
            f"<tr><td>{r['dt']}</td><td>{num_span(r['f'])}</td><td>{num_span(r['tr'])}</td>"
            f"<td>{num_span(r['dl'])}</td><td>{num_span(r['sm'])}</td></tr>"
            for r in inst
        ]
        tf = sum(r['f'] for r in inst)
        tt = sum(r['tr'] for r in inst)
        td_ = sum(r['dl'] for r in inst)
        rows.append(
            f"<tr style='font-weight:bold;border-top:2px solid #2a2e37;'><td>{DAYS_LOOKBACK}日累計</td>"
            f"<td>{num_span(tf)}</td><td>{num_span(tt)}</td><td>{num_span(td_)}</td><td>{num_span(tf+tt+td_)}</td></tr>"
        )
        inst_html = f"""
        <table>
            <tr><th>日期</th><th>外資</th><th>投信</th><th>自營</th><th>合計</th></tr>
            {''.join(rows)}
        </table>
        """

    margin_html = ""
    if mg:
        rows = [
            f"<tr><td>{r['dt']}</td><td>{r['mb']:,}</td><td>{num_span(r['md'])}</td>"
            f"<td>{r['sb']:,}</td><td>{num_span(r['sd'])}</td></tr>"
            for r in mg
        ]
        margin_html = f"""
        <table>
            <tr><th>日期</th><th>融資餘額</th><th>融資增減</th><th>融券餘額</th><th>融券增減</th></tr>
            {''.join(rows)}
        </table>
        """

    holders_html = ""
    if holders_hist:
        rows = [
            f"<tr><td>{fmt_date(d)}</td><td>{h.get('big400_pct', 0)}%</td><td>{h.get('big600_pct', 0)}%</td>"
            f"<td>{h.get('big800_pct', 0)}%</td><td>{h.get('big1000_pct', 0)}%</td>"
            f"<td>{h.get('retail_pct', 0)}%</td><td>{h.get('total_holders', 0):,}</td></tr>"
            for d, h in holders_hist
        ]
        holders_html = f"""
        <table>
            <tr><th>日期</th><th>400張以上</th><th>600張以上</th><th>800張以上</th>
                <th>1000張以上</th><th>散戶(&lt;400張)</th><th>集保戶數</th></tr>
            {''.join(rows)}
        </table>
        """

    html = f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<title>{esc(sid)} {esc(name)} 台股分析</title>
<style>{HTML_STYLE}</style>
</head>
<body>
<div class="wrap">
    <h1>{esc(sid)} {esc(name)} 台股分析</h1>
    <div class="sub">產生時間：{esc(now_str)}</div>

    <div class="card">
        <h2>基本資訊</h2>
        <div style="margin-bottom:10px;">{dispo_badge}</div>
        <div class="kv">{basic_html}</div>
    </div>

    {f'''<div class="card">
        <h2>K線圖</h2>
        <img src="{esc(chart_filename)}" alt="K線圖" style="max-width:100%;border-radius:6px;">
    </div>''' if chart_filename else ''}

    <div class="card">
        <h2>技術指標</h2>
        {tech_html or '<div>無K線資料</div>'}
    </div>

    <div class="card">
        <h2>近{KLINE_DISPLAY_DAYS}日K線</h2>
        {kline_rows_html or '<div>無資料</div>'}
    </div>

    <div class="card">
        <h2>三大法人近{DAYS_LOOKBACK}日（單位：張）</h2>
        {inst_html or '<div>無資料</div>'}
    </div>

    <div class="card">
        <h2>融資融券近{DAYS_LOOKBACK}日（單位：張）</h2>
        {margin_html or '<div>無資料</div>'}
    </div>

    <div class="card">
        <h2>大戶持股比例（集保週報，近{len(holders_hist or [])}週）</h2>
        {holders_html or '<div>無資料</div>'}
    </div>

    <footer>台股分析工具自動產生</footer>
</div>
</body>
</html>"""
    return html

# ── 處置期間查詢 (上市/上櫃) ──────────────────────────
def fetch_disposition_info(sid, is_otc=False):
    today = datetime.today().date()

    result = {
        "is_disposition": False,
        "start": None,
        "end": None,
        "display_report": "",
        "display_file": "",
        "report_line": "處置期間：否",
        "raw": ""
    }

    if not is_otc:
        candidates = [
            ("https://www.twse.com.tw/rwd/zh/announcement/punish", {"response": "json", "startDate": "", "endDate": "", "stockNo": sid}),
            ("https://www.twse.com.tw/zh/announcement/punish.html", {"startDate": "", "endDate": "", "stockNo": sid}),
        ]

        for url, params in candidates:
            try:
                r = requests.get(url, params=params, headers=HEADERS, timeout=12)
                txt = r.text.strip()
                if r.status_code != 200 or not txt:
                    continue

                try:
                    data = r.json()
                except:
                    data = None

                rows = []
                if isinstance(data, dict):
                    if isinstance(data.get("data"), list):
                        rows = data.get("data", [])
                    elif isinstance(data.get("tables"), list):
                        for t in data.get("tables", []):
                            if isinstance(t, dict) and isinstance(t.get("data"), list):
                                rows.extend(t.get("data", []))

                for row in rows:
                    if not isinstance(row, list) or len(row) < 8:
                        continue
                    code = str(row[2]).strip() if len(row) > 2 else ""
                    if code != sid:
                        continue

                    period_text = str(row[6]).strip() if len(row) > 6 else ""
                    d1, d2 = extract_disposition_period(period_text)
                    if d1 and d2:
                        result["raw"] = period_text
                        result["start"] = d1
                        result["end"] = d2
                        if d1.date() <= today <= d2.date():
                            result["is_disposition"] = True
                        break

                if result["start"] and result["end"]:
                    break

                if sid in txt:
                    pattern = rf"{sid}.*?(\d{{2,3}}/\d{{1,2}}/\d{{1,2}}\s*[~～\-]\s*\d{{2,3}}/\d{{1,2}}/\d{{1,2}})"
                    m = re.search(pattern, txt, re.S)
                    if m:
                        d1, d2 = extract_disposition_period(m.group(1))
                        if d1 and d2:
                            result["raw"] = m.group(1)
                            result["start"] = d1
                            result["end"] = d2
                            if d1.date() <= today <= d2.date():
                                result["is_disposition"] = True
                            break
            except:
                pass
            time.sleep(0.6)

    else:
        candidates = [
            ("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_disposal_securities_information", None),
            ("https://www.tpex.org.tw/openapi/v1/tpex_esb_disposal_securities_information", None),
            ("https://www.tpex.org.tw/web/bulletin/disposal_information/disposal_information_result.php", {"l": "zh-tw", "o": "json", "stkno": sid}),
        ]

        otc_hdrs = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        for url, params in candidates:
            try:
                r = requests.get(url, params=params, headers=otc_hdrs, timeout=12, verify=False)
                txt = r.text.strip()
                if r.status_code != 200 or not txt:
                    continue

                try:
                    data = r.json()
                except:
                    data = None

                rows = []
                if isinstance(data, list):
                    rows = data
                elif isinstance(data, dict):
                    if isinstance(data.get("data"), list):
                        rows = data.get("data", [])
                    elif isinstance(data.get("tables"), list):
                        for t in data.get("tables", []):
                            if isinstance(t, dict) and isinstance(t.get("data"), list):
                                rows.extend(t.get("data", []))

                for row in rows:
                    code = ""
                    period_text = ""

                    if isinstance(row, dict):
                        for k in row.keys():
                            ks = str(k)
                            if ("證券代號" in ks) or ("SecuritiesCompanyCode" in ks) or ("股票代號" in ks):
                                code = str(row[k]).strip()
                            if ("處置起訖時間" in ks) or ("處置起迄時間" in ks) or ("處置期間" in ks):
                                period_text = str(row[k]).strip()
                    elif isinstance(row, list):
                        if len(row) >= 4:
                            code = str(row[1]).strip() if len(row) > 1 else ""
                            period_text = str(row[3]).strip() if len(row) > 3 else ""

                    if code != sid:
                        continue

                    d1, d2 = extract_disposition_period(period_text)
                    if d1 and d2:
                        result["raw"] = period_text
                        result["start"] = d1
                        result["end"] = d2
                        if d1.date() <= today <= d2.date():
                            result["is_disposition"] = True
                        break

                if result["start"] and result["end"]:
                    break

                if sid in txt:
                    pattern = rf"{sid}.*?(\d{{2,3}}/\d{{1,2}}/\d{{1,2}}\s*[~～\-]\s*\d{{2,3}}/\d{{1,2}}/\d{{1,2}})"
                    m = re.search(pattern, txt, re.S)
                    if m:
                        d1, d2 = extract_disposition_period(m.group(1))
                        if d1 and d2:
                            result["raw"] = m.group(1)
                            result["start"] = d1
                            result["end"] = d2
                            if d1.date() <= today <= d2.date():
                                result["is_disposition"] = True
                            break
            except:
                pass
            time.sleep(0.6)

    if result["start"] and result["end"]:
        sx = short_mmdd(result["start"])
        ex = short_mmdd(result["end"])
        sx_file = short_mmdd_file(result["start"])
        ex_file = short_mmdd_file(result["end"])

        result["display_report"] = f"(處置期間{sx}~{ex})"
        result["display_file"] = f"(處置期間{sx_file}-{ex_file})"

        if result["is_disposition"]:
            result["report_line"] = f"⚠ 處置期間：是（{sx} ~ {ex}）"
        else:
            result["report_line"] = f"處置期間：否（最近公告區間 {sx} ~ {ex}，目前不在期間內）"

    return result

# ── 三大法人 (支援上市/上櫃) ──────────────────────────
def fetch_inst(sid, dates, is_otc=False, cache=None):
    rows = []
    cache = {} if cache is None else cache
    otc_hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    for d in dates:
        dt_key = fmt_date(d)
        reused_market_response = False
        if dt_key in cache:
            rows.append(cache[dt_key])
            if len(rows) >= DAYS_LOOKBACK:
                break
            continue

        if not is_otc:
            url = "https://www.twse.com.tw/rwd/zh/fund/T86"
            params = {"response": "json", "date": d, "selectType": "ALLBUT0999"}
            reused_market_response = twse_response_cached(url, params)
            data = twse_get(url, params)
            if not data or data.get("stat") != "OK":
                continue
            for row in data.get("data", []):
                if row[0].strip() != sid:
                    continue
                f_net = to_lot(row[4]) if len(row) > 4 else 0
                tr_net = to_lot(row[10]) if len(row) > 10 else 0
                dl_net = to_lot(row[11]) if len(row) > 11 else 0
                sm_net = to_lot(row[18]) if len(row) > 18 else (f_net + tr_net + dl_net)
                rec = {"dt": dt_key, "f": f_net, "tr": tr_net, "dl": dl_net, "sm": sm_net}
                rows.append(rec)
                cache[dt_key] = rec
                break
        else:
            url = "https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php"
            try:
                r = requests.get(url, params={"l": "zh-tw", "o": "json", "se": "AL", "t": "D", "d": to_roc_date(d)},
                                 headers=otc_hdrs, timeout=10, verify=False)
                r.encoding = 'utf-8'
                data = r.json() if r.status_code == 200 else {}
            except:
                data = {}

            data_list = data.get("tables", [])
            if not data_list:
                continue
            rows_data = data_list[0].get("data", [])

            for row in rows_data:
                if row[0].strip() != sid:
                    continue
                rec = {"dt": dt_key, "f": to_lot(row[10]), "tr": to_lot(row[13]), "dl": to_lot(row[22]), "sm": to_lot(row[23])}
                rows.append(rec)
                cache[dt_key] = rec
                break

        if len(rows) >= DAYS_LOOKBACK:
            break
        if not reused_market_response:
            time.sleep(0.8)

    return rows[:DAYS_LOOKBACK]

# ── 融資融券 (支援上市/上櫃) ──────────────────────────
def fetch_margin(sid, dates, is_otc=False, cache=None):
    rows = []
    cache = {} if cache is None else cache
    otc_hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    for d in dates:
        dt_key = fmt_date(d)
        reused_market_response = False
        if dt_key in cache:
            rows.append(cache[dt_key])
            if len(rows) >= DAYS_LOOKBACK:
                break
            continue

        if not is_otc:
            url = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN"
            params = {"response": "json", "date": d, "selectType": "ALL"}
            reused_market_response = twse_response_cached(url, params)
            data = twse_get(url, params)
            if not data or data.get("stat") != "OK":
                continue

            target_row = None
            tables = data.get("tables", [{"data": data.get("data", [])}])
            for t in tables:
                for row in t.get("data", []):
                    if len(row) >= 13 and str(row[0]).strip() == sid:
                        target_row = row
                        break
                if target_row:
                    break

            if target_row:
                mb_prev, mb_today = parse_int(target_row[5]), parse_int(target_row[6])
                sb_prev, sb_today = parse_int(target_row[11]), parse_int(target_row[12])
                rec = {"dt": dt_key, "mb": mb_today, "md": mb_today - mb_prev, "sb": sb_today, "sd": sb_today - sb_prev}
                rows.append(rec)
                cache[dt_key] = rec

        else:
            url = "https://www.tpex.org.tw/web/stock/margin_trading/margin_balance/margin_bal_result.php"
            try:
                r = requests.get(url, params={"l": "zh-tw", "o": "json", "d": to_roc_date(d)}, headers=otc_hdrs, timeout=10, verify=False)
                r.encoding = 'utf-8'
                data = r.json() if r.status_code == 200 else {}
            except:
                data = {}

            data_list = data.get("tables", [])
            if not data_list:
                continue
            rows_data = data_list[0].get("data", [])

            for row in rows_data:
                if len(row) >= 15 and str(row[0]).strip() == sid:
                    mb_prev, mb_today = parse_int(row[2]) // 1000, parse_int(row[6]) // 1000
                    sb_prev, sb_today = parse_int(row[10]) // 1000, parse_int(row[14]) // 1000
                    rec = {"dt": dt_key, "mb": mb_today, "md": mb_today - mb_prev, "sb": sb_today, "sd": sb_today - sb_prev}
                    rows.append(rec)
                    cache[dt_key] = rec
                    break

        if len(rows) >= DAYS_LOOKBACK:
            break
        if not reused_market_response:
            time.sleep(0.8)

    return rows[:DAYS_LOOKBACK]

# ── 主程式 ────────────────────────────────────────────
def run(ticker_input):
    raw = ticker_input.strip()
    sid = raw.upper().replace(".TW", "").replace(".TWO", "")

    if not re.fullmatch(r'[0-9A-Z]+', sid):
        print(f" 依名稱「{raw}」查詢代碼…", end="", flush=True)
        code, matched_name = resolve_by_name(raw)
        if not code:
            print(" ❌ 查無符合的股票名稱，請確認名稱或直接輸入代碼。\n")
            return False
        print(f" ✅ {code} {matched_name}")
        sid = code

    dates = trading_dates(DAYS_LOOKBACK * 2)
    cache = load_cache(sid)
    lines = []
    add = lines.append

    print(" 抓取K線…", end="", flush=True)

    import logging
    logging.getLogger('yfinance').setLevel(logging.CRITICAL)

    df = pd.DataFrame()
    today_key = datetime.now().strftime("%Y-%m-%d")
    market_cache = cache.get("market", {})
    info = dict(market_cache.get("info") or {}) if market_cache.get("date") == today_key else {}
    symbol = sid + ".TW"

    try:
        symbol = sid + ".TW"
        stok = yf.Ticker(symbol)
        df = stok.history(period="6mo", interval="1d", auto_adjust=True)
        if not df.empty and not info:
            info = stok.info or {}
    except Exception:
        pass

    if df.empty:
        try:
            symbol = sid + ".TWO"
            stok = yf.Ticker(symbol)
            df = stok.history(period="6mo", interval="1d", auto_adjust=True)
            if not df.empty and not info:
                info = stok.info or {}
        except Exception:
            pass

    if df.empty:
        print(" ❌ 失敗 (查無此代碼或已下市)，停止後續動作。\n")
        return False

    print(f" ✅ OK (成功抓取: {symbol})")

    name = None
    is_otc = symbol.endswith(".TWO")

    if not is_otc:
        try:
            res = requests.get(
                "https://www.twse.com.tw/zh/api/codeQuery",
                params={"query": sid}, headers=HEADERS, timeout=5
            )
            if res.status_code == 200:
                for sug in res.json().get("suggestions", []):
                    parts = sug.split("\t")
                    if len(parts) >= 2 and parts[0].strip() == sid:
                        name = parts[1].strip()
                        break
        except Exception:
            pass
    else:
        name = fetch_otc_company_names().get(sid)

    if not name and is_otc:
        try:
            otc_hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            res = requests.get(
                "https://www.tpex.org.tw/web/stock/aftertrading/otc_quotes_no1430/stk_wn1430_result.php",
                params={"l": "zh-tw", "o": "json", "se": "EW", "s": "0,asc", "d": to_roc_date(dates[0])},
                headers=otc_hdrs, timeout=8, verify=False
            )
            if res.status_code == 200:
                data_list = res.json().get("tables", [])
                if data_list:
                    for row in data_list[0].get("data", []):
                        if len(row) >= 2 and str(row[0]).strip() == sid:
                            name = str(row[1]).strip()
                            break
        except Exception:
            pass

    if not name:
        name = info.get("longName") or info.get("shortName") or sid

    print(" 查詢處置期間…", end="", flush=True)
    dispo = fetch_disposition_info(sid, is_otc)
    print(f" {'是' if dispo['is_disposition'] else '否'}")

    add("🎯【AI 專用絕對對照表：嚴禁配錯股名與股號】🎯")
    add(f"👉 股票代號：{sid} 👈")
    add(f"👉 股票名稱：{name} 👈")
    add("--------------------------------------------------")
    add("")

    add(dispo["report_line"])
    add("")

    price = info.get("currentPrice") or info.get("regularMarketPrice")
    w52h = info.get("fiftyTwoWeekHigh")
    w52l = info.get("fiftyTwoWeekLow")
    pe = info.get("trailingPE")
    pb = info.get("priceToBook")
    dy = info.get("dividendYield")
    mcap = info.get("marketCap")

    add(f"=== 台股分析 {sid} {name} {datetime.now().strftime('%Y-%m-%d')} ===")
    add("")

    parts = []
    if price:
        parts.append(f"價={price:.1f}")
    if w52h and w52l:
        parts.append(f"52W高={w52h:.1f}/低={w52l:.1f}")
    if pe:
        parts.append(f"PE={pe:.1f}")
    if pb:
        parts.append(f"PB={pb:.2f}")
    if dy:
        parts.append(f"殖{dy:.1f}%")
    if mcap:
        parts.append(f"市值{mcap/1e8:.0f}億")
    add("[基本] " + " ".join(parts))
    add("")

    ma5 = ma10 = ma20 = ma60 = ri = mc = ms = mh = K = D = J = bu = bm = bl = pd.Series(dtype=float)

    if not df.empty:
        df.index = pd.to_datetime(df.index).tz_localize(None)
        c, h, l, v = df.Close, df.High, df.Low, df.Volume

        ma5 = c.rolling(5).mean()
        ma10 = c.rolling(10).mean()
        ma20 = c.rolling(20).mean()
        ma60 = c.rolling(60).mean()
        ri = rsi(c)
        mc, ms, mh = macd(c)
        K, D, J = kdj(h, l, c)
        bu, bm, bl = bollinger(c)

        last = -1
        add("[技術指標最新]")
        add(f" MA5={ma5.iloc[last]:.2f} MA10={ma10.iloc[last]:.2f} MA20={ma20.iloc[last]:.2f} MA60={ma60.iloc[last]:.2f}")
        add(f" RSI={ri.iloc[last]} MACD={mc.iloc[last]}/Signal={ms.iloc[last]}/Hist={mh.iloc[last]}")
        add(f" K={K.iloc[last]} D={D.iloc[last]} J={J.iloc[last]}")
        add(f" BOLL上={bu.iloc[last]:.2f} 中={bm.iloc[last]:.2f} 下={bl.iloc[last]:.2f}")

        ma_dict = {"MA5": ma5.iloc[last], "MA10": ma10.iloc[last], "MA20": ma20.iloc[last], "MA60": ma60.iloc[last]}
        order = ">".join(k for k, _ in sorted(ma_dict.items(), key=lambda x: -x[1]))
        add(f" 均線排列: {order}")
        add("")

        n_tail = min(KLINE_DISPLAY_DAYS, len(df))
        tail = df.tail(n_tail)
        add(f"[近{KLINE_DISPLAY_DAYS}日K線] 開 高 低 收 量(K張) MA5 RSI MACD_hist K D BOLL上 BOLL中 BOLL下")
        for i, (dt, row) in enumerate(tail.iterrows()):
            idx = -(n_tail - i)
            add(
                f" {dt.strftime('%m/%d')} "
                f"{row.Open:.1f} {row.High:.1f} {row.Low:.1f} {row.Close:.1f} "
                f"{row.Volume/1e6:.1f}M "
                f"{ma5.iloc[idx]:.1f} {ri.iloc[idx]} {mh.iloc[idx]} "
                f"{K.iloc[idx]} {D.iloc[idx]} "
                f"{bu.iloc[idx]:.1f} {bm.iloc[idx]:.1f} {bl.iloc[idx]:.1f}"
            )
        add("")

    print(f" 抓取三大法人…(快取{len(cache['inst'])}筆)", end="", flush=True)
    inst = fetch_inst(sid, dates, is_otc, cache["inst"])
    print(f" {len(inst)}筆")

    if inst:
        add(f"[三大法人近{DAYS_LOOKBACK}日 單位:張] 日期 外資 投信 自營 合計")
        for r in inst:
            add(f" {r['dt']} {r['f']:+,} {r['tr']:+,} {r['dl']:+,} {r['sm']:+,}")
        tf = sum(r['f'] for r in inst)
        tt = sum(r['tr'] for r in inst)
        td = sum(r['dl'] for r in inst)
        add(f" {DAYS_LOOKBACK}日累計 外資{tf:+,} 投信{tt:+,} 自營{td:+,} 合計{tf+tt+td:+,}")
        add("")

    print(f" 抓取融資融券…(快取{len(cache['margin'])}筆)", end="", flush=True)
    mg = fetch_margin(sid, dates, is_otc, cache["margin"])
    print(f" {len(mg)}筆")

    if mg:
        add(f"[融資融券近{DAYS_LOOKBACK}日 單位:張] 日期 融資餘額 融資增減 融券餘額 融券增減")
        for r in mg:
            add(f" {r['dt']} {r['mb']:,} {r['md']:+,} {r['sb']:,} {r['sd']:+,}")
        add("")

    print(" 查詢大戶持股(集保)…", end="", flush=True)
    holders_lines = fetch_holders_market()
    holder_snap = parse_holders_row(holders_lines, sid)
    if holder_snap:
        cache["holders"][holder_snap["date"]] = holder_snap
        print(f" {holder_snap['date']} 400張以上{holder_snap['big400_pct']}%")
    else:
        print(" 查無資料")

    recent_fridays = []
    fd = datetime.strptime(_latest_expected_friday(), "%Y%m%d")
    for _ in range(HOLDERS_BACKFILL_WEEKS + 1):
        recent_fridays.append(fd.strftime("%Y%m%d"))
        fd -= timedelta(days=7)
    missing = [d for d in recent_fridays if d not in cache["holders"]]
    if missing:
        print(f" 回補大戶歷史({len(missing)}週)…", end="", flush=True)
        backfilled = fetch_holders_history_html(sid, missing)
        cache["holders"].update(backfilled)
        print(f" 補到{len(backfilled)}週")

    # Yahoo 的完整 info 端點通常是單檔更新最慢的一步；同一天重跑只沿用報表需要的欄位。
    info_cache_keys = (
        "currentPrice", "regularMarketPrice", "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
        "trailingPE", "priceToBook", "dividendYield", "marketCap", "longName", "shortName",
    )
    cache["market"] = {
        "date": today_key,
        "info": {key: info.get(key) for key in info_cache_keys if info.get(key) is not None},
    }
    save_cache(sid, cache)

    holders_hist = sorted(cache["holders"].items())[-HOLDERS_WEEKS:]
    if holders_hist:
        add(f"[大戶/散戶持股比例 近{len(holders_hist)}週，集保週報] 日期 400+% 600+% 800+% 1000+% 散戶(<400張)% 集保戶數")
        for d, h in holders_hist:
            add(
                f" {fmt_date(d)} {h.get('big400_pct', 0)}% {h.get('big600_pct', 0)}% "
                f"{h.get('big800_pct', 0)}% {h.get('big1000_pct', 0)}% {h.get('retail_pct', 0)}% "
                f"{h.get('total_holders', 0):,}"
            )
        add("")

    add("")
    add("=== END ===")

    output = "\n".join(lines)

    market_tag = "(TWO)" if is_otc else "(TW)"
    dispo_tag = dispo["display_file"] if dispo["is_disposition"] else ""
    safe_name = sanitize_filename(name)

    base_name = sanitize_filename(f"{sid}_{safe_name}{market_tag}{dispo_tag}")
    target_dir = find_existing_report_dir(sid)
    os.makedirs(target_dir, exist_ok=True)
    fname = os.path.join(target_dir, f"{base_name}.html")

    # 出關/名稱變動時清掉這檔股票在 reports/ 裡的舊檔名殘留，並順便清理舊 _chart.png
    for old_path in glob.glob(os.path.join(REPORTS_DIR, "**", f"{sid}_*.html"), recursive=True) + \
                     glob.glob(os.path.join(REPORTS_DIR, "**", f"{sid}_*_chart.png"), recursive=True):
        if os.path.abspath(old_path) != os.path.abspath(fname):
            try:
                os.remove(old_path)
                print(f" 🗑 移除舊檔: {os.path.relpath(old_path, REPORTS_DIR)}")
            except OSError:
                pass

    html = build_html(
        sid, name, dispo, info, df, ma5, ma10, ma20, ma60, ri, mc, ms, mh, K, D, J, bu, bm, bl, inst, mg,
        holders_hist, chart_filename=None,
    )

    with open(fname, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n✅ 已存檔: {fname}\n")
    return True

def parse_tickers(raw):
    """把使用者輸入拆成多支股票代碼/名稱（支援逗號、頓號、空白混合分隔）"""
    parts = re.split(r'[,，、\s]+', raw.strip())
    return [p for p in parts if p]

# ── 追蹤個股清單 (依資料夾內既有 html 報表反查) ──────────
TRACKED_FILENAME_RE = re.compile(r'^([0-9A-Za-z]{2,6})_(.+?)\((TW|TWO)\)')

def scan_tracked_stocks():
    """掃描 reports/ 資料夾(含用 reports_manager 手動分類過的子資料夾)內既有的
    {代號}_{名稱}(TW/TWO).html 報表，反查目前正在追蹤的個股，並依所在子資料夾分類"""
    found = {}
    try:
        for dirpath, _dirnames, filenames in os.walk(REPORTS_DIR):
            category = os.path.relpath(dirpath, REPORTS_DIR)
            category = "" if category == "." else category.replace(os.sep, "/")
            for fn in filenames:
                if not fn.lower().endswith(".html"):
                    continue
                m = TRACKED_FILENAME_RE.match(fn)
                if not m:
                    continue
                sid, name = m.group(1), m.group(2)
                found[sid] = (name, os.path.join(dirpath, fn), category)
    except Exception:
        pass

    def sort_key(item):
        sid, (_name, _path, category) = item
        sid_key = (0, int(sid)) if sid.isdigit() else (1, sid)
        # 根目錄(未分類)排最前面，其餘依資料夾路徑字母排序
        cat_key = (0, "") if category == "" else (1, category)
        return (cat_key, sid_key)

    return [(sid, name, path, category) for sid, (name, path, category) in sorted(found.items(), key=sort_key)]

def file_date_str(path):
    """回傳報表檔最後修改日期 (YYYY-MM-DD)，用來判斷今天是否已經更新過"""
    try:
        return datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d")
    except Exception:
        return None

ALL_TRACKED_INPUT = "999"       # 更新全部追蹤清單，今天已更新過的會跳過
FORCE_ALL_TRACKED_INPUT = "998"  # 強制更新全部追蹤清單，不管今天是否已更新過
FORCE_HOLDERS_UPDATE_INPUT = "997"  # 強制重新下載集保大戶全市場資料(忽略本週快取)

def force_refresh_holders_market():
    """刪除集保大戶(全市場)本地快取並立即重新下載一次"""
    _holders_market_memory.clear()
    if os.path.exists(HOLDERS_MARKET_CACHE):
        try:
            os.remove(HOLDERS_MARKET_CACHE)
        except OSError:
            pass
    print(" 強制重新下載集保大戶資料(全市場)…", end="", flush=True)
    lines = fetch_holders_market()
    print(f" ✅ 完成 ({len(lines)} 行)" if lines else " ❌ 失敗")

def print_tracked_list(tracked):
    today = datetime.now().strftime("%Y-%m-%d")
    print("\n📌 追蹤中個股清單 (依分類資料夾列出，輸入前面的編號即可快速選取，例如輸入 1 就等於輸入代號；")
    print(f"   輸入 {ALL_TRACKED_INPUT} 更新全部追蹤清單，今天已更新過的會自動跳過；")
    print(f"   輸入 {FORCE_ALL_TRACKED_INPUT} 強制更新全部追蹤清單，含今天已更新過的；")
    print(f"   輸入 {FORCE_HOLDERS_UPDATE_INPUT} 強制重新下載集保大戶資料，不受本週快取限制):")
    last_category = object()  # 保證第一筆一定會先印出分類標題
    for i, (sid, name, path, category) in enumerate(tracked, 1):
        if category != last_category:
            print(f" 🗂️ {category or '未分類'}")
            last_category = category
        d = file_date_str(path)
        mark = " (今天已更新)" if d == today else (f" (最後更新 {d})" if d else "")
        print(f"  {i:>2}. {sid} {name}{mark}")
    print()

def resolve_tracked_indices(items, tracked):
    """把 <100 的純數字輸入轉換成追蹤清單中對應位置的股票代號；
    999 展開成全部追蹤清單(跳過今天已更新過的)；998 展開成全部追蹤清單(強制全部更新)"""
    today = datetime.now().strftime("%Y-%m-%d")
    mapping = {str(i): sid for i, (sid, _, _, _) in enumerate(tracked, 1)}
    resolved = []
    for p in items:
        if p == ALL_TRACKED_INPUT:
            for sid, name, path, _category in tracked:
                if file_date_str(path) == today:
                    print(f" ⏭ {sid} {name} 今天已更新過，跳過")
                    continue
                resolved.append(sid)
        elif p == FORCE_ALL_TRACKED_INPUT:
            resolved.extend(sid for sid, _, _, _ in tracked)
        elif p == FORCE_HOLDERS_UPDATE_INPUT:
            force_refresh_holders_market()
        elif p.isdigit() and int(p) < 100 and p in mapping:
            resolved.append(mapping[p])
        else:
            resolved.append(p)
    return resolved

def run_batch(tickers):
    total = len(tickers)
    if not total:
        return

    def run_one(ticker):
        try:
            return bool(run(ticker)), ""
        except Exception as error:
            return False, str(error)

    # 單檔維持原本易讀的逐步輸出；多檔才啟用受控並行。
    if total == 1:
        t = tickers[0]
        print(f"\n{'='*60}")
        print(f"[1/1] {t}")
        print('='*60)
        ok, error = run_one(t)
        if error:
            print(f"\n❌ {t} 發生錯誤: {error}")
        print(f"{'✅ OK' if ok else '❌ 失敗'} [1/1] {t}")
        return

    try:
        configured_workers = int(os.environ.get("STOCK_UPDATE_WORKERS", DEFAULT_BATCH_WORKERS))
    except (TypeError, ValueError):
        configured_workers = DEFAULT_BATCH_WORKERS
    worker_count = min(total, MAX_BATCH_WORKERS, max(1, configured_workers))
    print(f"\n⚡ 批次並行更新：{total} 檔，使用 {worker_count} 個工作執行緒")

    results_by_index = {}
    completed = 0
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="stock-update") as executor:
        future_map = {executor.submit(run_one, ticker): (index, ticker) for index, ticker in enumerate(tickers)}
        for future in as_completed(future_map):
            index, ticker = future_map[future]
            ok, error = future.result()
            completed += 1
            print(f"\n[{completed}/{total}] {ticker}")
            if error:
                print(f"❌ {ticker} 發生錯誤: {error}")
            print(f"{'✅ OK' if ok else '❌ 失敗'} [{completed}/{total}] {ticker}")
            results_by_index[index] = (ticker, ok)

    print(f"\n{'='*60}")
    print(f"批次執行完畢（{worker_count} 執行緒）")
    for index in range(total):
        t, ok = results_by_index[index]
        print(f"  {'✅' if ok else '❌'} {t}")
    print('='*60)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        tracked = scan_tracked_stocks()
        tickers = resolve_tracked_indices(sys.argv[1:], tracked)
        run_batch(tickers)
    else:
        while True:
            tracked = scan_tracked_stocks()
            if tracked:
                print_tracked_list(tracked)
            raw = input("台股代碼或名稱(可用逗號/空白分隔多支，也可輸入上方編號，輸入 Q 離開): ").strip()
            if raw.upper() == "Q":
                break
            if not raw:
                continue
            tickers = resolve_tracked_indices(parse_tickers(raw), tracked)
            run_batch(tickers)
