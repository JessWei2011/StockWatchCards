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

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT_DIR = Path(__file__).resolve().parent
REPORTS_DIR = ROOT_DIR / "reports"
DATA_JS_FILE = ROOT_DIR / "data.js"
AI_RANKINGS_FILE = ROOT_DIR / "ai_rankings.json"
PORT = 8935

REPORTS_DIR.mkdir(exist_ok=True)

TRACKED_FILENAME_RE = re.compile(r'^([0-9A-Za-z]{2,6})_(.+?)\((TW|TWO)\)')
FORBIDDEN_NAME_CHARS = set('\\/:*?"<>|')
STOCK_CARDS_RE = re.compile(r"const\s+STOCK_CARDS\s*=\s*(\[.*\])\s*;?\s*$", re.S)


def read_ai_rankings():
    try:
        payload = json.loads(AI_RANKINGS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"version": 1, "rankings": []}
    rankings = payload.get("rankings") if isinstance(payload, dict) else None
    return {"version": 1, "rankings": rankings if isinstance(rankings, list) else []}


def validate_ai_ranking(entry):
    if not isinstance(entry, dict):
        raise ValueError("排行榜資料必須是 JSON 物件")
    date = str(entry.get("date") or "").strip()
    ai = str(entry.get("ai") or "").strip()
    top5 = entry.get("top5")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise ValueError("date 必須是 YYYY-MM-DD")
    if not ai or len(ai) > 50:
        raise ValueError("ai 名稱不可空白且最多 50 字")
    if not isinstance(top5, list) or len(top5) != 5:
        raise ValueError("top5 必須剛好包含 5 筆")
    cleaned = []
    seen_codes = set()
    seen_ranks = set()
    for item in top5:
        if not isinstance(item, dict):
            raise ValueError("top5 每一筆都必須是物件")
        try:
            rank = int(item.get("rank"))
        except (TypeError, ValueError):
            raise ValueError("rank 必須是 1 到 5")
        code = str(item.get("code") or "").strip()
        name = str(item.get("name") or "").strip()
        decision = str(item.get("decision") or "").strip()
        reason = str(item.get("reason") or "").strip()
        score = item.get("score")
        if rank not in range(1, 6) or rank in seen_ranks:
            raise ValueError("rank 必須為不重複的 1 到 5")
        if not re.fullmatch(r"[0-9A-Za-z]{2,8}", code) or code in seen_codes:
            raise ValueError("股票代號不可空白、重複或含特殊字元")
        if not name or not reason:
            raise ValueError("每筆必須包含股票名稱與推薦原因")
        if score is not None:
            try:
                score = float(score)
            except (TypeError, ValueError):
                raise ValueError("score 必須是數字或 null")
        seen_ranks.add(rank)
        seen_codes.add(code)
        cleaned.append({
            "rank": rank, "code": code, "name": name[:50], "score": score,
            "decision": decision[:30], "reason": reason[:1000],
        })
    cleaned.sort(key=lambda item: item["rank"])
    return {"date": date, "ai": ai, "top5": cleaned}


def upsert_ai_ranking(entry):
    cleaned = validate_ai_ranking(entry)
    payload = read_ai_rankings()
    rankings = [item for item in payload["rankings"]
                if not (item.get("date") == cleaned["date"] and item.get("ai") == cleaned["ai"])]
    rankings.append(cleaned)
    rankings.sort(key=lambda item: (item.get("date", ""), item.get("ai", "")))
    payload = {"version": 1, "rankings": rankings}
    temp_path = AI_RANKINGS_FILE.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(AI_RANKINGS_FILE)
    return cleaned


