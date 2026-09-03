"""Export every available stock analysis as a static mobile snapshot.

This exporter is deliberately independent from the local reports-manager API.
It only reads the current workspace files and writes deployable assets under
mobile_web/public/data/.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT_DIR = Path(__file__).resolve().parent
REPORTS_DIR = ROOT_DIR / "reports"
WATCHLIST_FILE = ROOT_DIR / "watchlist.json"
PUBLIC_DIR = ROOT_DIR / "mobile_web" / "public"
OUTPUT_DIR = PUBLIC_DIR / "data"
DEFAULT_CODE = "3324"
RANKING_SOURCES = {
    "gemini": ROOT_DIR / "stock_winrate_ranking_gemini.md",
    "chatgpt": ROOT_DIR / "stock_winrate_ranking.md",
}

REPORT_RE = re.compile(r"^(\d+)_(.+?)\((TW|TWO)\)(.*?)\.html$", re.I)


def load_watchlist() -> set[str]:
    if not WATCHLIST_FILE.exists():
        return set()
    try:
        data = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
        starred = data.get("starred") if isinstance(data, dict) else data
        return set(str(c).strip() for c in (starred or []) if str(c).strip())
    except Exception:
        return set()


def discover_latest_reports() -> dict[str, dict]:
    candidates: dict[str, list[tuple[bool, float, Path, str, str]]] = {}
    for path in REPORTS_DIR.rglob("*.html"):
        match = REPORT_RE.match(path.name)
        if not match:
            continue
        code, raw_name, market, suffix = match.groups()
        canonical = not suffix.strip()
        candidates.setdefault(code, []).append((canonical, path.stat().st_mtime, path, raw_name, market.upper()))

    if not candidates:
        raise RuntimeError("reports/ 中找不到任何個股 HTML 報告")

    discovered = {}
    for code, choices in candidates.items():
        choices.sort(key=lambda item: (item[0], item[1]), reverse=True)
        _canonical, _mtime, path, raw_name, market = choices[0]
        discovered[code] = {
            "path": path,
            "name": raw_name,
            "market": market,
            "group": path.parent.name,
        }
    return discovered


def discover_latest_analyses() -> dict[str, Path]:
    discovered: dict[str, Path] = {}
    for path in REPORTS_DIR.rglob("*_4階段技術分析報告.md"):
        match = re.match(r"^(\d+)_", path.name)
        if not match:
            continue
        code = match.group(1)
        if code not in discovered or path.stat().st_mtime > discovered[code].stat().st_mtime:
            discovered[code] = path
    return discovered


def validate_report_html(text: str, code: str) -> None:
    required_labels = ("日期", "開", "高", "低", "收", "量", "MA5", "RSI", "MACD")
    if not all(label in text for label in required_labels):
        raise RuntimeError(f"{code} HTML 報告缺少必要 K 線欄位，停止匯出")

    kline_rows = re.findall(
        r"<tr[^>]*>\s*<td>\d{2}/\d{2}</td>.*?</tr>", text, flags=re.I | re.S
    )
    if len(kline_rows) < 20:
        raise RuntimeError(f"{code} HTML 報告只有 {len(kline_rows)} 筆可辨識 K 線，停止匯出")


def _md_value(text: str, pattern: str, default: str = "") -> str:
    match = re.search(pattern, text, re.I | re.M)
    if not match:
        return default
    return re.sub(r"[*`]+", "", match.group(1)).strip()


def _first_number(value: str, default: str = "") -> str:
    match = re.search(r"[0-9][0-9,]*(?:\.[0-9]+)?", value or "")
    return match.group(0).replace(",", "") if match else default


def parse_latest_analysis(text: str, report_path: Path) -> dict:
    short_date = _md_value(text, r"分析日期\*{0,2}[：:]\s*([^\r\n]+)")
    if re.fullmatch(r"\d{2}/\d{2}", short_date):
        year = datetime.fromtimestamp(report_path.stat().st_mtime).year
        month, day = short_date.split("/")
        analysis_date = f"{year}-{month}-{day}"
    else:
        analysis_date = short_date

    current = _first_number(_md_value(text, r"當前價格\*{0,2}[：:]\s*([^\r\n]+)"))
    decision = _md_value(text, r"建議評級】?[：:]\s*([^\r\n]+)")
    win_rate = _md_value(text, r"預期勝率】?[：:]\s*([^\s\r\n]+)")
    pattern = _md_value(text, r"【主要形態】[：:]\s*([^\r\n]+)")
    confidence = _md_value(text, r"【確認程度】[：:]\s*([^\r\n]+)")
    entry_text = _md_value(text, r"【理想進場區】[：:]\s*([^\r\n]+)")
    stop_text = _md_value(text, r"【停損位】[：:]\s*([^\r\n]+)")
    resist_text = _md_value(text, r"阻力位\s*1\*{0,2}[：:]\s*([^\r\n]+)")
    target_text = _md_value(text, r"【形態目標價】[：:]\s*([^\r\n]+)")
    direction = _md_value(text, r"【交易方向】[：:]\s*([^\r\n]+)")
    strategy = _md_value(text, r"推薦策略[：:]\s*([^\r\n]+)")
    invalid_point = _md_value(text, r"【技術無效點】[：:]\s*([^\r\n]+)")

    tag_patterns = [
        ("K線", r"K線標籤\*{0,2}[：:]\s*([^\r\n]+)"),
        ("成交量", r"VOL\s*標籤\*{0,2}[：:]\s*([^\r\n]+)"),
        ("RSI", r"RSI\s*標籤\*{0,2}[：:]\s*([^\r\n]+)"),
        ("MACD", r"MACD\s*標籤\*{0,2}[：:]\s*([^\r\n]+)"),
        ("KD", r"KD\s*標籤\*{0,2}[：:]\s*([^\r\n]+)"),
        ("籌碼", r"籌碼標籤\*{0,2}[：:]\s*([^\r\n]+)"),
    ]
    bullish = []
    bearish = []

    def _is_bearish_tag(t: str) -> bool:
        bearish_keywords = ['⚡', '🚨', '⚠️', '❄️', '📉', '🔒', '死亡交叉', '死叉', '頂背離', '倒貨', '出貨', '退潮', '警戒', '空方', '了結', '弱勢', '賣超', '處置']
        return any(k in t for k in bearish_keywords)

    for label, regex in tag_patterns:
        value = _md_value(text, regex)
        if value:
            sub_tags = [t.strip() for t in re.split(r'[、,]', value) if t.strip()]
            b_list = []
            bear_list = []
            for t in sub_tags:
                if _is_bearish_tag(t):
                    bear_list.append(t)
                else:
                    b_list.append(t)
            if b_list:
                bullish.append(f'{label}：{"、".join(b_list)}')
            if bear_list:
                bearish.append(f'{label}：{"、".join(bear_list)}')
    if invalid_point:
        bearish.append(f"技術無效點：{invalid_point}")
    if stop_text:
        bearish.append(f"風控停損：{stop_text}")

    action_parts = [part for part in (
        f"交易方向：{direction}" if direction else "",
        f"建議評級：{decision}" if decision else "",
        f"策略：{strategy}" if strategy else "",
        f"目標價：{_first_number(target_text)} 元" if _first_number(target_text) else "",
        f"停損：{_first_number(stop_text)} 元" if _first_number(stop_text) else "",
    ) if part]

    return {
        "date": analysis_date,
        "current": current,
        "decision": decision,
        "winRate": win_rate,
        "pattern": pattern,
        "confidence": confidence,
        "entry": _first_number(entry_text, current),
        "stop": _first_number(stop_text),
        "resist": _first_number(resist_text),
        "target": _first_number(target_text),
        "bullish": bullish,
        "bearish": bearish,
        "action": "；".join(action_parts) + ("。" if action_parts else ""),
    }


def _clean_ranking_value(value: str) -> str:
    return re.sub(r"[*`]", "", value or "").strip()


def parse_dual_track_ranking(text: str, source: str) -> tuple[str, dict[str, list[dict]]]:
    """Parse the scanner's two Top 10 markdown tables for the mobile snapshot."""
    tracks = {"momentum": [], "defensive": []}
    current_track = ""
    scan_date = ""

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        date_match = re.search(r"資料截止日[：:]\s*([0-9/-]+)", line)
        if date_match:
            scan_date = date_match.group(1)
        if "暴漲動能型" in line or "攻擊型" in line or "攻擊榜" in line:
            current_track = "momentum"
            continue
        if "穩健防守型" in line or "穩健型" in line or "穩健榜" in line:
            current_track = "defensive"
            continue
        if not (current_track and line.startswith("|") and line.endswith("|")):
            continue

        cells = [_clean_ranking_value(cell) for cell in line.split("|")[1:-1]]
        if len(cells) < 5 or "排名" in cells[0] or all(re.fullmatch(r":?-+:?", cell) for cell in cells):
            continue
        code = cells[1]
        if not re.fullmatch(r"\d{4,6}", code):
            continue
        tracks[current_track].append({
            "rank": cells[0],
            "code": code,
            "name": cells[2],
            "price": cells[4],
        })

    missing = [track for track, items in tracks.items() if not items]
    if missing:
        raise RuntimeError(f"{source} 排行榜缺少區段：{', '.join(missing)}")
    return scan_date, tracks


