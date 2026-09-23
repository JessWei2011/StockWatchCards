# -*- coding: utf-8 -*-
"""
券商主力分點籌碼服務 (Broker Branch Chip Service)
抓取盤後買賣超前 15 大券商分點明細、主力集中度與關鍵買賣券商，並提供本機磁碟快取。
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent
CACHE_FILE = ROOT_DIR / "cache" / "broker_chips_cache.json"

URL_TEMPLATES = [
    "https://concords.moneydj.com/z/zc/zco/zco.djhtm?a={code}",
    "https://moneydj.emega.com.tw/jsdata/z/zc/zco/zco.djhtm?a={code}",
]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


DAY_TRADING_BROKERS = [
    "凱基-台北", "凱基台北",
    "富邦-建國", "富邦建國",
    "元大-土城永寧", "土城永寧",
    "富邦-忠孝", "富邦忠孝",
    "國泰-敦南", "國泰敦南",
    "群益金鼎-大安", "群益大安",
    "兆豐-大同", "兆豐大同",
    "元大-北府",
    "華南永昌-世貿",
    "凱基-松山",
]


def check_day_trader_involvement(top_buyers: List[str]) -> Tuple[bool, List[str]]:
    """檢測前兩大買超分點是否命中知名隔日衝主力"""
    hit = []
    for b in top_buyers:
        b_name = b.split("+")[0].strip()
        for dt in DAY_TRADING_BROKERS:
            if dt in b_name:
                hit.append(b_name)
                break
    return len(hit) > 0, hit


def _clean_broker_name(name: str) -> str:
    """清理券商名稱前贅詞與後綴"""
    cleaned = re.sub(r"^(美商|港商|新加坡商|法商|香港上海|瑞士信貸|台灣|大和)", "", name)
    cleaned = re.sub(r"證券$", "", cleaned)
    return cleaned.strip() or name


def fetch_broker_branch_chips(code: str, target_date: str = "") -> Optional[Dict[str, Any]]:
    """抓取單一個股券商分點主力買賣超（買超前15名 vs 賣超前15名合計）"""
    target_formatted = ""
    is_mmdd = False
    if target_date:
        clean_d = str(target_date).replace("-", "").replace("/", "").strip()
        if len(clean_d) == 8 and clean_d.isdigit():
            target_formatted = f"{clean_d[:4]}/{clean_d[4:6]}/{clean_d[6:8]}"
        elif len(clean_d) == 4 and clean_d.isdigit():
            target_formatted = f"{clean_d[:2]}/{clean_d[2:4]}"
            is_mmdd = True
        else:
            target_formatted = target_date

    for tmpl in URL_TEMPLATES:
        url = tmpl.format(code=code)
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            if resp.status_code != 200:
                continue
            resp.encoding = "big5"
            soup = BeautifulSoup(resp.text, "html.parser")

            date_match = re.search(r"最後更新日：(\d{4}/\d{2}/\d{2})", soup.get_text())
            report_date = date_match.group(1) if date_match else ""
            if target_formatted and report_date:
                if is_mmdd:
                    if not report_date.endswith(target_formatted):
                        continue
                elif report_date != target_formatted:
                    continue

            top_buyers, top_sellers = [], []
            total_buy, total_sell = 0, 0
            buy_pct_sum, sell_pct_sum = 0.0, 0.0

            for t in soup.find_all("table"):
                text = t.get_text()
                if "合計買超張數" in text and "合計賣超張數" in text:
                    for r in t.find_all("tr"):
                        cols = [td.get_text(strip=True).replace(",", "") for td in r.find_all(["td", "th"])]
                        if len(cols) >= 10:
                            b_name, b_net = cols[0], cols[3]
                            s_name, s_net = cols[5], cols[8]
                            if (
                                len(top_buyers) < 2
                                and b_name
                                and b_name not in ("買超券商", "合計買超張數")
                                and b_net.lstrip("-").isdigit()
                                and int(b_net) > 0
                            ):
                                clean_b = _clean_broker_name(b_name)
                                top_buyers.append(f"{clean_b}+{int(b_net):,}")
                            if (
                                len(top_sellers) < 2
                                and s_name
                                and s_name not in ("賣超券商", "合計賣超張數")
                                and s_net.lstrip("-").isdigit()
                                and int(s_net) > 0
                            ):
                                clean_s = _clean_broker_name(s_name)
                                top_sellers.append(f"{clean_s}-{int(s_net):,}")

                            if cols[4].endswith("%"):
                                try:
                                    buy_pct_sum += float(cols[4].rstrip("%"))
                                except ValueError:
                                    pass
                            if cols[9].endswith("%"):
                                try:
                                    sell_pct_sum += float(cols[9].rstrip("%"))
                                except ValueError:
                                    pass

                        for i, c in enumerate(cols):
                            if "合計買超張數" in c and i + 1 < len(cols) and cols[i + 1].isdigit():
                                total_buy = int(cols[i + 1])
                            if "合計賣超張數" in c and i + 1 < len(cols) and cols[i + 1].isdigit():
                                total_sell = int(cols[i + 1])
                    break

            if total_buy > 0 or total_sell > 0:
                concentration = round(buy_pct_sum - sell_pct_sum, 1) if (buy_pct_sum or sell_pct_sum) else None
                is_dt, dt_list = check_day_trader_involvement(top_buyers)
                return {
                    "code": code,
                    "broker_buy_lots": total_buy,
                    "broker_sell_lots": total_sell,
                    "broker_net_lots": total_buy - total_sell,
                    "top_buyers": top_buyers,
                    "top_sellers": top_sellers,
                    "broker_concentration": concentration,
                    "broker_report_date": report_date,
                    "has_day_trader": is_dt,
                    "day_traders": dt_list,
                }
        except Exception:
            continue

    return None


def load_broker_chips_cache() -> Dict[str, Dict[str, Any]]:
    """讀取本機券商分點籌碼快取檔案"""
    if CACHE_FILE.exists():
        try:
            data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            for code, item in data.items():
                if "has_day_trader" not in item:
                    is_dt, dt_list = check_day_trader_involvement(item.get("top_buyers", []))
                    item["has_day_trader"] = is_dt
                    item["day_traders"] = dt_list
            return data
        except Exception:
            return {}
    return {}


def save_broker_chips_cache(cache_data: Dict[str, Dict[str, Any]]) -> None:
    """寫入本機券商分點籌碼快取檔案"""
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(cache_data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning(f"儲存券商分點快取失敗: {exc}")


def get_broker_chips_batch(
    codes: List[str],
    target_date: str = "",
    max_workers: int = 8,
    use_cache: bool = True,
) -> Dict[str, Dict[str, Any]]:
    """
    批次獲取多檔個股之券商分點主力資料（優先使用本機快取，缺少時多執行緒並行抓取）。
    """
    results: Dict[str, Dict[str, Any]] = {}
    if not codes:
        return results

    cache = load_broker_chips_cache() if use_cache else {}
    target_formatted = ""
    is_mmdd = False
    if target_date:
        clean_d = str(target_date).replace("-", "").replace("/", "").strip()
        if len(clean_d) == 8 and clean_d.isdigit():
            target_formatted = f"{clean_d[:4]}/{clean_d[4:6]}/{clean_d[6:8]}"
        elif len(clean_d) == 4 and clean_d.isdigit():
            target_formatted = f"{clean_d[:2]}/{clean_d[2:4]}"
            is_mmdd = True
        else:
            target_formatted = target_date

    codes_to_fetch = []
    for code in codes:
        clean_code = str(code).strip()
        if clean_code in cache:
            cached_item = cache[clean_code]
            rep_date = cached_item.get("broker_report_date", "")
            date_ok = True
            if target_formatted and rep_date:
                if is_mmdd:
                    date_ok = rep_date.endswith(target_formatted)
                else:
                    date_ok = (rep_date == target_formatted)
            if date_ok and rep_date:
                results[clean_code] = cached_item
                continue
        codes_to_fetch.append(clean_code)

    if codes_to_fetch:
        workers = min(max_workers, len(codes_to_fetch), 12)

        def _worker(c: str) -> Tuple[str, Optional[Dict[str, Any]]]:
            return c, fetch_broker_branch_chips(c, target_date)

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_code = {executor.submit(_worker, c): c for c in codes_to_fetch}
            for future in concurrent.futures.as_completed(future_to_code):
                try:
                    c, data = future.result()
                    if data:
                        results[c] = data
                        cache[c] = data
                except Exception:
                    pass

        save_broker_chips_cache(cache)

    return results


def get_broker_chip_tag(broker_data: Optional[Dict[str, Any]]) -> Optional[str]:
    """根據券商分點數據生成實戰籌碼標籤"""
    if not broker_data:
        return None

    conc = broker_data.get("broker_concentration")
    if conc is None:
        return None

    top_b = "、".join(broker_data.get("top_buyers", []))
    top_s = "、".join(broker_data.get("top_sellers", []))
    has_dt = broker_data.get("has_day_trader", False)
    dt_list = broker_data.get("day_traders", [])
    dt_suffix = f" ⚡隔日衝({','.join(dt_list)})" if has_dt and dt_list else ""

    if conc >= 15.0:
        detail = f"｜{top_b}大買" if top_b else ""
        return f"🔥 主力極致鎖碼 (集中度 {conc:+.1f}%{detail}){dt_suffix}"
    elif conc >= 5.0:
        detail = f"｜{top_b}" if top_b else ""
        return f"🟢 主力積極吸籌 (集中度 {conc:+.1f}%{detail}){dt_suffix}"
    elif conc <= -15.0:
        detail = f"｜{top_s}狂倒" if top_s else ""
        return f"🚨 主力倒貨散戶接刀 (集中度 {conc:+.1f}%{detail})"
    elif conc <= -5.0:
        detail = f"｜{top_s}" if top_s else ""
        return f"⚠️ 籌碼偏向發散 (集中度 {conc:+.1f}%{detail})"
    else:
        return f"⚪ 主力多空拉鋸 (集中度 {conc:+.1f}%){dt_suffix}"
