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
TPEX_OPENAPI_URL = "https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading"
CODE_RE = re.compile(r"^\d{4}$")
USER_AGENT = "StockCenter Institutional Chip Ranking/1.0"


def fetch_json(url: str, params: dict[str, str] | None = None) -> dict | list:
    full_url = f"{url}?{urlencode(params)}" if params else url
    request = Request(
        full_url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    context = ssl._create_unverified_context()
    with urlopen(request, timeout=15, context=context) as response:
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


def parse_tpex_openapi(rows: list[dict]) -> tuple[date | None, dict[str, dict]]:
    result = {}
    target_dt = None
    for row in rows:
        code = str(row.get("SecuritiesCompanyCode", "")).strip()
        if not CODE_RE.fullmatch(code):
            continue
        if target_dt is None and "Date" in row:
            raw_d = str(row["Date"]).strip()
            if len(raw_d) >= 6:
                y = int(raw_d[:-4]) + 1911
                m = int(raw_d[-4:-2])
                d = int(raw_d[-2:])
                target_dt = date(y, m, d)

        foreign = as_lots(
            row.get("ForeignInvestorsInclude MainlandAreaInvestors-Difference") or
            row.get("ForeignInvestorsIncludeMainlandAreaInvestors-Difference") or
            row.get("Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference")
        )
        trust = as_lots(row.get("SecuritiesInvestmentTrustCompanies-Difference"))
        dealer = as_lots(row.get("Dealers-Difference"))
        total = as_lots(row.get("TotalDifference"))
        result[code] = {
            "code": code,
            "name": str(row.get("CompanyName", "")).strip(),
            "market": "TPEx",
            "foreign": foreign,
            "trust": trust,
            "dealer": dealer,
            "total": total,
        }
    return target_dt, result


def fetch_latest_market_data(today: date | None = None) -> tuple[date, list[dict]]:
    """優先透過 TPEx OpenAPI 取得最新上櫃法人資料，並同步取得 TWSE 上市法人資料。"""
    today = today or date.today()
    failures = []

    # 1. 先抓 TPEx OpenAPI (最新交易日)
    tpex_date = None
    tpex_records = {}
    try:
        tpex_data = fetch_json(TPEX_OPENAPI_URL)
        if isinstance(tpex_data, list):
            tpex_date, tpex_records = parse_tpex_openapi(tpex_data)
    except Exception as error:
        failures.append(f"TPEx OpenAPI 失敗 ({error})")

    if tpex_date and tpex_records:
        try:
            twse = fetch_json(TWSE_URL, {
                "response": "json", "date": tpex_date.strftime("%Y%m%d"), "selectType": "ALLBUT0999",
            })
            twse_rows = twse.get("data") or []
            if twse.get("stat") == "OK" and twse_rows:
                records = [*parse_twse(twse_rows).values(), *tpex_records.values()]
                if records:
                    return tpex_date, records
        except Exception as error:
            failures.append(f"TWSE {tpex_date} 失敗 ({error})")

    # 2. 若 OpenAPI 未能成功，後備依序向 TWSE 查詢最近交易日
    for offset in range(10):
        target = today - timedelta(days=offset)
        try:
            twse = fetch_json(TWSE_URL, {
                "response": "json", "date": target.strftime("%Y%m%d"), "selectType": "ALLBUT0999",
            })
            twse_rows = twse.get("data") or []
            if twse.get("stat") != "OK" or not twse_rows:
                failures.append(target.isoformat())
                continue
            records = [*parse_twse(twse_rows).values(), *tpex_records.values()]
            if records:
                return target, records
        except Exception as error:
            failures.append(f"{target.isoformat()} ({error})")

    raise RuntimeError("近十日無法取得完整 TWSE T86 與 TPEx 三大法人資料：" + "；".join(failures))


def fmt_lots(value: float) -> str:
    return f"{value:+,.0f}" if value else "0"


try:
    import broker_chip_service
except Exception:
    broker_chip_service = None


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


def broker_chip_ranking_rows(records: list[dict]) -> tuple[list[dict], list[dict]]:
    """依主力券商分點買賣超集中度排序 (吸籌 vs 倒貨)。"""
    if not broker_chip_service:
        return [], []
    cache = broker_chip_service.load_broker_chips_cache()
    if not cache:
        return [], []
    name_map = {item["code"]: item["name"] for item in records}
    
    enriched = []
    for code, info in cache.items():
        conc = info.get("broker_concentration")
        if conc is None or not CODE_RE.fullmatch(code):
            continue
        buyers = ", ".join(info.get("top_buyers", [])[:2])
        sellers = ", ".join(info.get("top_sellers", [])[:2])
        point_summary = f"買: {buyers}" if buyers else ""
        if sellers:
            point_summary += f" / 賣: {sellers}" if point_summary else f"賣: {sellers}"

        enriched.append({
            "code": code,
            "name": name_map.get(code, code),
            "buy": info.get("broker_buy_lots", 0),
            "sell": info.get("broker_sell_lots", 0),
            "net": info.get("broker_net_lots", 0),
            "concentration": conc,
            "has_day_trader": info.get("has_day_trader", False),
            "points": point_summary
        })

    accum = [x for x in enriched if x["concentration"] > 0]
    dump = [x for x in enriched if x["concentration"] < 0]

    accum_sorted = sorted(accum, key=lambda x: (-x["concentration"], -x["net"]))[:10]
    dump_sorted = sorted(dump, key=lambda x: (x["concentration"], x["net"]))[:10]
    return accum_sorted, dump_sorted


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


def broker_markdown_table(items: list[dict]) -> str:
    if not items:
        return "| — | 當日無符合標的 | — | — | — | — | — |\n"
    lines = []
    for rank, item in enumerate(items, 1):
        dt_flag = " ⚡(隔日衝)" if item.get("has_day_trader") else ""
        lines.append(
            f"| {rank} | `{item['code']}` | {item['name']}{dt_flag} | {fmt_lots(item['buy'])} | "
            f"{fmt_lots(item['sell'])} | {fmt_lots(item['net'])} | {item['concentration']:+.1f}% |"
        )
    return "\n".join(lines) + "\n"


def build_markdown(as_of: date, records: list[dict]) -> str:
    foreign_trust, trust_buy = ranking_rows(records)
    accum_list, dump_list = broker_chip_ranking_rows(records)
    header = "| 排名 | 股票代號 | 股票名稱 | 外資(張) | 投信(張) | 自營商(張) | 合計(張) |\n|---:|:---:|:---|---:|---:|---:|---:|\n"
    broker_header = "| 排名 | 股票代號 | 股票名稱 | 主力買(張) | 主力賣(張) | 買賣超差(張) | 集中度(%) |\n|---:|:---:|:---|---:|---:|---:|---:|\n"
    
    sections = [
        "# 🏦 法人與主力籌碼策略榜\n\n",
        f"> 資料日期：{as_of.isoformat()}｜來源：TWSE T86、TPEx 三大法人買賣超、MoneyDJ 券商分點｜單位：張\n\n",
        "## ① 外資、投信同步買超｜合計至少 300 張 TOP 10\n\n",
        header,
        markdown_table(foreign_trust),
        "\n## ② 投信買超 TOP 10\n\n",
        header,
        markdown_table(trust_buy)
    ]

    if accum_list:
        sections.extend([
            "\n## ③ 主力分點強力吸籌 TOP 10\n\n",
            broker_header,
            broker_markdown_table(accum_list)
        ])
    if dump_list:
        sections.extend([
            "\n## ④ 主力分點倒貨警戒 TOP 10\n\n",
            broker_header,
            broker_markdown_table(dump_list)
        ])

    sections.append("\n> 僅納入 4 碼普通股；三大法人買賣超及主力分點買賣均已統一換算為張。\n")
    return "".join(sections)


def recover_from_existing(output_path: Path) -> tuple[date, list[dict]]:
    if not output_path.exists():
        raise RuntimeError("無法自網路取得資料，且本機尚無既存榜單檔。")
    text = output_path.read_text(encoding="utf-8")
    m = re.search(r"資料日期[：:]\s*(\d{4}-\d{2}-\d{2})", text)
    as_of = date.fromisoformat(m.group(1)) if m else date.today()
    records = []
    seen = set()
    for line in text.splitlines():
        if not line.startswith("|") or not line.endswith("|"):
            continue
        parts = [p.strip().replace("`", "") for p in line.split("|")[1:-1]]
        if len(parts) >= 7 and CODE_RE.fullmatch(parts[1]):
            code = parts[1]
            if code not in seen:
                seen.add(code)
                records.append({
                    "code": code,
                    "name": parts[2],
                    "market": "TWSE",
                    "foreign": as_lots(parts[3]),
                    "trust": as_lots(parts[4]),
                    "dealer": as_lots(parts[5]),
                    "total": as_lots(parts[6]),
                })
    return as_of, records


def main() -> int:
    parser = argparse.ArgumentParser(description="產生法人籌碼策略榜")
    parser.add_argument("--output", type=Path, default=OUTPUT_FILE)
    args = parser.parse_args()
    try:
        as_of, records = fetch_latest_market_data()
    except Exception as e:
        print(f"⚠️ 即時網路擷取三大法人資料失敗 ({e})，使用既存報告之法人數據...")
        as_of, records = recover_from_existing(args.output)

    args.output.write_text(build_markdown(as_of, records), encoding="utf-8")
    first, second = ranking_rows(records)
    accum, dump = broker_chip_ranking_rows(records)
    print(f"OK: {as_of.isoformat()} 取得 {len(records)} 檔普通股法人資料")
    print(f"榜單① {len(first)} 檔；榜單② {len(second)} 檔；主力吸籌③ {len(accum)} 檔；主力倒貨④ {len(dump)} 檔")
    print(f"輸出：{args.output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