def read_stock_cards():
    """讀 data.js 拿目前所有 AI 分析卡片，用股票代號當 key（同代號取最新日期那筆）。

    data.js 是 `const STOCK_CARDS = [...];` 包裝的 JS 檔，但 STOCK_CARDS 這個陣列本身
    是用雙引號字串 + \\n 寫的（不是 JS 專屬的 backtick 樣板字串），所以拿掉頭尾這層
    JS 變數宣告的包裝之後，內容本身就是合法 JSON，可以直接 json.loads，不用真的去跑
    JS engine 解析。如果以後改成 backtick 或塞了非字面值的 JS 表達式，這裡就會解析失敗
    (回傳空字典)，不會噴錯把整個檔案系統弄掛。
    """
    try:
        text = DATA_JS_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    m = STOCK_CARDS_RE.search(text)
    if not m:
        return {}
    try:
        cards = json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}

    by_code = {}
    for c in cards:
        code = c.get("code")
        if not code:
            continue
        existing = by_code.get(code)
        if not existing or c.get("date", "") > existing.get("date", ""):
            by_code[code] = c
    attach_report_flows(by_code)
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
    return {"name": path.name if rel else "reports", "path": rel, "children": children, "reports": reports}


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
            reports.append({
                "base": base,
                "code": m.group(1) if m else None,
                "name": m.group(2) if m else None,
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
                reports.append({"base": base, "code": m.group(1), "name": m.group(2)})
    return reports


def build_reports_index():
    """建立型態教學使用的全域報表索引。

    reports/ 允許使用者自由建立分類子資料夾，因此不能假設報表都在根目錄。
    同一股票若因手動整理留下多份 HTML，索引只選最後修改時間最新的一份，
    同時把其他候選路徑附在 duplicates，讓前端能提示而不會靜默載入任意檔案。

    回傳值刻意維持陣列格式，與原 pattern_viewer/server.py 的
    /api/reports-index 相容；新增欄位不影響既有 PatternViewer 使用者。
    """
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

            code, name, market = match.groups()
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

    return sorted(index, key=lambda item: (item["code"], item["path"]))


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


def attach_report_flows(cards_by_code):
    """將最新報表內的法人與融資券日資料附加到卡片 API，供首頁標籤及迷你圖使用。"""
    reports_by_code = {item["code"]: item for item in build_reports_index()}
    for code, card in cards_by_code.items():
        report = reports_by_code.get(code)
        if not report:
            card["institutionalFlow"] = []
            card["marginFlow"] = []
            continue
        try:
            report_text = (REPORTS_DIR / report["path"]).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            card["institutionalFlow"] = []
            card["marginFlow"] = []
            continue
        institutional = _report_table_rows(
            report_text, "三大法人", ("foreign", "trust", "dealer", "total")
        )[:20]
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
    with GENERATE_LOCK:
        generate_job["done"] = True
        generate_job["running"] = False
        generate_job["returncode"] = proc.returncode


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
        if parsed.path == "/api/ai-rankings":
            self._json(200, {"ok": True, **read_ai_rankings()})
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
        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/shutdown":
            self._json(200, {"ok": True})
            # 給回應一點時間真的送到瀏覽器，再結束 process
            threading.Thread(target=lambda: (time.sleep(0.3), os._exit(0))).start()
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
                args = ["999" if p.upper() in ("ALL", "全部") else p for p in parts]
                generate_job = _new_generate_job()
                generate_job["running"] = True
            threading.Thread(target=_run_generate, args=(args,), daemon=True).start()
            self._json(200, {"ok": True})
            return

        if parsed.path == "/api/ai-rankings/upsert":
            try:
                body = self._read_json_body()
                saved = upsert_ai_ranking(body)
            except (ValueError, json.JSONDecodeError) as e:
                self._json(400, {"ok": False, "error": str(e)})
                return
            except OSError as e:
                self._json(500, {"ok": False, "error": f"排行榜寫入失敗: {e}"})
                return
            self._json(200, {"ok": True, "ranking": saved})
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
                self._json(200, {"ok": True})
                return

            if parsed.path == "/api/report/delete":
                code = (body.get("code") or "").strip()
                if not code or any(ch in code for ch in FORBIDDEN_NAME_CHARS):
                    self._json(400, {"ok": False, "error": "無效的股票代號"})
                    return
                # 用代號遞迴找整個 reports/ (含所有子資料夾)，不用先知道報表放在哪個分類，
                # 排行榜上的股票不一定跟目前選取的資料夾在同一層。只刪報表檔本身
                # (html+png)，不會動到 data.js 裡的 AI 分析卡片資料。
                matches = (
                    glob.glob(os.path.join(REPORTS_DIR, "**", f"{code}_*.html"), recursive=True)
                    + glob.glob(os.path.join(REPORTS_DIR, "**", f"{code}_*_chart.png"), recursive=True)
                )
                deleted = 0
                for path_str in matches:
                    try:
                        os.remove(path_str)
                        deleted += 1
                    except OSError:
                        pass
                self._json(200, {"ok": True, "deletedFiles": deleted})
                return

            self._json(404, {"ok": False, "error": "未知的 API"})
        except ValueError as e:
            self._json(400, {"ok": False, "error": str(e)})
        except OSError as e:
            self._json(500, {"ok": False, "error": f"檔案系統錯誤: {e}"})

    def log_message(self, fmt, *args):
        print("[reports-manager]", fmt % args)


if __name__ == "__main__":
    os.chdir(ROOT_DIR)
    with ThreadingTCPServer(("", PORT), Handler) as httpd:
        print(f"Serving {ROOT_DIR} at http://localhost:{PORT}/reports_manager.html")
        httpd.serve_forever()
