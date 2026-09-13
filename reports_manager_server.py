"""
Local server for the reports/ folder manager page.

reports/ holds every stock report as a flat {code}_{name}(TW|TWO).html +
matching _chart.png pair. This lets the user organize them into subfolders
(create/rename/delete folders, drag a report into a folder) without ever
touching Explorer -- reports/ isn't pushed to git (see .gitignore), so this
is purely a local organizing tool.

Serves reports_manager.html (and the report html/png files themselves, via
the default static file handler) from the repo root, plus a small JSON API
under /api/ for the tree/list/create/rename/delete/move operations.
"""
import glob
import html
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import datetime
import email.utils
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingTCPServer
from urllib.parse import urlparse, parse_qs
import requests

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT_DIR = Path(__file__).resolve().parent
REPORTS_DIR = ROOT_DIR / "reports"
EVOLUTION_RANKING_FILE = ROOT_DIR / "stock_winrate_ranking_evolution.md"
AI_RESEARCH_CARDS_FILE = ROOT_DIR / "ai_research_cards.json"
AI_RESEARCH_TEMP_FILE = ROOT_DIR / "ai_research_cards.tmp"
WATCHLIST_FILE = ROOT_DIR / "watchlist.json"
MACRO_DIR = ROOT_DIR / "指標數據"
MACRO_DATA_FILE = MACRO_DIR / "macro_data.json"
MACRO_STATUS_FILE = MACRO_DIR / "macro_update_status.json"
MACRO_UPDATE_SCRIPT = MACRO_DIR / "update_macro_data.py"
PORT = 8935
AUDIT_BLOCKS = ('monthly_revenue', 'earnings', 'catalyst', 'analyst_target', 'disposition')
RSS_CACHE_SECONDS = 600
RSS_MAX_CODES = 20
RSS_CACHE = {}
RSS_CACHE_LOCK = threading.RLock()
RSS_SUMMARY_CACHE_SECONDS = 21600
RSS_SUMMARY_CACHE = {}
RSS_SUMMARY_LOCK = threading.RLock()
GEMINI_RSS_MODELS = ('gemini-3.8-flash', 'gemini-3.7-flash', 'gemini-3.5-flash-lite')


def _rss_event_label(title: str) -> str:
    text = title.lower()
    rules = (
        ('重大訊息', ('重大訊息', '重訊', '公告')),
        ('營收／財報', ('營收', '財報', 'eps', '獲利', '虧損')),
        ('法說／展望', ('法說', '展望', '接單', '訂單', '擴產')),
        ('風險事件', ('處置', '注意股', '訴訟', '停工', '下修', '違約', '虧損')),
    )
    return next((label for label, keywords in rules if any(word in text for word in keywords)), '新聞動態')


def _rss_stock_name(code: str) -> str:
    try:
        names = json.loads((ROOT_DIR / 'stock_name_dict.json').read_text(encoding='utf-8'))
        return str(names.get(code, ''))
    except (OSError, json.JSONDecodeError):
        return ''


def _fetch_stock_rss(code: str) -> list[dict]:
    now = time.time()
    with RSS_CACHE_LOCK:
        cached = RSS_CACHE.get(code)
        if cached and now - cached['timestamp'] < RSS_CACHE_SECONDS:
            return cached['items']

    name = _rss_stock_name(code)
    query = f'{name} 股票 when:3d' if name else f'{code} 台股 when:3d'
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=72)
    try:
        response = requests.get(
            'https://news.google.com/rss/search',
            params={'q': query, 'hl': 'zh-TW', 'gl': 'TW', 'ceid': 'TW:zh-Hant'},
            headers={'User-Agent': 'StockAnalysisRSS/1.0'}, timeout=8,
        )
        response.raise_for_status()
        channel = ET.fromstring(response.content).find('channel')
        entries = []
        for item in (channel.findall('item') if channel is not None else [])[:3]:
            title = (item.findtext('title') or '').strip()
            link = (item.findtext('link') or '').strip()
            source_node = item.find('source')
            source = (source_node.text or '').strip() if source_node is not None else 'Google News RSS'
            published = (item.findtext('pubDate') or '').strip()
            try:
                published_at = email.utils.parsedate_to_datetime(published)
                if published_at.tzinfo is None:
                    published_at = published_at.replace(tzinfo=datetime.timezone.utc)
            except (TypeError, ValueError, IndexError):
                continue
            if title and link and published_at >= cutoff:
                entries.append({'code': code, 'name': name, 'title': title, 'link': link,
                                'source': source or 'Google News RSS', 'published': published,
                                'label': _rss_event_label(title)})
    except (requests.RequestException, ET.ParseError):
        entries = []
    with RSS_CACHE_LOCK:
        RSS_CACHE[code] = {'timestamp': now, 'items': entries}
    return entries


def filter_rss_items(items: list[dict], range_key: str) -> list[dict]:
    if range_key == '3d':
        return items
    taipei_today = (datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).date())
    filtered = []
    for item in items:
        try:
            published_at = email.utils.parsedate_to_datetime(item['published'])
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=datetime.timezone.utc)
            if published_at.astimezone(datetime.timezone(datetime.timedelta(hours=8))).date() == taipei_today:
                filtered.append(item)
        except (TypeError, ValueError, IndexError, KeyError):
            continue
    return filtered


def fetch_rss_news(codes: list[str], range_key: str = 'today') -> list[dict]:
    unique_codes = list(dict.fromkeys(code for code in codes if re.fullmatch(r'\d{4,6}', code)))[:RSS_MAX_CODES]
    items = []
    with ThreadPoolExecutor(max_workers=min(5, len(unique_codes) or 1)) as executor:
        futures = [executor.submit(_fetch_stock_rss, code) for code in unique_codes]
        for future in as_completed(futures):
            items.extend(filter_rss_items(future.result(), range_key))
    return items


def get_gemini_api_key() -> str | None:
    api_key = os.environ.get('GEMINI_API_KEY', '').strip()
    if api_key:
        return api_key
    env_file = ROOT_DIR / '.env'
    if not env_file.exists():
        return None
    for line in env_file.read_text(encoding='utf-8-sig', errors='ignore').splitlines():
        if line.strip().startswith('GEMINI_API_KEY='):
            value = line.split('=', 1)[1].strip().strip('"\'')
            return value or None
    return None


