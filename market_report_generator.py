#!/usr/bin/env python3
"""產生市場指數 K 線報表。

報表格式刻意和個股報表共用 PatternViewer；市場指數只輸出 OHLC，
台股兩項另附市場成交量，美股指數則不以 ETF 代理成交量。
"""
from __future__ import annotations

import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


ROOT = Path(__file__).resolve().parent
REPORT_DIR = ROOT / "reports" / "市場指數"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
YAHOO_ENDPOINT = "https://query1.finance.yahoo.com/v8/finance/chart/{}"

MARKETS = (
    ("MKT01", "台股市場", "^TWII", True),
    ("MKT02", "台股櫃買", None, True),
    ("MKT03", "道瓊指數", "^DJI", False),
    ("MKT04", "納斯達克指數", "^IXIC", False),
    ("MKT05", "費城半導體指數", "^SOX", False),
)


def number(value, default=None):
    try:
        return float(str(value).replace(",", "").replace("--", ""))
    except (TypeError, ValueError):
        return default


def yahoo_daily(symbol: str) -> list[dict]:
    response = requests.get(
        YAHOO_ENDPOINT.format(quote(symbol, safe="")),
        params={"range": "2y", "interval": "1d", "events": "history"},
        headers=HEADERS,
        timeout=20,
    )
    response.raise_for_status()
    result = response.json().get("chart", {}).get("result") or []
    if not result:
        raise ValueError(f"Yahoo Finance 未回傳 {symbol} 的歷史資料")
    data = result[0]
    tz_name = data.get("meta", {}).get("exchangeTimezoneName") or "UTC"
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc
    quote_data = (data.get("indicators", {}).get("quote") or [{}])[0]
    rows = []
    for index, timestamp in enumerate(data.get("timestamp") or []):
        values = {key: (quote_data.get(key) or [None] * (index + 1))[index] for key in ("open", "high", "low", "close", "volume")}
        if any(values[key] is None for key in ("open", "high", "low", "close")):
            continue
        rows.append({
            "date": datetime.fromtimestamp(timestamp, timezone.utc).astimezone(tz).strftime("%Y-%m-%d"),
            "open": float(values["open"]), "high": float(values["high"]),
            "low": float(values["low"]), "close": float(values["close"]),
            "volume": number(values["volume"], 0),
        })
    return rows


