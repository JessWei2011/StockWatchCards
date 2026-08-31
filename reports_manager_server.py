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
WATCHLIST_FILE = ROOT_DIR / "watchlist.json"
MACRO_DIR = ROOT_DIR / "指標數據"
MACRO_DATA_FILE = MACRO_DIR / "macro_data.json"
MACRO_STATUS_FILE = MACRO_DIR / "macro_update_status.json"
MACRO_UPDATE_SCRIPT = MACRO_DIR / "update_macro_data.py"
PORT = 8935

REPORTS_DIR.mkdir(exist_ok=True)

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
if STOCK_NAME_DICT_PATH.exists():
    try:
        STOCK_NAME_DICT = json.loads(STOCK_NAME_DICT_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass


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

    group = md_path.parent.name if md_path.parent != REPORTS_DIR else "未分類"
    return {
        "code": code,
        "name": name,
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
            name = STOCK_NAME_DICT.get(code, m.group(2)) if (m and code) else (m.group(2) if m else None)
            reports.append({
                "base": base,
                "code": code,
                "name": name,
                "html": entry,
                "chart": chart_name if (path / chart_name).exists() else None,
                "mtime": full.stat().st_mtime,
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
            if not fn.lower().endswith(".html"):
                continue
            base = fn[:-5]
            if base in seen_bases:
                continue
            seen_bases.add(base)
            m = TRACKED_FILENAME_RE.match(fn)
            if m:
                code = m.group(1)
                name = STOCK_NAME_DICT.get(code, m.group(2))
                reports.append({"base": base, "code": code, "name": name})
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
            name = STOCK_NAME_DICT.get(code, raw_name)
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


def attach_report_flows(cards_by_code):
    """將最新報表的基本面、法人與融資券資料附加到卡片 API。"""
    reports_by_code = {item["code"]: item for item in build_reports_index()}
    for code, card in cards_by_code.items():
        report = reports_by_code.get(code)
        if not report:
            card["pe"] = None
            card["institutionalFlow"] = []
            card["marginFlow"] = []
            continue
        try:
            report_text = (REPORTS_DIR / report["path"]).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            card["pe"] = None
            card["institutionalFlow"] = []
            card["marginFlow"] = []
            continue
        card["pe"] = _report_pe(report_text)
        institutional = _report_table_rows(
            report_text, "三大法人", ("foreign", "trust", "dealer", "total")
        )[:15]
        close_prices = _report_close_prices(report_text)
        for row in institutional:
            row["close"] = close_prices.get(row["date"][5:])
        card["institutionalFlow"] = institutional
        card["marginFlow"] = _report_table_rows(
            report_text, "融資融券", ("marginBalance", "marginChange", "shortBalance", "shortChange")
        )[:5]


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


def _new_generate_job():
    return {
        "running": False,
        "lines": [],
        "progress": {"i": 0, "total": 0, "ticker": ""},
        "results": [],
        "done": False,
        "returncode": None,
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
    }


batch_scanner_job = _new_batch_scanner_job()

BATCH_SCANNER_GEMINI_SCRIPT = ROOT_DIR / "batch_scanner_gemini.py"
BATCH_SCANNER_GEMINI_LOCK = threading.Lock()
batch_scanner_gemini_job = _new_batch_scanner_job()

DEPLOY_MOBILE_BAT = ROOT_DIR / "發布手機版.bat"
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
        "PATH": r"C:\Users\User\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin;" + os.environ.get("PATH", "")
    }
    try:
        proc = subprocess.Popen(
            ["cmd.exe", "/c", str(DEPLOY_MOBILE_BAT), "--no-pause"],
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
            deploy_mobile_job["lines"].append(f"❌ 無法啟動 發布手機版.bat: {e}")
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
        if parsed.path == "/api/winrate-ranking-report":
            qs = parse_qs(parsed.query)
            source = (qs.get("source") or ["chatgpt"])[0].lower()
            if source == "gemini":
                ranking_file = ROOT_DIR / "stock_winrate_ranking_gemini.md"
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
                base = body.get("base", "")
                if not base or any(ch in base for ch in FORBIDDEN_NAME_CHARS):
                    self._json(400, {"ok": False, "error": "無效的報表名稱"})
                    return
                if src_dir == dest_dir:
                    self._json(200, {"ok": True})
                    return
                if not dest_dir.is_dir():
                    self._json(404, {"ok": False, "error": "目標資料夾不存在"})
                    return

                html_name = base + ".html"
                chart_name = base + "_chart.png"
                src_html = src_dir / html_name
                src_chart = src_dir / chart_name
                if not src_html.exists():
                    self._json(404, {"ok": False, "error": "找不到來源報表"})
                    return
                if (dest_dir / html_name).exists() or (dest_dir / chart_name).exists():
                    self._json(409, {"ok": False, "error": "目標資料夾已有同名報表，請先處理後再移動"})
                    return

                shutil.move(str(src_html), str(dest_dir / html_name))
                if src_chart.exists():
                    shutil.move(str(src_chart), str(dest_dir / chart_name))
                m = TRACKED_FILENAME_RE.match(html_name)
                if m:
                    code = m.group(1)
                    for md_file in src_dir.glob(f"{code}_*.md"):
                        try:
                            shutil.move(str(md_file), str(dest_dir / md_file.name))
                        except Exception:
                            pass
                invalidate_all_caches()
                self._json(200, {"ok": True})
                return

            if parsed.path == "/api/report/delete":
                code = (body.get("code") or "").strip()
                if not code or any(ch in code for ch in FORBIDDEN_NAME_CHARS):
                    self._json(400, {"ok": False, "error": "無效的股票代號"})
                    return
                # 用代號遞迴找整個 reports/ (含所有子資料夾)，不用先知道報表放在哪個分類，
                # 排行榜上的股票不一定跟目前選取的資料夾在同一層。只刪報表檔本身
                # (html+md，若有殘留 png 也一併清理)。
                matches = (
                    list(REPORTS_DIR.rglob(f"{code}_*.html"))
                    + list(REPORTS_DIR.rglob(f"{code}_*.md"))
                    + list(REPORTS_DIR.rglob(f"{code}_*_chart.png"))
                )
                deleted = 0
                for path_obj in matches:
                    try:
                        path_obj.unlink(missing_ok=True)
                        deleted += 1
                    except OSError:
                        pass
                invalidate_all_caches()
                self._json(200, {"ok": True, "deletedFiles": deleted})
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