def summarize_rss_with_gemini(code: str, items: list[dict]) -> dict:
    if not items:
        return {'summary': '目前沒有可統整的 RSS 訊息。', 'facts': [], 'watch_items': [], 'source_indices': []}
    fingerprint = '|'.join(item['title'] for item in items)
    cache_key = f'{code}:{fingerprint}'
    now = time.time()
    with RSS_SUMMARY_LOCK:
        cached = RSS_SUMMARY_CACHE.get(cache_key)
        if cached and now - cached['timestamp'] < RSS_SUMMARY_CACHE_SECONDS:
            return cached['summary']
    api_key = get_gemini_api_key()
    if not api_key:
        raise RuntimeError('未設定 GEMINI_API_KEY')
    sources = '\n'.join(f'[{index}] {item["title"]}｜{item["source"]}｜{item["published"]}' for index, item in enumerate(items, 1))
    prompt = f'''你是台股資訊整理員。僅能根據下列 RSS 標題與來源整理 {code} 的資訊。
嚴禁給出買進、賣出、目標價、漲跌預測、評分或投資建議；不得補充來源未明示的事實。
請只輸出 JSON：{{"summary":"不超過90字的中性摘要","facts":["最多3項可由來源支持的事實"],"watch_items":["最多2項待確認事項，沒有則空陣列"],"source_indices":[引用來源編號]}}。
RSS 來源：
{sources}'''
    last_error = 'Gemini 暫時無法回應'
    for model in GEMINI_RSS_MODELS:
        try:
            response = requests.post(
                f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}',
                json={'contents': [{'parts': [{'text': prompt}]}], 'generationConfig': {'responseMimeType': 'application/json'}},
                timeout=25,
            )
            if response.status_code != 200:
                last_error = f'Gemini HTTP {response.status_code}'
                continue
            text = response.json()['candidates'][0]['content']['parts'][0]['text']
            result = json.loads(text)
            summary = {
                'summary': str(result.get('summary', '')).strip(),
                'facts': [str(value).strip() for value in result.get('facts', [])][:3],
                'watch_items': [str(value).strip() for value in result.get('watch_items', [])][:2],
                'source_indices': [int(value) for value in result.get('source_indices', []) if str(value).isdigit() and 1 <= int(value) <= len(items)],
            }
            with RSS_SUMMARY_LOCK:
                RSS_SUMMARY_CACHE[cache_key] = {'timestamp': now, 'summary': summary}
            return summary
        except (requests.RequestException, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
            last_error = f'Gemini 統整失敗：{error}'
    raise RuntimeError(last_error)

REPORTS_DIR.mkdir(exist_ok=True)


def validate_manual_llm_response(handoff, payload):
    """在重啟研究前攔下漏股或漏區塊的人工 JSON。"""
    if not isinstance(payload, dict):
        raise ValueError("Gemini 回覆必須是 JSON 物件")
    prompt = str(handoff.get('prompt') or '')
    is_research_cards = (
        handoff.get('response_schema') == 'research_cards'
        or ('"cards"' in prompt and 'counter_evidence_and_risks' in prompt)
    )
    if is_research_cards:
        cards = payload.get('cards')
        if not isinstance(cards, list):
            raise ValueError("缺少 cards 陣列")
        expected = [str(code) for code in handoff.get('expected_codes', [])]
        actual = [str(item.get('code') or '') for item in cards if isinstance(item, dict)]
        if expected and actual != expected:
            raise ValueError("cards 股票代號或順序與 Prompt 不同")
        required = ('code', 'name', 'as_of_date', 'data_cutoff', 'sources',
                    'supporting_facts', 'counter_evidence_and_risks',
                    'questions_for_human_review', 'data_quality_flags', 'disclaimer')
        for index, card in enumerate(cards, 1):
            if not isinstance(card, dict) or any(key not in card for key in required):
                raise ValueError(f"cards 第 {index} 筆欄位不完整")
        return
    if int(handoff.get('step') or 0) == 1:
        if not isinstance(payload.get('market_overview'), str):
            raise ValueError("缺少 market_overview")
        if not isinstance(payload.get('hot_sectors'), list):
            raise ValueError("缺少 hot_sectors 陣列（允許空陣列）")
        required = ('sector_name', 'heat_level', 'stage', 'catalysts',
                    'event_date', 'source_url', 'related_tags')
        for index, sector in enumerate(payload['hot_sectors'], 1):
            if not isinstance(sector, dict) or any(key not in sector for key in required):
                raise ValueError(f"hot_sectors 第 {index} 筆欄位不完整")
        return

    stocks = payload.get('stocks')
    if not isinstance(stocks, list):
        raise ValueError("缺少 stocks 陣列")
    expected = [str(code) for code in handoff.get('expected_codes', [])]
    if not expected:
        expected = list(dict.fromkeys(re.findall(
            r'"code"\s*:\s*"(\d{4,6})"', prompt
        )))
    actual = [str(item.get('code') or '') for item in stocks if isinstance(item, dict)]
    if expected and actual != expected:
        missing = [code for code in expected if code not in actual]
        extra = [code for code in actual if code not in expected]
        detail = []
        if missing:
            detail.append(f"缺少 {', '.join(missing)}")
        if extra:
            detail.append(f"多出 {', '.join(extra)}")
        if not detail:
            detail.append("股票順序與 Prompt 不同")
        raise ValueError("stocks 回覆不完整：" + "；".join(detail))
    for item in stocks:
        code = str(item.get('code') or '') if isinstance(item, dict) else ''
        if not isinstance(item, dict) or any(not isinstance(item.get(key), dict) for key in AUDIT_BLOCKS):
            raise ValueError(f"股票 {code or '未知'} 的五個查核區塊不完整")

MACRO_FILE_LOCK = threading.Lock()
MACRO_UPDATE_LOCK = threading.Lock()
MACRO_UPDATE_PROCESS = None
MACRO_UPDATE_JOB = {"running": False, "done": False, "returncode": None, "lines": []}
CLIENT_LOCK = threading.Lock()
CLIENT_HEARTBEATS = {}
CLIENTS_HAVE_CONNECTED = False
LAST_CLIENT_CHANGE = time.monotonic()

TRACKED_FILENAME_RE = re.compile(r'^([0-9A-Za-z]{2,6})_(.+?)\((TW|TWO)\)')
FORBIDDEN_NAME_CHARS = set('\\/:*?"<>|')
STOCK_NAME_DICT_PATH = ROOT_DIR / "stock_name_dict.json"
STOCK_NAME_DICT = {}
def reload_stock_name_dict():
    global STOCK_NAME_DICT
    if STOCK_NAME_DICT_PATH.exists():
        try:
            STOCK_NAME_DICT = json.loads(STOCK_NAME_DICT_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
reload_stock_name_dict()

def get_stock_name(code, default=None):
    if not code:
        return default
    return STOCK_NAME_DICT.get(str(code), default)


def read_watchlist():
    try:
        payload = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"version": 1, "updatedAt": "", "starred": []}
    starred = payload.get("starred") if isinstance(payload, dict) else payload
    if not isinstance(starred, list):
        starred = []
    seen = set()
    cleaned = []
    for item in starred:
        code = str(item or "").strip()
        if code and code not in seen:
            seen.add(code)
            cleaned.append(code)
    return {
        "version": 1,
        "updatedAt": str(payload.get("updatedAt", "")) if isinstance(payload, dict) else "",
        "starred": cleaned
    }


def write_watchlist(starred_list):
    if not isinstance(starred_list, list):
        raise ValueError("starred 必須是代號陣列")
    seen = set()
    cleaned = []
    for item in starred_list:
        code = str(item or "").strip()
        if code and code not in seen:
            seen.add(code)
            cleaned.append(code)
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    payload = {
        "version": 1,
        "updatedAt": now_str,
        "starred": cleaned
    }
    tmp_file = WATCHLIST_FILE.with_suffix(".tmp")
    tmp_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_file.replace(WATCHLIST_FILE)
    return payload


CACHE_DIR = ROOT_DIR / "cache"
CACHE_DIR.mkdir(exist_ok=True)
TODAY_NEW_STOCKS_FILE = CACHE_DIR / "today_new_stocks.json"
TODAY_NEW_STOCKS_LOCK = threading.Lock()


def read_today_new_stocks():
    """讀取今日新增個股清單。若檔案過期（非今日）則自動重置為空，自然消失，不需同步。"""
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    with TODAY_NEW_STOCKS_LOCK:
        if not TODAY_NEW_STOCKS_FILE.exists():
            return {"date": today_str, "codes": []}
        try:
            data = json.loads(TODAY_NEW_STOCKS_FILE.read_text(encoding="utf-8"))
            if data.get("date") != today_str or not isinstance(data.get("codes"), list):
                clean_data = {"date": today_str, "codes": []}
                TODAY_NEW_STOCKS_FILE.write_text(json.dumps(clean_data, ensure_ascii=False, indent=2), encoding="utf-8")
                return clean_data
            return {"date": today_str, "codes": [str(c).strip().upper() for c in data["codes"] if str(c).strip()]}
        except Exception:
            return {"date": today_str, "codes": []}


def record_today_new_stocks(codes):
    """將個股代號加入今日新增個股清單（今日有效）。"""
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    if isinstance(codes, str):
        codes = [codes]
    with TODAY_NEW_STOCKS_LOCK:
        existing = {"date": today_str, "codes": []}
        if TODAY_NEW_STOCKS_FILE.exists():
            try:
                loaded = json.loads(TODAY_NEW_STOCKS_FILE.read_text(encoding="utf-8"))
                if loaded.get("date") == today_str and isinstance(loaded.get("codes"), list):
                    existing["codes"] = [str(c).strip().upper() for c in loaded["codes"] if str(c).strip()]
            except Exception:
                pass
        seen = set(existing["codes"])
        for c in (codes or []):
            clean = str(c).strip().upper()
            if clean and clean not in seen:
                seen.add(clean)
                existing["codes"].append(clean)
        try:
            CACHE_DIR.mkdir(exist_ok=True)
            tmp_file = TODAY_NEW_STOCKS_FILE.with_suffix(".tmp")
            tmp_file.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_file.replace(TODAY_NEW_STOCKS_FILE)
        except Exception:
            pass
        return existing



def read_macro_data():
    try:
        payload = json.loads(MACRO_DATA_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    if not isinstance(payload, list):
        raise ValueError("macro_data.json 內容不是陣列")
    return payload


def write_macro_data(entries):
    if not isinstance(entries, list) or not all(
        isinstance(entry, dict) and isinstance(entry.get("date"), str) for entry in entries
    ):
        raise ValueError("總經資料格式不正確")
    sorted_entries = sorted(entries, key=lambda entry: entry["date"])
    with MACRO_FILE_LOCK:
        temp_file = MACRO_DATA_FILE.with_suffix(".json.tmp")
        temp_file.write_text(
            json.dumps(sorted_entries, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temp_file.replace(MACRO_DATA_FILE)
    return sorted_entries


def read_macro_update_status():
    try:
        payload = json.loads(MACRO_STATUS_FILE.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {
            "state": "idle",
            "message": "尚未執行總經資料更新。",
            "updatedFields": [],
            "failedFields": [],
        }


def _new_macro_update_job():
    return {"running": False, "done": False, "returncode": None, "lines": []}


def _run_macro_update():
    global MACRO_UPDATE_JOB, MACRO_UPDATE_PROCESS
    child_env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    returncode = -1
    try:
        proc = subprocess.Popen(
            [sys.executable, str(MACRO_UPDATE_SCRIPT)],
            cwd=MACRO_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=child_env,
        )
        with MACRO_UPDATE_LOCK:
            MACRO_UPDATE_PROCESS = proc
        for raw_line in proc.stdout:
            with MACRO_UPDATE_LOCK:
                MACRO_UPDATE_JOB["lines"].append(raw_line.rstrip("\n"))
        proc.wait()
        returncode = proc.returncode
    except OSError as error:
        with MACRO_UPDATE_LOCK:
            MACRO_UPDATE_JOB["lines"].append(f"無法啟動總經更新程式: {error}")
        returncode = -1
    finally:
        with MACRO_UPDATE_LOCK:
            MACRO_UPDATE_PROCESS = None
            MACRO_UPDATE_JOB["running"] = False
            MACRO_UPDATE_JOB["done"] = True
            MACRO_UPDATE_JOB["returncode"] = returncode


def _stop_macro_update_process():
    with MACRO_UPDATE_LOCK:
        proc = MACRO_UPDATE_PROCESS
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        try:
            proc.kill()
        except OSError:
            pass


def _stop_legacy_macro_server():
    """清理由舊版指標數據/server.py 留下的 8934 listener。"""
    if os.name != "nt":
        return
    try:
        output = subprocess.check_output(
            ["netstat", "-ano"], text=True, encoding="utf-8", errors="replace"
        )
    except OSError:
        return
    pids = set()
    for line in output.splitlines():
        if ":8934" not in line or "LISTENING" not in line.upper():
            continue
        parts = line.split()
        if parts and parts[-1].isdigit():
            pids.add(parts[-1])
    for pid in pids:
        try:
            subprocess.run(
                ["taskkill", "/F", "/PID", pid],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except OSError:
            pass


def _shutdown_application(delay=0.3):
    def worker():
        time.sleep(delay)
        try:
            _stop_macro_update_process()
            _stop_legacy_macro_server()
        finally:
            # 回應送達且子程序完成清理後，直接結束整個 Windows process；
            # 即使清理舊程序時遇到 Windows 權限問題，也不可留下主 server。
            os._exit(0)

    threading.Thread(target=worker, daemon=True).start()


def _record_client_heartbeat(client_id):
    global CLIENTS_HAVE_CONNECTED, LAST_CLIENT_CHANGE
    if not client_id:
        return
    with CLIENT_LOCK:
        CLIENT_HEARTBEATS[client_id] = time.monotonic()
        CLIENTS_HAVE_CONNECTED = True
        LAST_CLIENT_CHANGE = time.monotonic()


def _record_client_disconnect(client_id):
    global LAST_CLIENT_CHANGE
    if not client_id:
        return
    with CLIENT_LOCK:
        CLIENT_HEARTBEATS.pop(client_id, None)
        LAST_CLIENT_CHANGE = time.monotonic()


def _client_watchdog():
    """維持背景連線健康監控，依賴使用者點擊『關閉伺服器』或系統管理，避免分頁背景休眠時誤判自動關閉。"""
    while True:
        time.sleep(10)


# ── 記憶體快取加速層 (避免重複檔案遍歷與解析，API 延遲降至 < 1ms) ───────────
_CACHE_LOCK = threading.Lock()
_CARDS_CACHE = {"timestamp": 0, "data": None}
_REPORTS_INDEX_CACHE = {"timestamp": 0, "data": None}
_TREE_CACHE = {"timestamp": 0, "data": None}
_MD_REPORTS_CACHE = {}
_CACHE_TTL = 3.0  # 快取有效 3 秒

def invalidate_all_caches():
    with _CACHE_LOCK:
        _CARDS_CACHE["timestamp"] = 0
        _REPORTS_INDEX_CACHE["timestamp"] = 0
        _TREE_CACHE["timestamp"] = 0
        _MD_REPORTS_CACHE.clear()


def parse_md_report_card(md_path):
    try:
        text = md_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None
    m_code = re.search(r'股票代號[】\]\s\*]*[：:]\s*([0-9A-Za-z]+)', text)
    m_name = re.search(r'#\s*📈\s*(.*?)\s*\(', text)
    m_pat = re.search(r'型態標籤[】\]\s\*]*[：:]\s*([^\r\n]+)', text)
    m_win = re.search(r'預期勝率[】\]\s\*]*[：:]\s*[\*`\s]*([0-9.]+%?)', text)
    m_rr = re.search(r'(?:風險報酬比|風報比|R/R)[】\]\s\*]*[：:]\s*[\*`\s]*([0-9.]+)', text, re.I)
    m_action = re.search(r'建議評級[】\]\s\*]*[：:]\s*[\*`\s]*([^\r\n*`]+)', text)
    m_date = re.search(r'分析日期[】\]\s\*]*[：:]\s*([^\r\n*`]+)', text)
    m_price = re.search(r'當前價格[】\]\s\*]*[：:]\s*([0-9.]+)', text)
    m_stop = re.search(r'【停損位】\s*[：:]\s*[^0-9]*([0-9.]+)', text)
    m_target = re.search(r'【目標價】\s*[：:]\s*[^0-9]*([0-9.]+)', text)
    
    m_tech = re.search(r'技術標籤[】\]\s\*]*[：:]\s*([^\r\n]+)', text)
    m_kline = re.search(r'K線\s*(?:指標)?標籤[】\]\s\*]*[：:]\s*([^\r\n]+)', text, re.I)
    m_vol = re.search(r'VOL\s*(?:指標)?標籤[】\]\s\*]*[：:]\s*([^\r\n]+)', text, re.I) or re.search(r'量能標籤[】\]\s\*]*[：:]\s*([^\r\n]+)', text, re.I)
    m_chip = re.search(r'籌碼標籤[】\]\s\*]*[：:]\s*([^\r\n]+)', text)
    m_rsi = re.search(r'RSI\s*(?:指標)?標籤[】\]\s\*]*[：:]\s*([^\r\n]+)', text, re.I)
    m_macd = re.search(r'MACD\s*(?:指標)?標籤[】\]\s\*]*[：:]\s*([^\r\n]+)', text, re.I)
    m_kd = re.search(r'KD\s*(?:指標)?標籤[】\]\s\*]*[：:]\s*([^\r\n]+)', text, re.I)
    
    code = m_code.group(1).strip() if m_code else ""
    if not code:
        fname = md_path.stem
        code = fname.split('_')[0]
    name = m_name.group(1).strip() if m_name else ""
    if not name:
        parts = md_path.stem.split('_')
        if len(parts) >= 2:
            name = parts[1]
            
    pattern = m_pat.group(1).strip() if m_pat else "多頭排列階梯推升"
    win_str = m_win.group(1).replace('%', '').strip() if m_win else "70"
    try:
        win_rate = float(win_str)
    except ValueError:
        win_rate = 70.0

    # 優先採用報告已明列的 R/R；舊版報告沒有該欄時，以交易計畫的
    # 現價、停損與目標價計算：(目標價 - 現價) / (現價 - 停損)。
    rr_ratio = None
    try:
        if m_rr:
            rr_ratio = float(m_rr.group(1))
        elif m_price and m_stop and m_target:
            current_price = float(m_price.group(1))
            stop_price = float(m_stop.group(1))
            target_price = float(m_target.group(1))
            risk = current_price - stop_price
            reward = target_price - current_price
            if risk > 0 and reward >= 0:
                rr_ratio = round(reward / risk, 2)
    except ValueError:
        rr_ratio = None

    tech_tags_str = m_kline.group(1).strip() if m_kline else (m_tech.group(1).strip() if m_tech else "")
    vol_tags_str = m_vol.group(1).strip() if m_vol else ""
    chip_tags_str = m_chip.group(1).strip() if m_chip else ""
    rsi_tags_str = m_rsi.group(1).strip() if m_rsi else ""
    macd_tags_str = m_macd.group(1).strip() if m_macd else ""
    kd_tags_str = m_kd.group(1).strip() if m_kd else ""

    m_mkt = re.search(r'\(([0-9A-Za-z]+)\.(TW|TWO)\)', text)
    market = m_mkt.group(2) if m_mkt else ("TWO" if "(TWO)" in md_path.name else "TW")

    group = md_path.parent.name if md_path.parent != REPORTS_DIR else "未分類"
    return {
        "code": code,
        "name": name,
        "market": market,
        "group": group,
        "date": m_date.group(1).strip() if m_date else "",
        "current": m_price.group(1).strip() if m_price else "",
        "decision": m_action.group(1).strip() if m_action else "多頭順勢",
        "winRate": win_rate,
        "rr": rr_ratio,
        "pattern": pattern,
        "klineTags": tech_tags_str,
        "rsiTags": rsi_tags_str,
        "volTags": vol_tags_str,
        "macdTags": macd_tags_str,
        "kdTags": kd_tags_str,
        "technicalTags": tech_tags_str,
        "chipTags": chip_tags_str,
        "raw": text,
        "reportPath": md_path.relative_to(REPORTS_DIR).as_posix()
    }


def read_stock_cards():
    """直接解析 reports/ 底下所有最新的個股 .md 技術分析報告，具備記憶體快取加速。"""
    now = time.time()
    with _CACHE_LOCK:
        if _CARDS_CACHE["data"] is not None and (now - _CARDS_CACHE["timestamp"] < _CACHE_TTL):
            return _CARDS_CACHE["data"]

    by_code = {}
    if not REPORTS_DIR.is_dir():
        return by_code
        
    for md_path in REPORTS_DIR.glob("**/*_4階段技術分析報告.md"):
        card = parse_md_report_card(md_path)
        if card and card.get("code"):
            by_code[card["code"]] = card

    attach_report_flows(by_code)

    with _CACHE_LOCK:
        _CARDS_CACHE["timestamp"] = now
        _CARDS_CACHE["data"] = by_code

    return by_code


def resolve_safe_path(rel_path):
    """把前端傳來的相對路徑轉成 reports/ 底下的絕對路徑，並擋掉任何跳出 reports/ 的嘗試
    （例如 ../../ 或絕對路徑），避免這個工具被拿來動到 reports/ 以外的檔案。"""
    rel_path = (rel_path or "").strip().strip("/\\")
    base = str(REPORTS_DIR.resolve())
    if not rel_path:
        return REPORTS_DIR.resolve()
    candidate = Path(os.path.normpath(os.path.join(base, rel_path))).resolve()
    candidate_str = str(candidate)
    if candidate_str != base and not candidate_str.startswith(base + os.sep):
        raise ValueError("非法路徑")
    return candidate


def sanitize_folder_name(name):
    name = (name or "").strip()
    if not name or name in (".", "..") or any(ch in name for ch in FORBIDDEN_NAME_CHARS):
        raise ValueError("資料夾名稱不合法（不能是空白、.、.. 或包含 \\ / : * ? \" < > |）")
    return name


def build_tree(path=None):
    is_root = (path is None or path == REPORTS_DIR.resolve())
    now = time.time()
    if is_root:
        with _CACHE_LOCK:
            if _TREE_CACHE["data"] is not None and (now - _TREE_CACHE["timestamp"] < _CACHE_TTL):
                return _TREE_CACHE["data"]

    path = path or REPORTS_DIR.resolve()
    rel = os.path.relpath(str(path), str(REPORTS_DIR.resolve()))
    rel = "" if rel == "." else rel.replace(os.sep, "/")
    children = []
    try:
        for entry in sorted(os.listdir(path), key=str.lower):
            full = path / entry
            if full.is_dir():
                children.append(build_tree(full))
    except FileNotFoundError:
        pass
    _folders, reports = list_folder(path)
    res = {"name": path.name if rel else "reports", "path": rel, "children": children, "reports": reports}
    if is_root:
        try:
            today_info = read_today_new_stocks()
            res["todayNewStocks"] = today_info.get("codes", [])
        except Exception:
            res["todayNewStocks"] = []
        with _CACHE_LOCK:
            _TREE_CACHE["timestamp"] = now
            _TREE_CACHE["data"] = res
    return res


def list_folder(path):
    folders = []
    reports = []
    seen_bases = set()
    try:
        entries = sorted(os.listdir(path), key=str.lower)
    except FileNotFoundError:
        return folders, reports

    for entry in entries:
        # macOS 會在外接磁碟或非 APFS 檔案系統產生 AppleDouble 中繼檔（._*）。
        # 這些不是使用者的報表，不應出現在管理清單。
        if entry.startswith("._") or entry == ".DS_Store":
            continue
        full = path / entry
        if full.is_dir():
            folders.append(entry)
        elif entry.lower().endswith(".html"):
            base = entry[:-5]
            if base in seen_bases:
                continue
            seen_bases.add(base)
            chart_name = base + "_chart.png"
            m = TRACKED_FILENAME_RE.match(entry)
            code = m.group(1) if m else None
            if not code:
                parts = base.split("_")
                if parts and 2 <= len(parts[0]) <= 6:
                    code = parts[0]
            name = get_stock_name(code, m.group(2)) if (m and code) else (m.group(2) if m else None)
            market = m.group(3) if m else ("TWO" if "(TWO)" in entry else "TW")
            md_name = None
            if code:
                md_matches = list(path.glob(f"{code}_*.md"))
                if md_matches:
                    md_name = md_matches[0].name
            stat_info = full.stat()
            reports.append({
                "base": base,
                "code": code,
                "name": name,
                "market": market,
                "html": entry,
                "chart": chart_name if (path / chart_name).exists() else None,
                "md": md_name,
                "hasMd": bool(md_name),
                "mtime": stat_info.st_mtime,
                "size": stat_info.st_size,
            })
    return folders, reports


def list_reports_recursive(path):
    """遞迴收集某資料夾(含底下所有子資料夾)裡的全部報表，給「更新類組」用——
    使用者選一個大分類資料夾，要一次更新它跟底下所有子資料夾裡的個股。
    """
    reports = []
    for dirpath, _dirnames, filenames in os.walk(path):
        seen_bases = set()
        for fn in sorted(filenames, key=str.lower):
            if fn.startswith("._") or fn == ".DS_Store":
                continue
            if not fn.lower().endswith(".html"):
                continue
            base = fn[:-5]
            if base in seen_bases:
                continue
            seen_bases.add(base)
            m = TRACKED_FILENAME_RE.match(fn)
            if m:
                code = m.group(1)
                name = get_stock_name(code, m.group(2))
                market = m.group(3) if m else ("TWO" if "(TWO)" in fn else "TW")
                reports.append({"base": base, "code": code, "name": name, "market": market})
    return reports


def build_reports_index():
    """建立型態教學使用的全域報表索引，具備記憶體快取加速。

    reports/ 允許使用者自由建立分類子資料夾，因此不能假設報表都在根目錄。
    同一股票若因手動整理留下多份 HTML，索引只選最後修改時間最新的一份，
    同時把其他候選路徑附在 duplicates，讓前端能提示而不會靜默載入任意檔案。

    回傳值刻意維持陣列格式，與原 pattern_viewer/server.py 的
    /api/reports-index 相容；新增欄位不影響既有 PatternViewer 使用者。
    """
    now = time.time()
    with _CACHE_LOCK:
        if _REPORTS_INDEX_CACHE["data"] is not None and (now - _REPORTS_INDEX_CACHE["timestamp"] < _CACHE_TTL):
            return _REPORTS_INDEX_CACHE["data"]

    candidates_by_code = {}
    if not REPORTS_DIR.is_dir():
        return []

    for dirpath, _dirnames, filenames in os.walk(REPORTS_DIR):
        directory = Path(dirpath)
        for filename in filenames:
            if not filename.lower().endswith(".html"):
                continue
            match = TRACKED_FILENAME_RE.match(filename)
            if not match:
                continue

            code, raw_name, market = match.groups()
            name = get_stock_name(code, raw_name)
            html_path = directory / filename
            relative_path = html_path.relative_to(REPORTS_DIR).as_posix()
            chart_path = html_path.with_name(html_path.stem + "_chart.png")
            candidate = {
                "code": code,
                "name": name,
                "market": market,
                "path": relative_path,
                "chartPath": chart_path.relative_to(REPORTS_DIR).as_posix() if chart_path.exists() else None,
                "mtime": html_path.stat().st_mtime,
            }
            candidates_by_code.setdefault(code, []).append(candidate)

    index = []
    for code, candidates in candidates_by_code.items():
        # 路徑作為第二排序條件，確保 mtime 相同時每次仍選到同一份報表。
        candidates.sort(key=lambda item: (item["mtime"], item["path"]), reverse=True)
        selected = dict(candidates[0])
        selected["duplicateCount"] = len(candidates)
        selected["duplicates"] = [item["path"] for item in candidates[1:]]
        index.append(selected)

    res = sorted(index, key=lambda item: (item["code"], item["path"]))
    with _CACHE_LOCK:
        _REPORTS_INDEX_CACHE["timestamp"] = now
        _REPORTS_INDEX_CACHE["data"] = res
    return res


def _cell_text(fragment):
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def _signed_int(value):
    cleaned = value.replace(",", "").replace("＋", "+").replace("－", "-").strip()
    try:
        return int(cleaned)
    except ValueError:
        return None


def _report_table_rows(report_text, heading_pattern, value_keys):
    """從既有報表的指定表格擷取每日數據；略過表頭與累計列。"""
    match = re.search(
        rf"<h2[^>]*>[^<]*{heading_pattern}[^<]*</h2>.*?<table[^>]*>(.*?)</table>",
        report_text,
        re.I | re.S,
    )
    if not match:
        return []
    parsed = []
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", match.group(1), re.I | re.S):
        cells = [_cell_text(cell) for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.I | re.S)]
        if len(cells) != len(value_keys) + 1 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", cells[0]):
            continue
        values = [_signed_int(value) for value in cells[1:]]
        if any(value is None for value in values):
            continue
        parsed.append({"date": cells[0], **dict(zip(value_keys, values))})
    return parsed


def _report_holder_rows(report_text):
    """擷取集保週報的 400 張與 1000 張以上持股比例。"""
    match = re.search(
        r"<h2[^>]*>[^<]*大戶持股比例[^<]*</h2>.*?<table[^>]*>(.*?)</table>",
        report_text,
        re.I | re.S,
    )
    if not match:
        return []
    rows = []
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", match.group(1), re.I | re.S):
        cells = [_cell_text(cell) for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.I | re.S)]
        if len(cells) < 5 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", cells[0]):
            continue
        try:
            rows.append({
                "date": cells[0],
                "big400Pct": float(cells[1].replace("%", "").strip()),
                "big1000Pct": float(cells[4].replace("%", "").strip()),
            })
        except ValueError:
            continue
    return rows


def _chip_snapshot(margin_rows, holder_rows):
    """組合首頁與專業看盤共用的籌碼快照。"""
    margin_change_rates = {}
    for days in (1, 5, 20):
        period_rows = margin_rows[:days]
        if len(period_rows) < days:
            continue
        change = sum(row["marginChange"] for row in period_rows)
        base = period_rows[0]["marginBalance"] - change
        if base > 0:
            margin_change_rates[str(days)] = round(change / base * 100, 2)

    snapshot = {"marginChangeRates": margin_change_rates, "big400Change": None, "big1000Change": None}
    if len(holder_rows) >= 2:
        previous, latest = holder_rows[-2], holder_rows[-1]
        snapshot.update({
            "holdersDate": latest["date"],
            "big400Change": round(latest["big400Pct"] - previous["big400Pct"], 2),
            "big1000Change": round(latest["big1000Pct"] - previous["big1000Pct"], 2),
        })
    return snapshot


def _report_close_prices(report_text):
    """擷取技術資料表中的每日收盤價，以 MM-DD 為 key 供法人日期對齊。"""
    prices = {}
    for table_html in re.findall(r"<table[^>]*>(.*?)</table>", report_text, re.I | re.S):
        header_text = _cell_text(table_html[:1000])
        if not all(label in header_text for label in ("日期", "開", "高", "低", "收", "量")):
            continue
        for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, re.I | re.S):
            cells = [_cell_text(cell) for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.I | re.S)]
            if len(cells) < 5 or not re.fullmatch(r"\d{2}/\d{2}", cells[0]):
                continue
            try:
                prices[cells[0].replace("/", "-")] = float(cells[4].replace(",", ""))
            except ValueError:
                continue
        if prices:
            break
    return prices


def _report_pe(report_text):
    """擷取個股 HTML 報表基本面區的 trailing PE。"""
    match = re.search(r'<b>\s*PE\s*[：:]\s*</b>\s*([0-9]+(?:\.[0-9]+)?)', report_text, re.I)
    try:
        return float(match.group(1)) if match else None
    except ValueError:
        return None


def _report_recent_kline_summary(report_text):
    """擷取最後兩筆 K 線收盤價，計算當日漲跌幅 (change, changePct)。"""
    rows = []
    for table_html in re.findall(r"<table[^>]*>(.*?)</table>", report_text, re.I | re.S):
        header_text = _cell_text(table_html[:1000])
        if not all(label in header_text for label in ("日期", "開", "高", "低", "收", "量")):
            continue
        for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, re.I | re.S):
            cells = [_cell_text(cell) for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.I | re.S)]
            if len(cells) < 5 or not re.fullmatch(r"\d{2}/\d{2}", cells[0]):
                continue
            try:
                rows.append((cells[0], float(cells[4].replace(",", ""))))
            except ValueError:
                continue
        if rows:
            break
    if not rows:
        return {"price": None, "prevPrice": None, "change": None, "changePct": None}
    last_price = rows[-1][1]
    prev_price = rows[-2][1] if len(rows) >= 2 else None
    change = round(last_price - prev_price, 2) if prev_price is not None else 0.0
    change_pct = round((change / prev_price) * 100, 2) if (prev_price and prev_price > 0) else 0.0
    return {
        "price": last_price,
        "prevPrice": prev_price,
        "change": change,
        "changePct": change_pct
    }