def taiwan_market_volume(months: int = 9) -> dict[str, float]:
    """上市市場成交股數由 TWSE FMTQIK 補上；Yahoo 的指數本身沒有成交量。"""
    now = datetime.now()
    volumes: dict[str, float] = {}
    for offset in range(months):
        year = now.year
        month = now.month - offset
        if month <= 0:
            year -= 1
            month += 12
        date_param = f"{year}{month:02d}01"
        try:
            response = requests.get(
                "https://www.twse.com.tw/exchangeReport/FMTQIK",
                params={"response": "json", "date": date_param, "type": "ALL"},
                headers=HEADERS,
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
            fields = payload.get("fields") or []
            index_date = fields.index("日期")
            index_volume = fields.index("成交股數")
            for row in payload.get("data") or []:
                raw_date = str(row[index_date]).strip()
                pieces = raw_date.split("/")
                if len(pieces) != 3:
                    continue
                gregorian = int(pieces[0]) + 1911
                date = f"{gregorian:04d}-{int(pieces[1]):02d}-{int(pieces[2]):02d}"
                volumes[date] = number(row[index_volume], 0)
        except Exception as exc:
            print(f"⚠️ 台股市場 {year}-{month:02d} 成交量讀取失敗：{exc}")
    return volumes


def tpex_market_volumes() -> dict[str, float]:
    """櫃買中心 OpenAPI 提供本月每日市場成交量。"""
    volume_response = requests.get(
        "https://www.tpex.org.tw/openapi/v1/tpex_daily_trading_index",
        headers=HEADERS,
        timeout=20,
        verify=False,
    )
    volume_response.raise_for_status()
    volumes = {}
    for item in volume_response.json() or []:
        raw = str(item.get("Date") or "")
        if len(raw) == 7 and raw.isdigit():  # 民國年月日，例如 1150901
            raw = f"{int(raw[:3]) + 1911:04d}{raw[3:]}"
        if len(raw) == 8 and raw.isdigit():
            volumes[f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"] = number(item.get("TradeVolume"), 0)
    return volumes


def tpex_month_ohlc(year: int, month: int, volumes: dict[str, float]) -> list[dict]:
    """官方歷史指數 API，可查 2012/04 起的每月 OHLC。"""
    response = requests.post(
        "https://www.tpex.org.tw/www/zh-tw/indexInfo/inx",
        data={"date": f"{year}{month:02d}01", "response": "json"},
        headers=HEADERS,
        timeout=20,
        verify=False,
    )
    response.raise_for_status()
    tables = response.json().get("tables") or []
    data = tables[0].get("data") if tables else []
    rows = []
    for item in data or []:
        if len(item) < 5:
            continue
        date = str(item[0]).replace("/", "-")
        open_price, high_price, low_price, close_price = (number(value) for value in item[1:5])
        if not date or None in (open_price, high_price, low_price, close_price):
            continue
        rows.append({
            "date": date, "open": open_price, "high": high_price, "low": low_price,
            "close": close_price, "volume": volumes.get(date),
        })
    return rows


def tpex_history() -> list[dict]:
    """櫃買官方 OHLC 按月發布；本機快取會在每天更新後逐月累積。"""
    cache_path = ROOT / "cache" / "tpex_index_history.json"
    cached: dict[str, dict] = {}
    try:
        loaded = json.loads(cache_path.read_text(encoding="utf-8"))
        for raw_date, row in loaded.items():
            date = str(row.get("date") or raw_date).replace("/", "-")
            if len(date) == 8 and date.isdigit():
                date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
            if len(date) == 10 and date[4] == "-" and date[7] == "-":
                cached[date] = {**row, "date": date}
    except (OSError, json.JSONDecodeError):
        pass
    volumes = tpex_market_volumes()
    now = datetime.now()
    for row in tpex_month_ohlc(now.year, now.month, volumes):
        cached[row["date"]] = row
    cache_path.parent.mkdir(exist_ok=True)
    cache_path.write_text(json.dumps(cached, ensure_ascii=False, indent=2), encoding="utf-8")
    return [cached[key] for key in sorted(cached)][-500:]


def display_number(value: float, decimals: int = 2) -> str:
    return f"{value:,.{decimals}f}"


def write_report(code: str, name: str, rows: list[dict], has_volume: bool):
    if not rows:
        raise ValueError(f"{name} 沒有可寫入的 K 線資料")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    headers = "<th>日期</th><th>開</th><th>高</th><th>低</th><th>收</th>"
    if has_volume:
        headers += "<th>市場成交量</th>"
    body = []
    for row in rows[-500:]:
        cells = [
            html.escape(row["date"]), display_number(row["open"]), display_number(row["high"]),
            display_number(row["low"]), display_number(row["close"]),
        ]
        if has_volume:
            cells.append(f"{int(row.get('volume') or 0):,}")
        body.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")
    volume_note = "含市場成交量" if has_volume else "指數本身不提供成交量"
    document = f"""<!doctype html>
<html lang=\"zh-Hant\"><head><meta charset=\"utf-8\"><meta name=\"report-kind\" content=\"market\">
<title>{html.escape(name)} 市場報表</title></head>
<body data-report-kind=\"market\" data-has-volume=\"{'true' if has_volume else 'false'}\">
<h1>{html.escape(name)}</h1><p>市場指數 K 線報表｜{volume_note}｜更新：{datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
<table data-kline-table><thead><tr>{headers}</tr></thead><tbody>{''.join(body)}</tbody></table>
</body></html>"""
    (REPORT_DIR / f"{code}_{name}(IDX).html").write_text(document, encoding="utf-8")


def main():
    taiwan_volumes = taiwan_market_volume()
    completed = 0
    for code, name, symbol, has_volume in MARKETS:
        try:
            rows = tpex_history() if code == "MKT02" else yahoo_daily(symbol)
            if code == "MKT01":
                for row in rows:
                    row["volume"] = taiwan_volumes.get(row["date"], 0)
            write_report(code, name, rows, has_volume)
            print(f"✅ {name}：{len(rows)} 根 K 線")
            completed += 1
        except Exception as exc:
            print(f"❌ {name}：{exc}")
    if completed != len(MARKETS):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
