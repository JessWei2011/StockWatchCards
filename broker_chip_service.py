# -*- coding: utf-8 -*-
"""
券商主力分點籌碼服務 (Broker Branch Chip Service)
抓取盤後買賣超前 15 大券商分點明細、雙週期（單日 1D + 波段 5D）主力集中度與關鍵買賣券商，
並提供籌碼矩陣型態診斷（真突破、洗盤、假反彈、真出貨）與本機快取。
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

MIRROR_TEMPLATES = [
    "https://concords.moneydj.com/z/zc/zco/zco_{code}_{period}.djhtm",
    "https://fubon-ebrokerdj.fbs.com.tw/z/zc/zco/zco_{code}_{period}.djhtm",
    "https://kgieworld.moneydj.com/z/zc/zco/zco_{code}_{period}.djhtm",
]
FALLBACK_1D_URLS = [
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


def _parse_single_period_html(html_text: str) -> Optional[Dict[str, Any]]:
    """解析 MoneyDJ 單一週期之買賣超分點表格"""
    soup = BeautifulSoup(html_text, "html.parser")
    date_match = re.search(r"最後更新日：(\d{4}/\d{2}/\d{2})", soup.get_text())
    report_date = date_match.group(1) if date_match else ""

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
        concentration = round(buy_pct_sum - sell_pct_sum, 1) if (buy_pct_sum or sell_pct_sum) else 0.0
        return {
            "report_date": report_date,
            "total_buy": total_buy,
            "total_sell": total_sell,
            "net_lots": total_buy - total_sell,
            "top_buyers": top_buyers,
            "top_sellers": top_sellers,
            "concentration": concentration,
        }
    return None


def determine_matrix_status(conc_1d: Optional[float], conc_5d: Optional[float], has_dt: bool = False) -> Dict[str, str]:
    """
    根據 5 日波段與 1 日單日集中度進行四象限矩陣籌碼型態診斷。
    """
    if conc_1d is None and conc_5d is None:
        return {"status": "⚪ 無資料", "action": "觀望", "type": "none"}
    c1 = conc_1d if conc_1d is not None else 0.0
    c5 = conc_5d if conc_5d is not None else c1

    if c5 >= 5.0 and c1 >= 10.0:
        return {"status": "🔥 真突破·強勢吸籌", "action": "波段順勢做多", "type": "breakout"}
    elif c5 >= 5.0 and c1 <= -5.0:
        return {"status": "💎 波段吸籌·短線洗盤", "action": "拉回守均線低接", "type": "wash"}
    elif c5 <= -5.0 and c1 >= 10.0:
        return {"status": "⚠️ 空方反彈·隔日沖搶短", "action": "逢高獲利減碼，切忌追高", "type": "rebound"}
    elif c5 <= -5.0 and c1 <= -5.0:
        return {"status": "🚨 主力波段倒貨", "action": "空手觀望或果斷停損", "type": "dump"}
    elif c5 >= 5.0:
        return {"status": "🟢 波段主力吸籌", "action": "持股續抱", "type": "accumulate"}
    elif c5 <= -5.0:
        return {"status": "🔴 波段籌碼發散", "action": "謹慎避開", "type": "distribute"}
    elif c1 >= 10.0:
        return {"status": "⚡ 單日大單急拉", "action": "觀察次日續航力", "type": "single_buy"}
    elif c1 <= -10.0:
        return {"status": "⚠️ 單日急殺調節", "action": "提防短線回檔", "type": "single_sell"}
    else:
        return {"status": "⚪ 主力多空拉鋸", "action": "區間震盪整理", "type": "neutral"}


def _fetch_period_data(code: str, period: int, target_formatted: str = "", is_mmdd: bool = False) -> Optional[Dict[str, Any]]:
    """抓取指定週期 (1=單日, 2=5日) 之分點數據"""
    # 優先嘗試鏡像站
    for tmpl in MIRROR_TEMPLATES:
        url = tmpl.format(code=code, period=period)
        try:
            resp = requests.get(url, headers=HEADERS, timeout=8)
            if resp.status_code != 200:
                continue
            resp.encoding = "big5"
            data = _parse_single_period_html(resp.text)
            if data:
                rep_date = data.get("report_date", "")
                if target_formatted and rep_date:
                    if is_mmdd and not rep_date.endswith(target_formatted):
                        continue
                    elif not is_mmdd and rep_date != target_formatted:
                        continue
                return data
        except Exception:
            continue

    # 若 period=1 且鏡像皆未命中，嘗試舊版 fallback URL
    if period == 1:
        for url_tmpl in FALLBACK_1D_URLS:
            url = url_tmpl.format(code=code)
            try:
                resp = requests.get(url, headers=HEADERS, timeout=8)
                if resp.status_code != 200:
                    continue
                resp.encoding = "big5"
                data = _parse_single_period_html(resp.text)
                if data:
                    rep_date = data.get("report_date", "")
                    if target_formatted and rep_date:
                        if is_mmdd and not rep_date.endswith(target_formatted):
                            continue
                        elif not is_mmdd and rep_date != target_formatted:
                            continue
                    return data
            except Exception:
                continue

    return None


def fetch_broker_branch_chips(code: str, target_date: str = "") -> Optional[Dict[str, Any]]:
    """
    抓取單一個股券商分點主力買賣超（同時抓取單日 1D 與波段 5D 數據並做矩陣診斷）。
    """
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

    # 並行抓取 1D 與 5D 數據
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(_fetch_period_data, code, 1, target_formatted, is_mmdd)
        f5 = executor.submit(_fetch_period_data, code, 2, target_formatted, is_mmdd)
        data_1d = f1.result()
        data_5d = f5.result()

    if not data_1d and not data_5d:
        return None

    # 若 1D 缺漏則由 5D 替補基本資訊，反之亦然
    base_data = data_1d or data_5d
    rep_date = base_data.get("report_date", "")

    conc_1d = data_1d.get("concentration") if data_1d else None
    conc_5d = data_5d.get("concentration") if data_5d else None

    top_b_1d = data_1d.get("top_buyers", []) if data_1d else []
    top_s_1d = data_1d.get("top_sellers", []) if data_1d else []
    top_b_5d = data_5d.get("top_buyers", []) if data_5d else []
    top_s_5d = data_5d.get("top_sellers", []) if data_5d else []

    is_dt, dt_list = check_day_trader_involvement(top_b_1d)
    matrix_info = determine_matrix_status(conc_1d, conc_5d, is_dt)

    total_buy_1d = data_1d.get("total_buy", 0) if data_1d else 0
    total_sell_1d = data_1d.get("total_sell", 0) if data_1d else 0
    total_buy_5d = data_5d.get("total_buy", 0) if data_5d else 0
    total_sell_5d = data_5d.get("total_sell", 0) if data_5d else 0

    return {
        "code": code,
        # 向後相容單日舊欄位
        "broker_buy_lots": total_buy_1d,
        "broker_sell_lots": total_sell_1d,
        "broker_net_lots": total_buy_1d - total_sell_1d,
        "top_buyers": top_b_1d,
        "top_sellers": top_s_1d,
        "broker_concentration": conc_1d,
        "broker_report_date": rep_date,
        "has_day_trader": is_dt,
        "day_traders": dt_list,
        # 雙週期與矩陣新欄位
        "concentration_1d": conc_1d,
        "concentration_5d": conc_5d,
        "broker_buy_lots_5d": total_buy_5d,
        "broker_sell_lots_5d": total_sell_5d,
        "broker_net_lots_5d": total_buy_5d - total_sell_5d,
        "top_buyers_5d": top_b_5d,
        "top_sellers_5d": top_s_5d,
        "matrix_status": matrix_info["status"],
        "matrix_action": matrix_info["action"],
        "matrix_type": matrix_info["type"],
    }


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
                if "matrix_status" not in item:
                    c1 = item.get("concentration_1d", item.get("broker_concentration"))
                    c5 = item.get("concentration_5d")
                    st = determine_matrix_status(c1, c5, item.get("has_day_trader", False))
                    item["matrix_status"] = st["status"]
                    item["matrix_action"] = st["action"]
                    item["matrix_type"] = st["type"]
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
            has_5d = "concentration_5d" in cached_item and cached_item["concentration_5d"] is not None
            date_ok = True
            if target_formatted and rep_date:
                if is_mmdd:
                    date_ok = rep_date.endswith(target_formatted)
                else:
                    date_ok = (rep_date == target_formatted)
            # 若快取已有當日資料且已包含 5D 數據，則直接使用
            if date_ok and rep_date and has_5d:
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
    """根據雙週期券商分點數據生成實戰籌碼標籤"""
    if not broker_data:
        return None

    c1 = broker_data.get("concentration_1d", broker_data.get("broker_concentration"))
    c5 = broker_data.get("concentration_5d")
    if c1 is None and c5 is None:
        return None

    has_dt = broker_data.get("has_day_trader", False)
    dt_list = broker_data.get("day_traders", [])
    dt_suffix = f" ⚡隔日衝({','.join(dt_list)})" if has_dt and dt_list else ""

    status = broker_data.get("matrix_status")
    if not status:
        st = determine_matrix_status(c1, c5, has_dt)
        status = st["status"]

    top_b_1d = "、".join(broker_data.get("top_buyers", []))
    top_b_5d = "、".join(broker_data.get("top_buyers_5d", []))
    top_s_1d = "、".join(broker_data.get("top_sellers", []))
    top_s_5d = "、".join(broker_data.get("top_sellers_5d", []))

    # 詳細主力買賣分點
    detail_parts = []
    if top_b_5d and (c5 is not None and c5 >= 5.0):
        detail_parts.append(f"5D買【{top_b_5d}】")
    elif top_s_5d and (c5 is not None and c5 <= -5.0):
        detail_parts.append(f"5D賣【{top_s_5d}】")

    if top_b_1d and (c1 is not None and c1 >= 10.0):
        detail_parts.append(f"1D買【{top_b_1d}】")
    elif top_s_1d and (c1 is not None and c1 <= -10.0):
        detail_parts.append(f"1D賣【{top_s_1d}】")

    detail_str = f"｜{'·'.join(detail_parts)}" if detail_parts else ""

    if c5 is not None and c1 is not None:
        return f"{status} (5D {c5:+.1f}% ｜ 1D {c1:+.1f}%{detail_str}){dt_suffix}"
    elif c5 is not None:
        return f"{status} (5D {c5:+.1f}%{detail_str}){dt_suffix}"
    else:
        return f"{status} (1D {c1:+.1f}%{detail_str}){dt_suffix}"