def attach_report_flows(cards_by_code):
    """將最新報表的基本面、法人、收盤價與融資券資料附加到卡片 API。"""
    reports_by_code = {item["code"]: item for item in build_reports_index()}
    for code, card in cards_by_code.items():
        report = reports_by_code.get(code)
        if not report:
            card["pe"] = None
            card["price"] = float(card.get("current")) if card.get("current") else None
            card["change"] = None
            card["changePct"] = None
            card["institutionalFlow"] = []
            card["marginFlow"] = []
            card["holderFlow"] = []
            card["chipSnapshot"] = _chip_snapshot([], [])
            continue
        try:
            report_text = (REPORTS_DIR / report["path"]).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            card["pe"] = None
            card["price"] = float(card.get("current")) if card.get("current") else None
            card["change"] = None
            card["changePct"] = None
            card["institutionalFlow"] = []
            card["marginFlow"] = []
            card["holderFlow"] = []
            card["chipSnapshot"] = _chip_snapshot([], [])
            continue
        card["pe"] = _report_pe(report_text)
        kline_sum = _report_recent_kline_summary(report_text)
        card["price"] = kline_sum["price"] if kline_sum["price"] is not None else (float(card.get("current")) if card.get("current") else None)
        card["change"] = kline_sum["change"]
        card["changePct"] = kline_sum["changePct"]

        institutional = _report_table_rows(
            report_text, "三大法人", ("foreign", "trust", "dealer", "total")
        )[:15]
        close_prices = _report_close_prices(report_text)
        for row in institutional:
            row["close"] = close_prices.get(row["date"][5:])
        card["institutionalFlow"] = institutional
        card["marginFlow"] = _report_table_rows(
            report_text, "融資融券", ("marginBalance", "marginChange", "shortBalance", "shortChange")
        )
        card["holderFlow"] = _report_holder_rows(report_text)
        card["chipSnapshot"] = _chip_snapshot(card["marginFlow"], card["holderFlow"])