def export_four_rankings() -> None:
    parsed: dict[str, dict[str, list[dict]]] = {}
    scan_dates: list[str] = []
    for source, path in RANKING_SOURCES.items():
        if not path.is_file():
            raise RuntimeError(f"找不到 {source} 排行榜：{path.name}")
        scan_date, tracks = parse_dual_track_ranking(path.read_text(encoding="utf-8"), source)
        parsed[source] = tracks
        if scan_date:
            scan_dates.append(scan_date)

    boards = [
        ("gemini-momentum", "Gemini 暴漲動能 TOP 10", "gemini-momentum", parsed["gemini"]["momentum"]),
        ("gemini-defensive", "Gemini 穩健防守 TOP 10", "gemini-defensive", parsed["gemini"]["defensive"]),
        ("chatgpt-momentum", "ChatGPT 攻擊型 TOP 10", "chatgpt-momentum", parsed["chatgpt"]["momentum"]),
        ("chatgpt-defensive", "ChatGPT 穩健型 TOP 10", "chatgpt-defensive", parsed["chatgpt"]["defensive"]),
    ]
    payload = {
        "version": 1,
        "scanDate": scan_dates[0] if scan_dates else "",
        "boards": [
            {"id": board_id, "title": title, "tone": tone, "items": items[:10]}
            for board_id, title, tone, items in boards
        ],
    }
    (OUTPUT_DIR / "rankings.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    reports = discover_latest_reports()
    analyses = discover_latest_analyses()
    missing_analyses = sorted(set(reports) - set(analyses))
    if missing_analyses:
        raise RuntimeError(f"以下個股缺少四階段分析：{', '.join(missing_analyses)}")

    stocks_dir = OUTPUT_DIR / "stocks"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stocks_dir.mkdir(parents=True, exist_ok=True)
    active_codes = set(reports)
    for existing_dir in stocks_dir.iterdir():
        if existing_dir.is_dir() and existing_dir.name not in active_codes:
            shutil.rmtree(existing_dir)

    now = datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds")
    watchlist = load_watchlist()
    stock_index = []
    for code in sorted(reports, key=lambda value: int(value)):
        report_info = reports[code]
        report_path = report_info["path"]
        analysis_path = analyses[code]
        report_text = report_path.read_text(encoding="utf-8")
        analysis_text = analysis_path.read_text(encoding="utf-8")
        validate_report_html(report_text, code)
        if len(analysis_text.strip()) < 100:
            raise RuntimeError(f"{code} 四階段分析內容過短，停止匯出")

        latest_analysis = parse_latest_analysis(analysis_text, report_path)
        required_summary = ("date", "current", "decision", "winRate", "pattern", "action")
        missing_summary = [
            field for field in required_summary
            if not str(latest_analysis.get(field) or "").strip()
        ]
        if missing_summary:
            raise RuntimeError(
                f"{code} Markdown 分析缺少手機摘要欄位：{', '.join(missing_summary)}"
            )
        card = {
            "code": code,
            "name": report_info["name"],
            "group": report_info["group"],
            "market": report_info["market"],
            "date": latest_analysis.get("date") or "",
            "isStarred": code in watchlist,
        }
        stock_payload = {
            "version": 2,
            "stock": card,
            "latestAnalysis": latest_analysis,
        }
        stock_output_dir = stocks_dir / code
        stock_output_dir.mkdir(parents=True, exist_ok=True)
        (stock_output_dir / "stock.json").write_text(
            json.dumps(stock_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        shutil.copyfile(report_path, stock_output_dir / "report.html")
        shutil.copyfile(analysis_path, stock_output_dir / "analysis.md")

        stock_index.append({
            "code": code,
            "name": report_info["name"],
            "group": report_info["group"],
            "market": report_info["market"],
            "analysisDate": latest_analysis.get("date") or "",
            "current": latest_analysis.get("current") or "",
            "decision": latest_analysis.get("decision") or "",
            "winRate": latest_analysis.get("winRate") or "",
            "pattern": latest_analysis.get("pattern") or "",
            "isStarred": code in watchlist,
        })

    (OUTPUT_DIR / "index.json").write_text(
        json.dumps({
            "version": 2,
            "publishedAt": now,
            "defaultCode": DEFAULT_CODE if DEFAULT_CODE in reports else stock_index[0]["code"],
            "count": len(stock_index),
            "stocks": stock_index,
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    export_four_rankings()

    default_entry = next(item for item in stock_index if item["code"] == (DEFAULT_CODE if DEFAULT_CODE in reports else stock_index[0]["code"]))
    manifest = {
        "version": 2,
        "code": default_entry["code"],
        "name": default_entry["name"],
        "market": default_entry["market"],
        "analysisDate": default_entry["analysisDate"],
        "publishedAt": now,
        "stockCount": len(stock_index),
    }
    (OUTPUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    required = ["index.json", "manifest.json", "rankings.json"]
    for filename in required:
        target = OUTPUT_DIR / filename
        if not target.is_file() or target.stat().st_size == 0:
            raise RuntimeError(f"輸出驗證失敗：{target}")

    for item in stock_index:
        stock_output_dir = stocks_dir / item["code"]
        for filename in ("stock.json", "report.html", "analysis.md"):
            target = stock_output_dir / filename
            if not target.is_file() or target.stat().st_size == 0:
                raise RuntimeError(f"輸出驗證失敗：{target}")

    print(f"OK: 已匯出 {len(stock_index)} 檔個股")
    print(f"預設個股：{manifest['code']} {manifest['name']}")
    print(f"輸出目錄：{OUTPUT_DIR}")


if __name__ == "__main__":
    main()
