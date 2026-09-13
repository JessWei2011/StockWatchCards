#!/usr/bin/env python3
"""每日盤後產生法人籌碼策略榜，不依賴既有技術掃描流程。"""
from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT_DIR = Path(__file__).resolve().parent
OUTPUT_FILE = ROOT_DIR / "institutional_chip_strategy_ranking.md"
TWSE_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"
TPEX_URL = "https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php"
CODE_RE = re.compile(r"^\d{4}$")
USER_AGENT = "Stock2 Institutional Chip Ranking/1.0"


def fetch_json(url: str, params: dict[str, str]) -> dict:
    request = Request(
        f"{url}?{urlencode(params)}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    context = ssl.create_default_context()
    with urlopen(request, timeout=20, context=context) as response:
        return json.loads(response.read().decode("utf-8-sig"))


def as_lots(value: object) -> float:
    """交易所原始單位為股，統一轉成張。"""
    text = str(value or "").replace(",", "").replace("+", "").strip()
    try:
        return float(text) / 1000
    except ValueError:
        return 0.0


def roc_date(day: date) -> str:
    return f"{day.year - 1911}/{day.month:02d}/{day.day:02d}"


def parse_twse(rows: list[list[object]]) -> dict[str, dict]:
    result = {}
    for row in rows:
        if len(row) < 19:
            continue
        code = str(row[0]).strip()
        if not CODE_RE.fullmatch(code):
            continue
        result[code] = {
            "code": code,
            "name": str(row[1]).strip(),
            "market": "TWSE",
            "foreign": as_lots(row[4]),
            "trust": as_lots(row[10]),
            "dealer": as_lots(row[11]),
            "total": as_lots(row[18]),
        }
    return result


def parse_tpex(payload: dict) -> dict[str, dict]:
    tables = payload.get("tables") or []
    rows = tables[0].get("data", []) if tables else []
    result = {}
    for row in rows:
        if len(row) < 24:
            continue
        code = str(row[0]).strip()
        if not CODE_RE.fullmatch(code):
            continue
        result[code] = {
            "code": code,
            "name": str(row[1]).strip(),
            "market": "TPEx",
            "foreign": as_lots(row[10]),
            "trust": as_lots(row[13]),
            "dealer": as_lots(row[22]),
            "total": as_lots(row[23]),
        }
    return result


def fetch_latest_market_data(today: date | None = None) -> tuple[date, list[dict]]:
    """從最近十個日曆日中找出上市、上櫃皆已有法人資料的交易日。"""
    today = today or date.today()
    failures = []
    for offset in range(10):
        target = today - timedelta(days=offset)
        try:
            twse = fetch_json(TWSE_URL, {
                "response": "json", "date": target.strftime("%Y%m%d"), "selectType": "ALLBUT0999",
            })
            tpex = fetch_json(TPEX_URL, {
                "l": "zh-tw", "o": "json", "se": "AL", "t": "D", "d": roc_date(target),
            })
            twse_rows = twse.get("data") or []
            tpex_rows = (tpex.get("tables") or [{}])[0].get("data") or []
            if twse.get("stat") != "OK" or not twse_rows or not tpex_rows:
                failures.append(target.isoformat())
                continue
            records = [*parse_twse(twse_rows).values(), *parse_tpex(tpex).values()]
            if records:
                return target, records
        except Exception as error:
            failures.append(f"{target.isoformat()} ({error})")
    raise RuntimeError("近十日無法取得完整 TWSE T86 與 TPEx 三大法人資料：" + "；".join(failures))


def fmt_lots(value: float) -> str:
    return f"{value:+,.0f}" if value else "0"


def ranking_rows(records: list[dict]) -> tuple[list[dict], list[dict]]:
    # 僅普通股四碼；上市 T86 已排除 0999 類，TPEx 端再以四碼代號排除 ETF、權證等。
    ordinary = [item for item in records if CODE_RE.fullmatch(item["code"])]
    foreign_trust = [
        item for item in ordinary
        if item["foreign"] > 0 and item["trust"] > 0 and item["total"] >= 300
    ]
    trust_buy = [item for item in ordinary if item["trust"] > 0]
    return (
        sorted(foreign_trust, key=lambda item: (-item["total"], item["code"]))[:10],
        sorted(trust_buy, key=lambda item: (-item["trust"], item["code"]))[:10],
    )


def markdown_table(items: list[dict]) -> str:
    if not items:
        return "| — | 當日無符合標的 | — | — | — | — | — |\n"
    lines = []
    for rank, item in enumerate(items, 1):
        lines.append(
            f"| {rank} | `{item['code']}` | {item['name']} | {fmt_lots(item['foreign'])} | "
            f"{fmt_lots(item['trust'])} | {fmt_lots(item['dealer'])} | {fmt_lots(item['total'])} |"
        )
    return "\n".join(lines) + "\n"


def build_markdown(as_of: date, records: list[dict]) -> str:
    foreign_trust, trust_buy = ranking_rows(records)
    header = "| 排名 | 股票代號 | 股票名稱 | 外資(張) | 投信(張) | 自營商(張) | 合計(張) |\n|---:|:---:|:---|---:|---:|---:|---:|\n"
    return (
        "# 🏦 法人籌碼策略榜\n\n"
        f"> 資料日期：{as_of.isoformat()}｜來源：TWSE T86、TPEx 三大法人買賣超｜單位：張\n\n"
        "## ① 外資、投信同步買超｜合計至少 300 張 TOP 10\n\n"
        + header + markdown_table(foreign_trust)
        + "\n## ② 投信買超 TOP 10\n\n"
        + header + markdown_table(trust_buy)
        + "\n> 僅納入 4 碼普通股；外資、投信、自營商及合計均已統一換算為張。\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="產生法人籌碼策略榜")
    parser.add_argument("--output", type=Path, default=OUTPUT_FILE)
    args = parser.parse_args()
    as_of, records = fetch_latest_market_data()
    args.output.write_text(build_markdown(as_of, records), encoding="utf-8")
    first, second = ranking_rows(records)
    print(f"OK: {as_of.isoformat()} 取得 {len(records)} 檔普通股法人資料")
    print(f"榜單① {len(first)} 檔；榜單② {len(second)} 檔")
    print(f"輸出：{args.output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