def count_contents(path):
    """遞迴數一數資料夾裡有幾份報表(html)，給刪除確認用，讓使用者知道會連坐刪掉多少東西。"""
    count = 0
    for root, _dirs, files in os.walk(path):
        count += sum(1 for f in files if f.lower().endswith(".html"))
    return count


# ── 報表產生（把 stock_report_generator.py 接進來，做到「生產→排版→評分」一條龍）──
GENERATOR_SCRIPT = ROOT_DIR / "stock_report_generator.py"
GENERATE_LOCK = threading.Lock()
GENERATE_PROGRESS_RE = re.compile(r"^\[(\d+)/(\d+)\]\s+(.+)$")
GENERATE_RESULT_RE = re.compile(r"^(✅ OK|❌ 失敗) \[(\d+)/(\d+)\] (.+)$")
GENERATE_ACTIVE_RE = re.compile(r"^🔄 尚在處理：(.*)$")


def _new_generate_job():
    return {
        "running": False,
        "lines": [],
        "progress": {"i": 0, "total": 0, "ticker": ""},
        "active": [],
        "results": [],
        "done": False,
        "returncode": None,
        "started_at": None,
        "last_output_at": None,
        "finished_at": None,
    }


generate_job = _new_generate_job()

BATCH_SCANNER_SCRIPT = ROOT_DIR / "batch_scanner.py"
BATCH_SCANNER_LOCK = threading.Lock()


def _new_batch_scanner_job():
    return {
        "running": False,
        "lines": [],
        "done": False,
        "returncode": None,
        "started_at": None,
        "finished_at": None,
        "last_output_at": None,
        "research_enabled": False,
        "research_updated": False,
        "warning": None,
        "debug_events": [],
        "command": [],
    }


batch_scanner_job = _new_batch_scanner_job()

BATCH_SCANNER_GEMINI_SCRIPT = ROOT_DIR / "batch_scanner_gemini.py"
BATCH_SCANNER_GEMINI_LOCK = threading.Lock()
batch_scanner_gemini_job = _new_batch_scanner_job()

EVOLUTION_ENGINE_SCRIPT = ROOT_DIR / "evolution_engine.py"
LLM_HANDOFF_FILE = ROOT_DIR / "llm_manual_handoff.json"
EVOLUTION_ENGINE_LOCK = threading.Lock()
evolution_engine_job = _new_batch_scanner_job()


def build_evolution_engine_cmd(research_enabled: bool = True) -> list[str]:
    """組裝啟動 evolution_engine.py 的完整命令列參數。

    research_enabled: 為 True 時加入 --research 明確啟動不計分之反證與風險研究卡產出。
    """
    cmd = [sys.executable, str(EVOLUTION_ENGINE_SCRIPT)]
    if research_enabled:
        cmd.append("--research")
    return cmd


def _run_evolution_engine(research_enabled: bool = True):
    global evolution_engine_job
    child_env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    rc = -1
    cmd = build_evolution_engine_cmd(research_enabled=research_enabled)
    with EVOLUTION_ENGINE_LOCK:
        evolution_engine_job["command"] = [str(part) for part in cmd]
        evolution_engine_job["debug_events"].append(
            f"準備啟動子程序；research={research_enabled}；argv={' '.join(str(part) for part in cmd)}"
        )
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=ROOT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=child_env,
        )
        with EVOLUTION_ENGINE_LOCK:
            evolution_engine_job["debug_events"].append(f"子程序已啟動；pid={proc.pid}")
        for raw_line in proc.stdout:
            with EVOLUTION_ENGINE_LOCK:
                line = re.sub(r'key=[^\s&]+', 'key=[REDACTED]', raw_line.rstrip("\n"))
                line = re.sub(r'AIza[\w-]+', '[REDACTED]', line)
                line = re.sub(r'AQ\.[\w-]+', '[REDACTED]', line)
                evolution_engine_job["lines"].append(line)
                evolution_engine_job["last_output_at"] = time.time()
                if "已保存" in line and "份 AI 研究卡" in line:
                    evolution_engine_job["research_updated"] = True
                if "未設定 GEMINI_API_KEY" in line or "未檢測到 GEMINI_API_KEY" in line:
                    evolution_engine_job["warning"] = "未設定 GEMINI_API_KEY，略過 AI 研究卡產出"
                elif "配額" in line or "quota" in line.lower() or "429" in line:
                    evolution_engine_job["warning"] = "Gemini API 配額受限或逾時"
                elif "保存研究卡失敗" in line or "生成研究卡失敗" in line:
                    evolution_engine_job["warning"] = line.strip()
        proc.wait()
        rc = proc.returncode
        with EVOLUTION_ENGINE_LOCK:
            evolution_engine_job["debug_events"].append(f"子程序結束；returncode={rc}")
    except Exception as e:
        with EVOLUTION_ENGINE_LOCK:
            evolution_engine_job["lines"].append(f"❌ 執行 AI 進化/研究引擎失敗: {e}")
            evolution_engine_job["warning"] = f"執行失敗: {e}"
            evolution_engine_job["debug_events"].append(f"子程序啟動或執行例外：{type(e).__name__}: {e}")
        rc = -1
    finally:
        with EVOLUTION_ENGINE_LOCK:
            evolution_engine_job["running"] = False
            evolution_engine_job["done"] = True
            evolution_engine_job["returncode"] = rc
            evolution_engine_job["finished_at"] = time.time()


# =========================================================================
# 🔬 單一步驟人工 AI 反證與風險研究卡接力模組 (無外部 API 依賴)
# =========================================================================
AI_RESEARCH_LOCK = threading.RLock()
AI_RESEARCH_EVENTS = []


def _log_ai_research_event(event_type: str, detail: str = ""):
    with AI_RESEARCH_LOCK:
        AI_RESEARCH_EVENTS.append({
            "timestamp": datetime.datetime.now().strftime("%H:%M:%S"),
            "event": event_type,
            "detail": detail
        })
        if len(AI_RESEARCH_EVENTS) > 30:
            AI_RESEARCH_EVENTS.pop(0)


def load_evolution_candidates() -> tuple[str, list[dict]]:
    """讀取當前最新量化實戰勝率榜候選名單與截止日。"""
    md_file = EVOLUTION_RANKING_FILE
    as_of_date = ""
    candidates = []
    if md_file.exists():
        content = md_file.read_text(encoding="utf-8", errors="replace")
        for line in content.splitlines():
            line = line.strip()
            if not as_of_date and "資料截止日" in line:
                m = re.search(r'資料截止日[：:]\s*([0-9\/-]+)', line)
                if m:
                    as_of_date = m.group(1)
            if line.startswith("|") and line.endswith("|"):
                parts = [c.strip() for c in line.split("|")[1:-1]]
                if len(parts) >= 7 and parts[0].replace("*", "").isdigit():
                    code = parts[1].replace("`", "").strip()
                    name = parts[2].replace("*", "").strip()
                    cat = parts[3].strip()
                    price = parts[4].strip()
                    today_pct = parts[5].strip()
                    score = parts[6].replace("*", "").strip()
                    feat = parts[8].strip() if len(parts) > 8 else ""
                    candidates.append({
                        "code": code,
                        "name": name,
                        "category": cat,
                        "price": price,
                        "today_pct": today_pct,
                        "score": score,
                        "feature": feat
                    })
    if not as_of_date:
        as_of_date = datetime.date.today().isoformat()
    return as_of_date, candidates


def build_ai_research_prompt(candidates: list[dict], as_of_date: str) -> str:
    """產生單一步驟人工接力所需的 AI 反證與風險研究 Prompt。"""
    stock_lines = []
    for c in candidates:
        stock_lines.append(
            f"- 代號：{c['code']}，名稱：{c['name']}，類群：{c.get('category', '未分類')}，"
            f"收盤價：{c.get('price', '-')}，今日漲跌：{c.get('today_pct', '-')}，量化規則分：{c.get('score', '-')}"
        )
    stocks_text = "\n".join(stock_lines)
    example_code = candidates[0]['code'] if candidates else '3491'
    example_name = candidates[0]['name'] if candidates else '昇達科'

    prompt = f"""【台股量化候選名單・AI 反證與風險研究卡查核任務】

資料基準截止日：{as_of_date}
分析標的名單（共 {len(candidates)} 檔）：
{stocks_text}

【你的角色與任務目標】
你是一位專注於「下行風險、反向證據、盲點挖掘」的獨立風險查核員，並非投資顧問或題材推銷員。
以上股票是由本機可重現的量價/多因子規則模型篩選出的候選股。你的唯一任務是為每一檔標的挖掘客觀風險警訊與待確認事項。

【嚴格紀律規範】
1. 嚴禁任何推薦、建議買進/賣出、目標價預估、預期報酬或主觀評分。
2. 每一檔標的必須提供至少一項具體之反向證據或下行風險（如：原物料暴漲、同業殺價擴產、客戶砍單、高本益比修正、董監持股偏低、法說會保守展望等）。
3. 優先參考公司重大訊息、公開資訊觀測站(MOPS)、最新財報與官方公告等一手來源；若引用新聞或第三方研調，來源缺漏請標示 "unknown"，不可憑空捏造。
4. 若市場普遍存在「受惠 AI」、「概念股」等泛泛題材宣傳，必須在 data_quality_flags 加入 "news_lag"，嚴禁直接轉為正面結論。
5. 輸出格式必須是完全合法的單一 JSON 物件，頂層欄位名稱為 "cards"，包含各股票研究卡陣列。不要添加任何開場白、結尾或 Markdown 贅字。

【輸出 JSON Schema】
{{
  "cards": [
    {{
      "code": "{example_code}",
      "name": "{example_name}",
      "as_of_date": "{as_of_date}",
      "data_cutoff": "{as_of_date}",
      "sources": [
        {{
          "url": "https://... 或 unknown",
          "publisher": "發布單位或 unknown",
          "published_at": "YYYY-MM-DD 或 unknown",
          "source_type": "official|news|unknown"
        }}
      ],
      "supporting_facts": [
        "可查核之一手或客觀事實（若無請留空陣列）"
      ],
      "counter_evidence_and_risks": [
        "至少列出一項反向證據或下行風險警訊（必填）"
      ],
      "questions_for_human_review": [
        "投資人或研究員在下次財報/營收公告應重點確認之提問"
      ],
      "data_quality_flags": [
        "news_lag|duplicate_source|no_primary_source|unknown"
      ],
      "disclaimer": "此研究卡不參與評分、排序或投資建議。"
    }}
  ]
}}
"""
    return prompt.strip()


def validate_ai_research_cards(payload: dict, expected_codes: list[str]) -> list[dict]:
    """嚴格驗證 AI 回覆的研究卡 JSON，確保完整性、順序與欄位正確。"""
    if not isinstance(payload, dict):
        raise ValueError("AI 回覆必須是 JSON 物件（最外層為包含 'cards' 的物件）")
    cards = payload.get("cards")
    if not isinstance(cards, list):
        raise ValueError("缺少 'cards' 陣列")
    if not cards:
        raise ValueError("'cards' 陣列不可為空")

    actual_codes = [str(c.get("code", "")).strip() for c in cards if isinstance(c, dict)]
    if actual_codes != expected_codes:
        missing = [c for c in expected_codes if c not in actual_codes]
        extra = [c for c in actual_codes if c not in expected_codes]
        errs = []
        if missing:
            errs.append(f"缺少股票：{', '.join(missing)}")
        if extra:
            errs.append(f"多出未請求股票：{', '.join(extra)}")
        if not errs:
            errs.append("股票順序與要求不一致")
        raise ValueError("股票名單不符：" + "；".join(errs))

    validated_cards = []
    for i, c in enumerate(cards, 1):
        if not isinstance(c, dict):
            raise ValueError(f"第 {i} 筆研究卡不是有效的物件")
        code = str(c.get("code", "")).strip()
        name = str(c.get("name", "")).strip()
        if not code:
            raise ValueError(f"第 {i} 筆研究卡缺少股票代號 (code)")
        if not name:
            raise ValueError(f"第 {i} 筆股票 {code} 缺少名稱 (name)")

        risks = c.get("counter_evidence_and_risks")
        if not isinstance(risks, list) or not any(str(r).strip() for r in risks):
            raise ValueError(f"股票 {code} 缺少反證與風險提示 (counter_evidence_and_risks 至少需有 1 項)")

        facts = c.get("supporting_facts") if isinstance(c.get("supporting_facts"), list) else []
        questions = c.get("questions_for_human_review") if isinstance(c.get("questions_for_human_review"), list) else []
        raw_sources = c.get("sources") if isinstance(c.get("sources"), list) else []
        sources = []
        for s in raw_sources:
            if isinstance(s, dict):
                sources.append({
                    "url": str(s.get("url", "unknown")),
                    "publisher": str(s.get("publisher", "unknown")),
                    "published_at": str(s.get("published_at", "unknown")),
                    "source_type": str(s.get("source_type", "unknown")),
                })
        flags = [str(f).strip() for f in c.get("data_quality_flags", []) if str(f).strip()]
        if not flags:
            flags = ["unknown"]

        card = {
            "code": code,
            "name": name,
            "as_of_date": str(c.get("as_of_date", "")).strip() or "unknown",
            "data_cutoff": str(c.get("data_cutoff", "")).strip() or "unknown",
            "sources": sources,
            "supporting_facts": [str(f).strip() for f in facts if str(f).strip()],
            "counter_evidence_and_risks": [str(r).strip() for r in risks if str(r).strip()],
            "questions_for_human_review": [str(q).strip() for q in questions if str(q).strip()],
            "data_quality_flags": flags,
            "disclaimer": str(c.get("disclaimer", "此研究卡不參與評分、排序或投資建議。"))
        }
        validated_cards.append(card)

    return validated_cards


def save_ai_research_cards_atomically(cards: list[dict]):
    """原子化寫入 ai_research_cards.json，避免半寫入或損壞。"""
    cards_file = AI_RESEARCH_CARDS_FILE
    temp_file = AI_RESEARCH_TEMP_FILE
    data = json.dumps(cards, ensure_ascii=False, indent=2)
    temp_file.write_text(data, encoding="utf-8")
    temp_file.replace(cards_file)


def update_evolution_ranking_md_research_cards(cards: list[dict]):
    """更新 stock_winrate_ranking_evolution.md 中的 AI 研究卡區塊，不影響上方量化榜單。"""
    md_file = EVOLUTION_RANKING_FILE
    if not md_file.exists():
        return
    content = md_file.read_text(encoding="utf-8", errors="replace")
    split_marker = "### 🔬 【AI 研究卡（不計分）】"
    if split_marker in content:
        top_part = content.split(split_marker)[0]
    else:
        top_part = content.rstrip() + "\n\n---\n\n"

    lines = [split_marker, ""]
    lines.append("> ⚠️ **免責聲明**：以下研究卡僅供投資人查核反向證據與潛在下行風險，**完全不參與選股評分、排序或買賣建議**。所有資訊請以公司公開申報與官方公告為準。\n")
    for card in cards:
        lines.append(f"#### 📌 `{card['code']}` {card['name']}")
        lines.append(f"- **資料截止日**：{card.get('data_cutoff', card.get('as_of_date', ''))} ｜ **產生時間**：{card.get('as_of_date', '')}")
        flags_str = ', '.join(card.get('data_quality_flags', [])) or '無特定標記'
        lines.append(f"- **資料品質標記**：`{flags_str}`")
        lines.append("- **客觀事實**：")
        for fact in card.get('supporting_facts', []):
            lines.append(f"  - {fact}")
        if not card.get('supporting_facts'):
            lines.append("  - (無已核實之額外數據)")
        lines.append("- **反證與風險提示**：")
        for risk in card.get('counter_evidence_and_risks', []):
            lines.append(f"  - ⚠️ {risk}")
        lines.append("- **人工審查待確認事項**：")
        for q in card.get('questions_for_human_review', []):
            lines.append(f"  - ❓ {q}")
        if card.get('sources'):
            lines.append("- **參考來源**：")
            for src in card.get('sources', []):
                lines.append(f"  - [{src.get('publisher', '來源')}]({src.get('url', '#')}) ({src.get('source_type', 'unknown')}, {src.get('published_at', '')})")
        lines.append(f"> 免責聲明：{card.get('disclaimer', '此研究卡不參與評分、排序或投資建議。')}\n")

    new_content = top_part.rstrip() + "\n\n" + "\n".join(lines).strip() + "\n"
    md_file.write_text(new_content, encoding="utf-8")

DEPLOY_MOBILE_SCRIPT = ROOT_DIR / "deploy_mobile.py"
DEPLOY_MOBILE_LOCK = threading.Lock()


def _new_deploy_mobile_job():
    return {
        "running": False,
        "lines": [],
        "done": False,
        "returncode": None,
    }


deploy_mobile_job = _new_deploy_mobile_job()


def _run_deploy_mobile():
    global deploy_mobile_job
    child_env = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
    }
    try:
        proc = subprocess.Popen(
            [sys.executable, str(DEPLOY_MOBILE_SCRIPT)],
            cwd=ROOT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=child_env,
        )
    except OSError as e:
        with DEPLOY_MOBILE_LOCK:
            deploy_mobile_job["lines"].append(f"❌ 無法啟動 deploy_mobile.py: {e}")
            deploy_mobile_job["done"] = True
            deploy_mobile_job["running"] = False
        return

    for raw_line in proc.stdout:
        line = raw_line.rstrip("\n")
        with DEPLOY_MOBILE_LOCK:
            deploy_mobile_job["lines"].append(line)

    proc.wait()
    with DEPLOY_MOBILE_LOCK:
        deploy_mobile_job["done"] = True
        deploy_mobile_job["running"] = False
        deploy_mobile_job["returncode"] = proc.returncode


def _run_batch_scanner():
    global batch_scanner_job
    child_env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        proc = subprocess.Popen(
            [sys.executable, str(BATCH_SCANNER_SCRIPT)],
            cwd=ROOT_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", env=child_env,
        )
    except OSError as e:
        with BATCH_SCANNER_LOCK:
            batch_scanner_job["lines"].append(f"❌ 無法啟動 batch_scanner.py: {e}")
            batch_scanner_job["done"] = True
            batch_scanner_job["running"] = False
        return

    for raw_line in proc.stdout:
        line = raw_line.rstrip("\n")
        with BATCH_SCANNER_LOCK:
            batch_scanner_job["lines"].append(line)

    proc.wait()
    invalidate_all_caches()
    with BATCH_SCANNER_LOCK:
        batch_scanner_job["done"] = True
        batch_scanner_job["running"] = False
        batch_scanner_job["returncode"] = proc.returncode


def _run_batch_scanner_gemini():
    global batch_scanner_gemini_job
    child_env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        proc = subprocess.Popen(
            [sys.executable, str(BATCH_SCANNER_GEMINI_SCRIPT)],
            cwd=ROOT_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", env=child_env,
        )
    except OSError as e:
        with BATCH_SCANNER_GEMINI_LOCK:
            batch_scanner_gemini_job["lines"].append(f"❌ 無法啟動 batch_scanner_gemini.py: {e}")
            batch_scanner_gemini_job["done"] = True
            batch_scanner_gemini_job["running"] = False
        return

    for raw_line in proc.stdout:
        line = raw_line.rstrip("\n")
        with BATCH_SCANNER_GEMINI_LOCK:
            batch_scanner_gemini_job["lines"].append(line)

    proc.wait()
    invalidate_all_caches()
    with BATCH_SCANNER_GEMINI_LOCK:
        batch_scanner_gemini_job["done"] = True
        batch_scanner_gemini_job["running"] = False
        batch_scanner_gemini_job["returncode"] = proc.returncode


def _run_generate(args):
    """在背景執行緒裡跑 `python stock_report_generator.py <args...>`（跟直接在命令列
    輸入代號效果一樣，非互動模式跑完就結束），一邊讀 stdout 一邊解析進度/結果，讓網頁
    可以用輪詢的方式顯示「跑到第幾檔」，不用等全部跑完才有反應。stdout 不會像命令列
    那樣有視窗可以看，所以失敗時要把附近的錯誤訊息一起收集起來，最後回報給使用者，
    不能只說「失敗」卻不給理由。
    """
    global generate_job
    # stock_report_generator.py 自己有 sys.stdout.reconfigure(encoding="utf-8") 的防呆，
    # 但那個判斷式是看「目前是不是已經是 utf-8」，用 pipe 接起來時子行程有時候還是會拿到
    # Windows 系統的 cp950 當預設，一堆 emoji 進度訊息就會印到一半直接 UnicodeEncodeError
    # 掛掉。用環境變數強制指定，從一開始就不會有這個模糊地帶。
    child_env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        proc = subprocess.Popen(
            [sys.executable, str(GENERATOR_SCRIPT), *args],
            cwd=ROOT_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", env=child_env,
        )
    except OSError as e:
        with GENERATE_LOCK:
            generate_job["lines"].append(f"❌ 無法啟動 stock_report_generator.py: {e}")
            generate_job["done"] = True
            generate_job["running"] = False
        return

    segment_lines = []
    for raw_line in proc.stdout:
        line = raw_line.rstrip("\n")
        with GENERATE_LOCK:
            generate_job["lines"].append(line)
            generate_job["last_output_at"] = time.time()

        active_match = GENERATE_ACTIVE_RE.match(line)
        if active_match:
            with GENERATE_LOCK:
                generate_job["active"] = [x for x in active_match.group(1).split("、") if x]
            continue

        m = GENERATE_PROGRESS_RE.match(line)
        if m:
            segment_lines = []
            with GENERATE_LOCK:
                generate_job["progress"] = {"i": int(m.group(1)), "total": int(m.group(2)), "ticker": m.group(3)}
            continue

        m2 = GENERATE_RESULT_RE.match(line)
        if m2:
            ok = m2.group(1).startswith("✅")
            ticker = m2.group(4)
            reason = "；".join(l.strip() for l in segment_lines if "❌" in l) if not ok else ""
            with GENERATE_LOCK:
                generate_job["results"].append({"ticker": ticker, "ok": ok, "reason": reason})
            segment_lines = []
            continue

        if "❌" in line:
            segment_lines.append(line)

    proc.wait()
    invalidate_all_caches()
    with GENERATE_LOCK:
        generate_job["done"] = True
        generate_job["running"] = False
        generate_job["returncode"] = proc.returncode
        generate_job["finished_at"] = time.time()


def fetch_institutional_breakdown():
    """即時打 TWSE 官方 API，回傳大盤法人明細（略過自營商避險）。"""
    try:
        resp = requests.get("https://www.twse.com.tw/rwd/zh/fund/BFI82U?response=json", timeout=10)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        return None, f"連線 TWSE 失敗: {e}"

    if payload.get("stat") != "OK":
        return None, payload.get("stat") or "TWSE 回應異常"

    rows = []
    for item in payload.get("data", []):
        if len(item) < 4:
            continue
        label, buy, sell, net = item[0], item[1], item[2], item[3]
        if label == "外資自營商" or "自營商避險" in label or "自營商(避險)" in label:
            continue
        try:
            rows.append({
                "label": label,
                "buy": round(float(buy.replace(",", "")) / 1e8, 2),
                "sell": round(float(sell.replace(",", "")) / 1e8, 2),
                "net": round(float(net.replace(",", "")) / 1e8, 2),
            })
        except ValueError:
            continue

    raw_date = payload.get("date", "")
    date_str = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}" if len(raw_date) == 8 else raw_date
    return {"date": date_str, "rows": rows}, None


DISPOSAL_NOTICE_LOCK = threading.Lock()
DISPOSAL_NOTICE_CACHE = {"timestamp": 0, "data": None}
DISPOSAL_NOTICE_TTL = 300  # 5 分鐘快取


def _parse_roc_date_str(s):
    if not s:
        return ""
    s = str(s).strip().replace("*", "")
    m = re.match(r"(\d{2,3})[/-]?(\d{1,2})[/-]?(\d{1,2})", s)
    if m:
        roc_y, m_val, d_val = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{roc_y + 1911}-{m_val:02d}-{d_val:02d}"
    m8 = re.match(r"(\d{3})(\d{2})(\d{2})", s)
    if m8:
        roc_y, m_val, d_val = int(m8.group(1)), int(m8.group(2)), int(m8.group(3))
        return f"{roc_y + 1911}-{m_val:02d}-{d_val:02d}"
    m_ad = re.match(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", s)
    if m_ad:
        return f"{int(m_ad.group(1))}-{int(m_ad.group(2)):02d}-{int(m_ad.group(3)):02d}"
    return s


def _is_common_stock(code, name):
    """過濾只保留台股現股（排除 CB 可轉換公司債、權證等衍生商品）。"""
    code = str(code).strip()
    name = str(name).strip()
    # 權證 (6碼數字)
    if len(code) == 6 and code.isdigit():
        return False
    # 可轉債 (5碼數字，且通常為第 5 碼為發行期數)
    if len(code) == 5 and code.isdigit():
        return False
    # 排除超過 5 碼之衍生商品
    if len(code) > 5:
        return False
    # 排除名稱中明確包含權證或公司債關鍵字
    if re.search(r"(?:購\d{2}|售\d{2}|牛\d{2}|熊\d{2}|認購|認售|權證|公司債|可轉債|\bCB\b)", name):
        return False
    # 排除代碼非4碼且名稱結尾為中文數字（如 華星光三、一詮七、先進光一）
    if len(code) != 4 and re.search(r"[一二三四五六七八九十]$", name):
        return False
    # 台股現股代碼為 4 碼數字，或特別股 4碼+英文（如 2881A）
    return bool(re.match(r"^\d{4}[A-Za-z]?$", code))


def fetch_disposal_and_notice_data(force_refresh=False):
    """即時爬取證交所(TWSE)與櫃買中心(TPEx)的處置有價證券與注意股票（僅保留現股）。"""
    global DISPOSAL_NOTICE_CACHE
    now = time.time()
    with DISPOSAL_NOTICE_LOCK:
        if not force_refresh and DISPOSAL_NOTICE_CACHE["data"] and (now - DISPOSAL_NOTICE_CACHE["timestamp"] < DISPOSAL_NOTICE_TTL):
            return DISPOSAL_NOTICE_CACHE["data"], None

    today_str = datetime.date.today().strftime("%Y-%m-%d")
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    disposals = []
    notices = []
    stock_map = {}

    # 1. 上市處置 (TWSE)
    try:
        r = requests.get("https://openapi.twse.com.tw/v1/announcement/punish", headers=headers, timeout=5)
        if r.status_code == 200:
            for row in r.json():
                code = str(row.get("Code", "")).strip()
                name = str(row.get("Name", "")).strip()
                if not code or not _is_common_stock(code, name):
                    continue
                pub_date = _parse_roc_date_str(row.get("Date", ""))
                period_raw = str(row.get("DispositionPeriod", "")).strip()
                p_parts = re.split(r"[～~\-至]", period_raw)
                start_d = _parse_roc_date_str(p_parts[0]) if len(p_parts) > 0 else ""
                end_d = _parse_roc_date_str(p_parts[1]) if len(p_parts) > 1 else ""
                measures = str(row.get("DispositionMeasures", "")).strip()
                reasons = str(row.get("ReasonsOfDisposition", "")).strip()

                if start_d and start_d > today_str:
                    status = "upcoming"
                    status_label = "🚨 明日處置"
                elif start_d and end_d and start_d <= today_str <= end_d:
                    status = "active"
                    status_label = "🔒 處置中"
                else:
                    status = "ended"
                    status_label = "✅ 處置結束"

                item = {
                    "market": "TWSE",
                    "code": code,
                    "name": name,
                    "pub_date": pub_date,
                    "start_date": start_d,
                    "end_date": end_d,
                    "period_raw": period_raw,
                    "status": status,
                    "status_label": status_label,
                    "measures": measures,
                    "reasons": reasons,
                    "count": str(row.get("NumberOfAnnouncement", "1"))
                }
                disposals.append(item)
                stock_map[code] = {
                    "type": "disposal",
                    "code": code,
                    "name": name,
                    "status": status,
                    "status_label": status_label,
                    "start_date": start_d,
                    "end_date": end_d,
                    "period_raw": period_raw,
                    "measures": measures,
                    "reasons": reasons,
                    "market": "TWSE"
                }
    except Exception as e:
        print(f"[Warn] Fetch TWSE Punish Error: {e}", file=sys.stderr)

    # 2. 上櫃處置 (TPEx)
    try:
        r = requests.get("https://www.tpex.org.tw/www/zh-tw/bulletin/disposal/disp?response=json", headers=headers, timeout=5)
        if r.status_code == 200:
            d = r.json()
            if "tables" in d and d["tables"]:
                for row in d["tables"][0].get("data", []):
                    if len(row) < 6:
                        continue
                    pub_date = _parse_roc_date_str(row[1])
                    code_m = re.search(r"\b\d{4,6}\b", str(row[2]))
                    code = code_m.group(0) if code_m else str(row[2]).strip()
                    name_m = re.match(r"^[^\(<]+", str(row[3]))
                    name = name_m.group(0).strip() if name_m else str(row[3]).strip()
                    if not code or not _is_common_stock(code, name):
                        continue
                    count = str(row[4])
                    period_raw = str(row[5]).strip()
                    p_parts = re.split(r"[～~\-至]", period_raw)
                    start_d = _parse_roc_date_str(p_parts[0]) if len(p_parts) > 0 else ""
                    end_d = _parse_roc_date_str(p_parts[1]) if len(p_parts) > 1 else ""
                    reasons = str(row[6]).strip() if len(row) > 6 else ""
                    measures = str(row[7]).strip() if len(row) > 7 else ""

                    if start_d and start_d > today_str:
                        status = "upcoming"
                        status_label = "🚨 明日處置"
                    elif start_d and end_d and start_d <= today_str <= end_d:
                        status = "active"
                        status_label = "🔒 處置中"
                    else:
                        status = "ended"
                        status_label = "✅ 處置結束"

                    item = {
                        "market": "TPEx",
                        "code": code,
                        "name": name,
                        "pub_date": pub_date,
                        "start_date": start_d,
                        "end_date": end_d,
                        "period_raw": period_raw,
                        "status": status,
                        "status_label": status_label,
                        "measures": measures,
                        "reasons": reasons,
                        "count": count
                    }
                    disposals.append(item)
                    stock_map[code] = {
                        "type": "disposal",
                        "code": code,
                        "name": name,
                        "status": status,
                        "status_label": status_label,
                        "start_date": start_d,
                        "end_date": end_d,
                        "period_raw": period_raw,
                        "measures": measures,
                        "reasons": reasons,
                        "market": "TPEx"
                    }
    except Exception as e:
        print(f"[Warn] Fetch TPEx Punish Error: {e}", file=sys.stderr)

    # 3. 上市注意 (TWSE)
    try:
        r = requests.get("https://openapi.twse.com.tw/v1/announcement/notice", headers=headers, timeout=5)
        if r.status_code == 200:
            for row in r.json():
                code = str(row.get("Code", "")).strip()
                name = str(row.get("Name", "")).strip()
                if not code or not _is_common_stock(code, name):
                    continue
                info = str(row.get("NoticeInformation", "")).strip()
                notices.append({"market": "TWSE", "code": code, "name": name, "info": info, "count": "1"})
                if code not in stock_map:
                    stock_map[code] = {
                        "type": "notice",
                        "code": code,
                        "name": name,
                        "status": "notice",
                        "status_label": "👀 注意股",
                        "info": info,
                        "count": "1",
                        "market": "TWSE"
                    }
    except Exception as e:
        print(f"[Warn] Fetch TWSE Notice Error: {e}", file=sys.stderr)

    # 4. 上櫃注意 (TPEx)
    try:
        r = requests.get("https://www.tpex.org.tw/www/zh-tw/bulletin/attention/history?response=json", headers=headers, timeout=5)
        if r.status_code == 200:
            d = r.json()
            if "tables" in d and d["tables"]:
                for row in d["tables"][0].get("data", []):
                    if len(row) < 5:
                        continue
                    code_m = re.search(r"\b\d{4,6}\b", str(row[1]))
                    code = code_m.group(0) if code_m else str(row[1]).strip()
                    name_m = re.match(r"^[^\(<]+", str(row[2]))
                    name = name_m.group(0).strip() if name_m else str(row[2]).strip()
                    if not code or not _is_common_stock(code, name):
                        continue
                    count = str(row[3])
                    info = str(row[4]).strip()
                    notices.append({"market": "TPEx", "code": code, "name": name, "count": count, "info": info})
                    if code not in stock_map:
                        stock_map[code] = {
                            "type": "notice",
                            "code": code,
                            "name": name,
                            "status": "notice",
                            "status_label": f"👀 注意股(累積{count}次)" if count and count.isdigit() and int(count) > 1 else "👀 注意股",
                            "info": info,
                            "count": count,
                            "market": "TPEx"
                        }
    except Exception as e:
        print(f"[Warn] Fetch TPEx Notice Error: {e}", file=sys.stderr)

    result = {
        "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "today": today_str,
        "disposals": disposals,
        "notices": notices,
        "map": stock_map
    }
    with DISPOSAL_NOTICE_LOCK:
        DISPOSAL_NOTICE_CACHE["timestamp"] = now
        DISPOSAL_NOTICE_CACHE["data"] = result
    return result, None


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        super().end_headers()

    def _json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/rss-news":
            qs = parse_qs(parsed.query)
            codes = re.findall(r'\d{4,6}', ','.join(qs.get('code', []) + qs.get('codes', [])))
            range_key = (qs.get('range') or ['today'])[0]
            if not codes:
                self._json(400, {"ok": False, "error": "請輸入 4 至 6 碼股票代號"})
                return
            if range_key not in ('today', '3d'):
                self._json(400, {"ok": False, "error": "不支援的 RSS 時間範圍"})
                return
            self._json(200, {"ok": True, "items": fetch_rss_news(codes, range_key), "max_codes": RSS_MAX_CODES, "range": range_key})
            return
        if parsed.path == "/api/macro/data":
            try:
                self._json(200, {"ok": True, "entries": read_macro_data()})
            except (OSError, ValueError, json.JSONDecodeError) as error:
                self._json(500, {"ok": False, "error": f"總經資料讀取失敗: {error}"})
            return
        if parsed.path == "/api/macro/update-status":
            with MACRO_UPDATE_LOCK:
                job = dict(MACRO_UPDATE_JOB)
            self._json(200, {"ok": True, "status": read_macro_update_status(), "job": job})
            return
        if parsed.path == "/api/institutional-breakdown":
            data, error = fetch_institutional_breakdown()
            if data is not None:
                self._json(200, {"ok": True, "data": data})
            else:
                self._json(502, {"ok": False, "error": error})
            return
        if parsed.path == "/api/disposal-notice":
            qs = parse_qs(parsed.query)
            force = qs.get("refresh", ["0"])[0] == "1"
            data, error = fetch_disposal_and_notice_data(force_refresh=force)
            if data is not None:
                self._json(200, {"ok": True, "data": data})
            else:
                self._json(502, {"ok": False, "error": error or "無法取得處置/注意資訊"})
            return
        if parsed.path == "/api/new-stocks-today":
            self._json(200, {"ok": True, **read_today_new_stocks()})
            return
        if parsed.path == "/api/tree":
            self._json(200, {"ok": True, "tree": build_tree()})
            return
        if parsed.path == "/api/list":
            qs = parse_qs(parsed.query)
            rel_path = (qs.get("path") or [""])[0]
            try:
                target = resolve_safe_path(rel_path)
            except ValueError as e:
                self._json(400, {"ok": False, "error": str(e)})
                return
            if not target.is_dir():
                self._json(404, {"ok": False, "error": "資料夾不存在"})
                return
            folders, reports = list_folder(target)
            self._json(200, {"ok": True, "folders": folders, "reports": reports})
            return
        if parsed.path == "/api/list/recursive":
            qs = parse_qs(parsed.query)
            rel_path = (qs.get("path") or [""])[0]
            try:
                target = resolve_safe_path(rel_path)
            except ValueError as e:
                self._json(400, {"ok": False, "error": str(e)})
                return
            if not target.is_dir():
                self._json(404, {"ok": False, "error": "資料夾不存在"})
                return
            self._json(200, {"ok": True, "reports": list_reports_recursive(target)})
            return
        if parsed.path == "/api/cards":
            self._json(200, {"ok": True, "cards": read_stock_cards()})
            return
        if parsed.path == "/api/watchlist":
            self._json(200, {"ok": True, **read_watchlist()})
            return
        if parsed.path == "/api/servers-status":
            import socket
            def check_port(port):
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.15):
                        return True
                except OSError:
                    return False
            self._json(200, {
                "ok": True,
                "servers": {
                    "8935": {"name": "個股中心", "port": 8935, "online": True, "desc": "個股/看盤/榜單/總經"},
                    "8934": {"name": "總經資訊", "port": 8934, "online": check_port(8934), "desc": "獨立總經追蹤器"}
                }
            })
            return
        if parsed.path == "/api/reports-index":
            # 保持原 PatternViewer API 的裸陣列格式，第二階段元件化前即可直接換用
            # reports_manager_server.py，不必同步修改舊介面。
            self._json(200, build_reports_index())
            return
        if parsed.path == "/api/generate/status":
            with GENERATE_LOCK:
                self._json(200, {"ok": True, **generate_job})
            return
        if parsed.path == "/api/batch-scanner/status":
            with BATCH_SCANNER_LOCK:
                self._json(200, {"ok": True, **batch_scanner_job})
            return
        if parsed.path == "/api/batch-scanner-gemini/status":
            with BATCH_SCANNER_GEMINI_LOCK:
                self._json(200, {"ok": True, **batch_scanner_gemini_job})
            return
        if parsed.path == "/api/batch-scanner-evolution/status":
            with EVOLUTION_ENGINE_LOCK:
                self._json(200, {"ok": True, **evolution_engine_job})
            return
        if parsed.path == "/api/ai-research/status":
            with AI_RESEARCH_LOCK:
                if not LLM_HANDOFF_FILE.exists():
                    self._json(200, {
                        "ok": True,
                        "status": "idle",
                        "prompt_id": None,
                        "expected_codes": [],
                        "created_at": None,
                        "completed_at": None,
                        "error": None,
                        "events": list(AI_RESEARCH_EVENTS)
                    })
                    return
                try:
                    handoff = json.loads(LLM_HANDOFF_FILE.read_text(encoding="utf-8"))
                    self._json(200, {
                        "ok": True,
                        "status": handoff.get("status", "idle"),
                        "prompt_id": handoff.get("prompt_id"),
                        "expected_codes": handoff.get("expected_codes", []),
                        "created_at": handoff.get("created_at"),
                        "completed_at": handoff.get("completed_at"),
                        "error": handoff.get("error"),
                        "events": list(AI_RESEARCH_EVENTS)
                    })
                except Exception as error:
                    self._json(500, {"ok": False, "status": "error", "error": f"讀取狀態失敗: {error}"})
            return



        if parsed.path == "/api/llm-handoff":
            if not LLM_HANDOFF_FILE.exists():
                self._json(200, {"ok": True, "handoff": None})
                return
            try:
                handoff = json.loads(LLM_HANDOFF_FILE.read_text(encoding="utf-8"))
                self._json(200, {"ok": True, "handoff": handoff})
            except Exception as error:
                self._json(500, {"ok": False, "error": f"讀取人工接力資料失敗: {error}"})
            return
        if parsed.path == "/api/deploy-mobile/status":
            with DEPLOY_MOBILE_LOCK:
                self._json(200, {"ok": True, **deploy_mobile_job})
            return
        if parsed.path == "/api/markdown-report":
            qs = parse_qs(parsed.query)
            code = (qs.get("code") or [""])[0].strip()
            if not code:
                self._json(400, {"ok": False, "error": "缺少股票代號"})
                return
            pattern = str(REPORTS_DIR / "**" / f"{code}_*.md")
            matches = glob.glob(pattern, recursive=True)
            if not matches:
                all_mds = glob.glob(str(REPORTS_DIR / "**" / "*.md"), recursive=True)
                matches = [f for f in all_mds if f"{code}_" in os.path.basename(f) or os.path.basename(f).startswith(f"{code}")]
            if not matches:
                self._json(404, {"ok": False, "error": f"找不到 {code} 的 Markdown 分析報告（可點擊上方 Batch Scanner 產生）"})
                return
            matches.sort(key=os.path.getmtime, reverse=True)
            target_path = Path(matches[0])
            try:
                content = target_path.read_text(encoding="utf-8", errors="replace")
                rel_path = target_path.relative_to(REPORTS_DIR).as_posix()
                self._json(200, {
                    "ok": True,
                    "code": code,
                    "filename": target_path.name,
                    "relPath": rel_path,
                    "content": content,
                })
            except Exception as e:
                self._json(500, {"ok": False, "error": f"讀取報告失敗: {e}"})
            return
        if parsed.path in ("/evolution-log", "/evolution_log"):
            self.send_response(302)
            self.send_header("Location", "/evolution_log.html")
            self.end_headers()
            return
        if parsed.path == "/api/evolution-log":
            log_file = ROOT_DIR / "evolution_log.md"
            if not log_file.exists():
                self._json(404, {"ok": False, "error": "尚未找到實戰覆盤日記，請先執行「盤後進化」。"})
                return
            try:
                content = log_file.read_text(encoding="utf-8", errors="replace")
                mtime = os.path.getmtime(log_file)
                self._json(200, {"ok": True, "content": content, "mtime": mtime})
            except Exception as e:
                self._json(500, {"ok": False, "error": f"讀取覆盤日記失敗: {e}"})
            return

        if parsed.path == "/api/winrate-ranking-report":
            qs = parse_qs(parsed.query)
            source = (qs.get("source") or ["chatgpt"])[0].lower()
            if source == "gemini":
                ranking_file = ROOT_DIR / "stock_winrate_ranking_gemini.md"
            elif source in ("evolution", "ai", "exclusive"):
                ranking_file = ROOT_DIR / "stock_winrate_ranking_evolution.md"
            else:
                ranking_file = ROOT_DIR / "stock_winrate_ranking.md"

            if not ranking_file.exists():
                self._json(404, {"ok": False, "error": f"尚未找到 {ranking_file.name}，請先執行對應的 Scanner 進行全市場掃描產生。"})
                return
            try:
                content = ranking_file.read_text(encoding="utf-8", errors="replace")
                mtime = os.path.getmtime(ranking_file)
                self._json(200, {
                    "ok": True,
                    "source": source,
                    "filename": ranking_file.name,
                    "content": content,
                    "mtime": mtime,
                })
            except Exception as e:
                self._json(500, {"ok": False, "error": f"讀取排行榜失敗: {e}"})
            return
        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/rss-summary":
            try:
                body = self._read_json_body()
                code = str(body.get('code') or '').strip()
                range_key = str(body.get('range') or 'today')
                if not re.fullmatch(r'\d{4,6}', code):
                    raise ValueError('請輸入 4 至 6 碼股票代號')
                if range_key not in ('today', '3d'):
                    raise ValueError('不支援的 RSS 時間範圍')
                items = filter_rss_items(_fetch_stock_rss(code), range_key)
                self._json(200, {"ok": True, "code": code, "summary": summarize_rss_with_gemini(code, items)})
            except (ValueError, RuntimeError) as error:
                self._json(400, {"ok": False, "error": str(error)})
            return

        if parsed.path == "/api/shutdown":
            self._json(200, {"ok": True})
            _shutdown_application()
            return

        if parsed.path == "/api/client/heartbeat":
            try:
                body = self._read_json_body()
            except Exception:
                body = {}
            _record_client_heartbeat(str(body.get("clientId") or "").strip())
            self._json(200, {"ok": True})
            return

        if parsed.path == "/api/client/disconnect":
            try:
                body = self._read_json_body()
            except Exception:
                body = {}
            _record_client_disconnect(str(body.get("clientId") or "").strip())
            self._json(200, {"ok": True})
            return

        if parsed.path == "/api/macro/update":
            global MACRO_UPDATE_JOB
            if not MACRO_UPDATE_SCRIPT.exists():
                self._json(404, {"ok": False, "error": "找不到總經更新程式"})
                return
            with MACRO_UPDATE_LOCK:
                if MACRO_UPDATE_JOB["running"]:
                    self._json(409, {"ok": False, "error": "總經數據正在更新中，請稍候"})
                    return
                MACRO_UPDATE_JOB = _new_macro_update_job()
                MACRO_UPDATE_JOB["running"] = True
            threading.Thread(target=_run_macro_update, daemon=True).start()
            self._json(200, {"ok": True})
            return

        if parsed.path == "/api/macro/save":
            try:
                body = self._read_json_body()
                saved = write_macro_data(body.get("entries"))
            except (ValueError, json.JSONDecodeError) as error:
                self._json(400, {"ok": False, "error": str(error)})
                return
            except OSError as error:
                self._json(500, {"ok": False, "error": f"總經資料寫入失敗: {error}"})
                return
            self._json(200, {"ok": True, "entries": saved})
            return

        if parsed.path == "/api/batch-scanner":
            global batch_scanner_job
            with BATCH_SCANNER_LOCK:
                if batch_scanner_job["running"]:
                    self._json(409, {"ok": False, "error": "已經有 Batch Scanner 任務在執行，請稍候"})
                    return
                batch_scanner_job = _new_batch_scanner_job()
                batch_scanner_job["running"] = True
            threading.Thread(target=_run_batch_scanner, daemon=True).start()
            self._json(200, {"ok": True})
            return

        if parsed.path == "/api/batch-scanner-gemini":
            global batch_scanner_gemini_job
            with BATCH_SCANNER_GEMINI_LOCK:
                if batch_scanner_gemini_job["running"]:
                    self._json(409, {"ok": False, "error": "已經有 Gemini Scanner 任務在執行，請稍候"})
                    return
                batch_scanner_gemini_job = _new_batch_scanner_job()
                batch_scanner_gemini_job["running"] = True
            threading.Thread(target=_run_batch_scanner_gemini, daemon=True).start()
            self._json(200, {"ok": True})
            return

        if parsed.path == "/api/batch-scanner-evolution":
            self._json(410, {"ok": False, "error": "AI 選股功能已移除"})
            return
            global evolution_engine_job
            try:
                body = self._read_json_body()
            except Exception:
                body = {}
            research_param = body.get("research")
            if research_param is None:
                qs = parse_qs(parsed.query)
                if "research" in qs:
                    research_param = qs.get("research", ["1"])[0].lower() not in ("0", "false", "no")
                else:
                    research_param = True
            else:
                research_param = bool(research_param)

            with EVOLUTION_ENGINE_LOCK:
                if evolution_engine_job["running"]:
                    self._json(409, {"ok": False, "error": "AI 進化/研究引擎正在執行中，請稍候"})
                    return
                evolution_engine_job = _new_batch_scanner_job()
                evolution_engine_job["running"] = True
                evolution_engine_job["research_enabled"] = research_param
                evolution_engine_job["started_at"] = time.time()
                evolution_engine_job["last_output_at"] = time.time()
                evolution_engine_job["debug_events"].append(
                    f"API 已接收啟動請求；research={research_param}"
                )
            threading.Thread(target=_run_evolution_engine, args=(research_param,), daemon=True).start()
            self._json(200, {"ok": True, "research_enabled": research_param})
            return

        if parsed.path == "/api/ai-research/start":
            self._json(410, {"ok": False, "error": "AI 選股功能已移除"})
            return
            with AI_RESEARCH_LOCK:
                as_of_date, candidates = load_evolution_candidates()
                if not candidates:
                    self._json(400, {"ok": False, "status": "error", "error": "尚未有量化候選名單，請先計算量化排名"})
                    return
                import uuid
                prompt_id = uuid.uuid4().hex[:12]
                prompt_text = build_ai_research_prompt(candidates, as_of_date)
                now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                handoff = {
                    "status": "awaiting_response",
                    "prompt_id": prompt_id,
                    "kind": "AI 反證與風險研究卡",
                    "expected_codes": [c["code"] for c in candidates],
                    "prompt": prompt_text,
                    "created_at": now_str,
                    "completed_at": None,
                    "error": None
                }
                LLM_HANDOFF_FILE.write_text(json.dumps(handoff, ensure_ascii=False, indent=2), encoding="utf-8")
                AI_RESEARCH_EVENTS.clear()
                _log_ai_research_event("Prompt 已建立", f"標的數量: {len(candidates)}, prompt_id: #{prompt_id[:8]}")
                self._json(200, {
                    "ok": True,
                    "status": "awaiting_response",
                    "prompt_id": prompt_id,
                    "expected_codes": handoff["expected_codes"],
                    "count": len(candidates),
                    "created_at": now_str,
                    "prompt": prompt_text
                })
            return

        if parsed.path == "/api/ai-research/submit":
            self._json(410, {"ok": False, "error": "AI 選股功能已移除"})
            return
            try:
                body = self._read_json_body()
                prompt_id = str(body.get("prompt_id") or "").strip()
                response_text = str(body.get("response") or "").strip()
                if not response_text:
                    self._json(400, {"ok": False, "status": "error", "error": "請貼上 AI 回覆的 JSON"})
                    return

                with AI_RESEARCH_LOCK:
                    if not LLM_HANDOFF_FILE.exists():
                        self._json(400, {"ok": False, "status": "error", "error": "尚未建立 Prompt，請先按產生 Prompt"})
                        return
                    handoff = json.loads(LLM_HANDOFF_FILE.read_text(encoding="utf-8"))
                    if handoff.get("prompt_id") != prompt_id:
                        _log_ai_research_event("驗證失敗", "Prompt 已更新，請使用最新 Prompt")
                        self._json(400, {"ok": False, "status": "error", "error": "Prompt 已更新，請使用最新 Prompt"})
                        return

                    _log_ai_research_event("收到回覆", f"字元數: {len(response_text)}")
                    cleaned = re.sub(r'^```(?:json)?\s*|\s*```$', '', response_text, flags=re.I | re.S).strip()
                    try:
                        parsed_response = json.loads(cleaned)
                    except json.JSONDecodeError as err:
                        handoff["status"] = "error"
                        handoff["error"] = f"JSON 語法錯誤：{err}"
                        LLM_HANDOFF_FILE.write_text(json.dumps(handoff, ensure_ascii=False, indent=2), encoding="utf-8")
                        _log_ai_research_event("JSON 解析失敗", str(err))
                        self._json(400, {"ok": False, "status": "error", "error": f"JSON 語法錯誤：{err}"})
                        return

                    _log_ai_research_event("JSON 解析成功")

                    try:
                        cards = validate_ai_research_cards(parsed_response, handoff.get("expected_codes", []))
                    except ValueError as err:
                        handoff["status"] = "error"
                        handoff["error"] = str(err)
                        LLM_HANDOFF_FILE.write_text(json.dumps(handoff, ensure_ascii=False, indent=2), encoding="utf-8")
                        _log_ai_research_event("股票完整性驗證失敗", str(err))
                        self._json(400, {"ok": False, "status": "error", "error": str(err)})
                        return

                    _log_ai_research_event("股票完整性通過", f"共 {len(cards)} 檔研究卡")

                    save_ai_research_cards_atomically(cards)
                    _log_ai_research_event("研究卡已保存", "已原子化寫入 ai_research_cards.json")

                    update_evolution_ranking_md_research_cards(cards)
                    _log_ai_research_event("畫面已更新", "已更新排行榜研究卡區塊，量化排名未受影響")

                    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    handoff["status"] = "completed"
                    handoff["completed_at"] = now_str
                    handoff["error"] = None
                    LLM_HANDOFF_FILE.write_text(json.dumps(handoff, ensure_ascii=False, indent=2), encoding="utf-8")

                    self._json(200, {
                        "ok": True,
                        "status": "completed",
                        "saved_count": len(cards),
                        "completed_at": now_str
                    })
            except Exception as e:
                self._json(500, {"ok": False, "status": "error", "error": f"處理回覆失敗: {e}"})
            return

        if parsed.path == "/api/llm-handoff":
            try:
                body = self._read_json_body()
                prompt_id = str(body.get("prompt_id") or "").strip()
                response_text = str(body.get("response") or "").strip()
                if not LLM_HANDOFF_FILE.exists():
                    raise ValueError("目前沒有等待回覆的 Prompt")
                handoff = json.loads(LLM_HANDOFF_FILE.read_text(encoding="utf-8"))
                if handoff.get("status") != "awaiting_response" or handoff.get("prompt_id") != prompt_id:
                    raise ValueError("Prompt 已更新，請重新複製最新內容")
                cleaned = re.sub(r'^```(?:json)?\s*|\s*```$', '', response_text, flags=re.I | re.S).strip()
                parsed_response = json.loads(cleaned)
                validate_manual_llm_response(handoff, parsed_response)
                handoff["response"] = json.dumps(parsed_response, ensure_ascii=False)
                handoff["status"] = "ready"
                handoff["submitted_at"] = datetime.datetime.now().isoformat(timespec="seconds")
                LLM_HANDOFF_FILE.write_text(json.dumps(handoff, ensure_ascii=False, indent=2), encoding="utf-8")
            except (ValueError, json.JSONDecodeError) as error:
                self._json(400, {"ok": False, "error": f"回覆格式錯誤：{error}"})
                return
            with EVOLUTION_ENGINE_LOCK:
                if evolution_engine_job["running"]:
                    self._json(409, {"ok": False, "error": "AI 進化/研究引擎仍在執行中"})
                    return
                evolution_engine_job = _new_batch_scanner_job()
                evolution_engine_job["running"] = True
                evolution_engine_job["research_enabled"] = True
                evolution_engine_job["started_at"] = time.time()
                evolution_engine_job["last_output_at"] = time.time()
            threading.Thread(target=_run_evolution_engine, args=(True,), daemon=True).start()
            self._json(200, {"ok": True, "restarted": True, "research_enabled": True})
            return

        if parsed.path == "/api/deploy-mobile":
            global deploy_mobile_job
            with DEPLOY_MOBILE_LOCK:
                if deploy_mobile_job["running"]:
                    self._json(409, {"ok": False, "error": "已經有手機版發布任務在執行中，請稍候"})
                    return
                deploy_mobile_job = _new_deploy_mobile_job()
                deploy_mobile_job["running"] = True
            threading.Thread(target=_run_deploy_mobile, daemon=True).start()
            self._json(200, {"ok": True})
            return

        if parsed.path == "/api/generate":
            global generate_job
            try:
                body = self._read_json_body()
            except Exception:
                self._json(400, {"ok": False, "error": "無效的請求內容"})
                return
            raw = (body.get("input") or "").strip()
            if not raw:
                self._json(400, {"ok": False, "error": "請輸入股票代號/名稱，或輸入 ALL"})
                return
            with GENERATE_LOCK:
                if generate_job["running"]:
                    self._json(409, {"ok": False, "error": "已經有報表產生任務在執行，請稍候"})
                    return
                parts = [p for p in re.split(r"[,，、\s]+", raw) if p]
                args = ["ALL" if p.upper() in ("ALL", "全部") else p for p in parts]
                generate_job = _new_generate_job()
                generate_job["running"] = True
                generate_job["started_at"] = time.time()
                generate_job["last_output_at"] = time.time()
            threading.Thread(target=_run_generate, args=(args,), daemon=True).start()
            self._json(200, {"ok": True})
            return

        if parsed.path == "/api/watchlist":
            try:
                body = self._read_json_body()
                starred_input = body.get("starred", []) if isinstance(body, dict) else body
                saved = write_watchlist(starred_input)
            except (ValueError, json.JSONDecodeError) as e:
                self._json(400, {"ok": False, "error": str(e)})
                return
            except OSError as e:
                self._json(500, {"ok": False, "error": f"關注清單寫入失敗: {e}"})
                return
            self._json(200, {"ok": True, **saved})
            return

        if parsed.path == "/api/new-stocks-today":
            try:
                body = self._read_json_body()
            except Exception:
                body = {}
            codes = body.get("codes") or ([body.get("code")] if body.get("code") else [])
            saved = record_today_new_stocks(codes)
            self._json(200, {"ok": True, **saved})
            return

        try:
            body = self._read_json_body()
        except Exception:
            self._json(400, {"ok": False, "error": "無效的請求內容"})
            return

        try:
            if parsed.path == "/api/folder/create":
                parent = resolve_safe_path(body.get("parentPath", ""))
                name = sanitize_folder_name(body.get("name"))
                target = parent / name
                if target.exists():
                    self._json(409, {"ok": False, "error": "同名資料夾已存在"})
                    return
                target.mkdir(parents=False)
                invalidate_all_caches()
                self._json(200, {"ok": True})
                return

            if parsed.path == "/api/folder/rename":
                target = resolve_safe_path(body.get("path", ""))
                if target == REPORTS_DIR.resolve():
                    self._json(400, {"ok": False, "error": "不能重新命名根目錄"})
                    return
                new_name = sanitize_folder_name(body.get("newName"))
                dest = target.parent / new_name
                if dest.exists():
                    self._json(409, {"ok": False, "error": "同名資料夾已存在"})
                    return
                target.rename(dest)
                invalidate_all_caches()
                self._json(200, {"ok": True})
                return

            if parsed.path == "/api/folder/delete":
                target = resolve_safe_path(body.get("path", ""))
                if target == REPORTS_DIR.resolve():
                    self._json(400, {"ok": False, "error": "不能刪除根目錄"})
                    return
                if not target.is_dir():
                    self._json(404, {"ok": False, "error": "資料夾不存在"})
                    return
                has_contents = any(target.iterdir())
                if has_contents and not body.get("force"):
                    self._json(409, {
                        "ok": False,
                        "error": "資料夾內還有內容",
                        "reportCount": count_contents(target),
                    })
                    return
                shutil.rmtree(target)
                invalidate_all_caches()
                self._json(200, {"ok": True})
                return

            if parsed.path == "/api/report/move":
                src_dir = resolve_safe_path(body.get("from", ""))
                dest_dir = resolve_safe_path(body.get("to", ""))
                base = (body.get("base") or "").strip()
                code = (body.get("code") or "").strip()
                if not base and not code:
                    self._json(400, {"ok": False, "error": "無效的報表名稱或股票代號"})
                    return
                if base and any(ch in base for ch in FORBIDDEN_NAME_CHARS):
                    self._json(400, {"ok": False, "error": "無效的報表名稱"})
                    return
                if src_dir == dest_dir:
                    self._json(200, {"ok": True, "message": "來源與目標相同，略過移動"})
                    return
                if not dest_dir.is_dir():
                    self._json(404, {"ok": False, "error": "目標資料夾不存在"})
                    return

                html_name = (base + ".html") if base else ""
                chart_name = (base + "_chart.png") if base else ""
                src_html = (src_dir / html_name) if html_name else None
                src_chart = (src_dir / chart_name) if chart_name else None

                if (not src_html or not src_html.exists()) and code:
                    html_matches = list(src_dir.glob(f"{code}_*.html"))
                    if html_matches:
                        src_html = html_matches[0]
                        html_name = src_html.name
                        base = src_html.stem
                        chart_name = base + "_chart.png"
                        src_chart = src_dir / chart_name

                if not code and base:
                    m = TRACKED_FILENAME_RE.match(html_name or (base + ".html"))
                    if m:
                        code = m.group(1)
                    else:
                        parts = base.split("_")
                        if parts and 2 <= len(parts[0]) <= 6:
                            code = parts[0]

                files_to_move = []
                if src_html and src_html.exists():
                    files_to_move.append(src_html)
                if src_chart and src_chart.exists():
                    files_to_move.append(src_chart)

                if code:
                    for md_file in src_dir.glob(f"{code}_*.md"):
                        if md_file not in files_to_move:
                            files_to_move.append(md_file)
                    for extra_chart in src_dir.glob(f"{code}_*_chart.png"):
                        if extra_chart not in files_to_move:
                            files_to_move.append(extra_chart)

                if not files_to_move:
                    self._json(404, {"ok": False, "error": "找不到可移動的來源報表檔案"})
                    return

                conflicts = [f.name for f in files_to_move if (dest_dir / f.name).exists()]
                if conflicts:
                    self._json(409, {
                        "ok": False,
                        "error": "目標資料夾已有同名檔案，為避免覆蓋已取消移動",
                        "conflicts": conflicts,
                    })
                    return

                moved_names = []
                for f in files_to_move:
                    dest_file = dest_dir / f.name
                    try:
                        shutil.move(str(f), str(dest_file))
                        moved_names.append(f.name)
                    except Exception as e:
                        print(f"[move-error] 移動 {f.name} 失敗: {e}")

                invalidate_all_caches()
                self._json(200, {"ok": True, "moved": moved_names})
                return

            if parsed.path == "/api/report/delete":
                code = (body.get("code") or "").strip()
                folder = body.get("folder") if "folder" in body else body.get("path")
                base = (body.get("base") or "").strip()
                if not code and base:
                    m = TRACKED_FILENAME_RE.match(base + ".html")
                    if m:
                        code = m.group(1)
                    else:
                        parts = base.split("_")
                        if parts and 2 <= len(parts[0]) <= 6:
                            code = parts[0]

                if not code and not base:
                    self._json(400, {"ok": False, "error": "無效的股票代號或報表名稱"})
                    return

                target_dir = resolve_safe_path(folder) if folder is not None else None
                matches = set()
                if target_dir is not None:
                    if code:
                        matches.update(target_dir.glob(f"{code}_*.html"))
                        matches.update(target_dir.glob(f"{code}_*.md"))
                        matches.update(target_dir.glob(f"{code}_*_chart.png"))
                    if base:
                        f_html = target_dir / f"{base}.html"
                        if f_html.exists():
                            matches.add(f_html)
                        f_chart = target_dir / f"{base}_chart.png"
                        if f_chart.exists():
                            matches.add(f_chart)
                else:
                    if code:
                        matches.update(REPORTS_DIR.rglob(f"{code}_*.html"))
                        matches.update(REPORTS_DIR.rglob(f"{code}_*.md"))
                        matches.update(REPORTS_DIR.rglob(f"{code}_*_chart.png"))
                    if base:
                        matches.update(REPORTS_DIR.rglob(f"{base}.html"))
                        matches.update(REPORTS_DIR.rglob(f"{base}_chart.png"))

                deleted = 0
                deleted_names = []
                for path_obj in sorted(matches):
                    try:
                        if path_obj.exists():
                            path_obj.unlink()
                            deleted += 1
                            deleted_names.append(path_obj.name)
                    except OSError:
                        pass
                invalidate_all_caches()
                self._json(200, {"ok": True, "deletedFiles": deleted, "deletedNames": deleted_names})
                return

            if parsed.path == "/api/report/batch-move":
                items = body.get("items", [])
                default_to = body.get("to", "")
                if not items:
                    self._json(400, {"ok": False, "error": "沒有指定要移動的項目"})
                    return
                all_moved = []
                errors = []
                for item in items:
                    src_dir_path = item.get("from", "")
                    dest_dir_path = item.get("to") or default_to
                    base = (item.get("base") or "").strip()
                    code = (item.get("code") or "").strip()
                    try:
                        src_dir = resolve_safe_path(src_dir_path)
                        dest_dir = resolve_safe_path(dest_dir_path)
                        if src_dir == dest_dir:
                            continue
                        if not dest_dir.is_dir():
                            errors.append(f"{base or code}: 目標資料夾不存在")
                            continue
                        files_to_move = []
                        if base:
                            h = src_dir / f"{base}.html"
                            c = src_dir / f"{base}_chart.png"
                            if h.exists():
                                files_to_move.append(h)
                            if c.exists():
                                files_to_move.append(c)
                        if not code and base:
                            parts = base.split("_")
                            if parts and 2 <= len(parts[0]) <= 6:
                                code = parts[0]
                        if code:
                            for h in src_dir.glob(f"{code}_*.html"):
                                if h not in files_to_move:
                                    files_to_move.append(h)
                            for m in src_dir.glob(f"{code}_*.md"):
                                if m not in files_to_move:
                                    files_to_move.append(m)
                            for c in src_dir.glob(f"{code}_*_chart.png"):
                                if c not in files_to_move:
                                    files_to_move.append(c)
                        conflicts = [f.name for f in files_to_move if (dest_dir / f.name).exists()]
                        if conflicts:
                            errors.append(f"{base or code}: 目標已有同名檔案（未移動）")
                            continue
                        for f in files_to_move:
                            df = dest_dir / f.name
                            shutil.move(str(f), str(df))
                            all_moved.append(f.name)
                    except Exception as e:
                        errors.append(f"{base or code}: {e}")
                invalidate_all_caches()
                self._json(200, {"ok": True, "moved": all_moved, "errors": errors})
                return

            if parsed.path == "/api/report/batch-delete":
                items = body.get("items", [])
                if not items:
                    self._json(400, {"ok": False, "error": "沒有指定要刪除的項目"})
                    return
                all_deleted = []
                for item in items:
                    folder = item.get("folder") if "folder" in item else item.get("path")
                    code = (item.get("code") or "").strip()
                    base = (item.get("base") or "").strip()
                    if not code and base:
                        parts = base.split("_")
                        if parts and 2 <= len(parts[0]) <= 6:
                            code = parts[0]
                    target_dir = resolve_safe_path(folder) if folder is not None else None
                    matches = set()
                    if target_dir is not None:
                        if code:
                            matches.update(target_dir.glob(f"{code}_*.html"))
                            matches.update(target_dir.glob(f"{code}_*.md"))
                            matches.update(target_dir.glob(f"{code}_*_chart.png"))
                        if base:
                            f_html = target_dir / f"{base}.html"
                            if f_html.exists():
                                matches.add(f_html)
                    else:
                        if code:
                            matches.update(REPORTS_DIR.rglob(f"{code}_*.html"))
                            matches.update(REPORTS_DIR.rglob(f"{code}_*.md"))
                            matches.update(REPORTS_DIR.rglob(f"{code}_*_chart.png"))
                    for path_obj in matches:
                        try:
                            if path_obj.exists():
                                path_obj.unlink()
                                all_deleted.append(path_obj.name)
                        except OSError:
                            pass
                invalidate_all_caches()
                self._json(200, {"ok": True, "deletedFiles": len(all_deleted), "deletedNames": all_deleted})
                return

            self._json(404, {"ok": False, "error": "未知的 API"})
        except ValueError as e:
            self._json(400, {"ok": False, "error": str(e)})
        except OSError as e:
            self._json(500, {"ok": False, "error": f"檔案系統錯誤: {e}"})

    def log_message(self, fmt, *args):
        print("[reports-manager]", fmt % args)


class ReportsManagerServer(ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    os.chdir(ROOT_DIR)
    _stop_legacy_macro_server()
    with ReportsManagerServer(("", PORT), Handler) as httpd:
        threading.Thread(target=_client_watchdog, daemon=True).start()
        print(f"Serving {ROOT_DIR} at http://localhost:{PORT}/reports_manager.html")
        httpd.serve_forever()
