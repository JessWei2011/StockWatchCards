"""
StockCenter Interface Service
提供給外部系統（如 NotifyRobot）讀取個股最新指標與標籤的標準介面層
"""

import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent
REPORTS_DIR = ROOT_DIR / "reports"
GENERATOR_SCRIPT = ROOT_DIR / "stock_report_generator.py"


def resolve_stock_code(ticker_input: str) -> tuple[str, str]:
    """將輸入代碼或名稱解析為代碼與名稱"""
    raw = ticker_input.strip()
    sid = re.sub(r'\.(TWO|TW)$', '', raw.upper())

    # 檢查是否為純代碼 (台股代號通常為 4~6 碼數字/英數字)
    if re.fullmatch(r'[0-9A-Z]+', sid):
        return sid, ""

    # 名稱查詢
    try:
        from stock_report_generator import resolve_by_name
        code, name = resolve_by_name(raw)
        if code:
            return code, name
    except Exception as e:
        logger.warning(f"resolve_by_name 失敗: {e}")

    return sid, raw


def run_report_generator(ticker: str, timeout: int = 60) -> tuple[bool, str]:
    """呼叫 stock_report_generator.py 更新該個股報表"""
    if not GENERATOR_SCRIPT.exists():
        return False, f"找不到生成器腳本: {GENERATOR_SCRIPT}"

    child_env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        proc = subprocess.run(
            [sys.executable, str(GENERATOR_SCRIPT), ticker],
            cwd=str(ROOT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=child_env,
            timeout=timeout,
        )
        return proc.returncode == 0, proc.stdout
    except subprocess.TimeoutExpired:
        return False, f"產生報表超時 ({timeout}s)"
    except Exception as e:
        return False, f"執行失敗: {e}"


def find_latest_report_file(sid: str) -> Optional[Path]:
    """在 reports 目錄中尋找指定代碼最新的 HTML 報表"""
    pattern = f"{sid}_*.html"
    matches = list(REPORTS_DIR.rglob(pattern))
    if not matches:
        return None
    # 依修改時間降序排序，取最新者
    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0]


def extract_stock_tags_from_file(file_path: Path) -> dict:
    """從 HTML 報表解析數據並計算各面向指標標籤"""
    from batch_scanner import (
        parse_html_report,
        detect_kline_tags,
        detect_volume_tags,
        detect_macd_tags,
        detect_kd_tags,
        detect_rsi_tags,
        detect_chip_tags,
    )

    info = parse_html_report(file_path)
    if not info:
        raise ValueError(f"無法解析報表內容: {file_path}")

    kline_list = info.get("kline", [])
    if not kline_list:
        raise ValueError("報表中無有效的 K 線數據")

    df = pd.DataFrame(kline_list)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna().reset_index(drop=True)

    if len(df) < 5:
        raise ValueError("K 線資料筆數不足")

    close_s = df["close"]
    high_s = df["high"]
    low_s = df["low"]

    is_disposal = ("處置" in info.get("name", "")) or ("處置" in str(file_path))

    # 計算各維度標籤
    kline_tags = detect_kline_tags(df)
    vol_tags = detect_volume_tags(df, is_disposal=is_disposal)
    kd_tags = detect_kd_tags(high_s, low_s, close_s)
    macd_tags = detect_macd_tags(close_s)
    rsi_tags = detect_rsi_tags(close_s)
    chip_tags = detect_chip_tags(info, kline_df=df)

    # 彙整行情簡訊
    cur_close = float(close_s.iloc[-1])
    prev_close = float(close_s.iloc[-2]) if len(close_s) >= 2 else cur_close
    change = cur_close - prev_close
    change_pct = (change / prev_close * 100.0) if prev_close > 0 else 0.0
    latest_vol = float(df["volume"].iloc[-1])
    vol_lots = int(latest_vol / 1000) if latest_vol > 0 else 0
    latest_date = str(df["date"].iloc[-1])

    all_tags = []
    for tags in (kline_tags, vol_tags, kd_tags, macd_tags, rsi_tags, chip_tags):
        all_tags.extend(tags)

    return {
        "code": info.get("code"),
        "name": info.get("name"),
        "market": info.get("market"),
        "category": info.get("category"),
        "date": latest_date,
        "price": cur_close,
        "prev_close": prev_close,
        "change": round(change, 2),
        "change_pct": round(change_pct, 2),
        "volume_lots": vol_lots,
        "tags": {
            "kline": kline_tags,
            "volume": vol_tags,
            "kd": kd_tags,
            "macd": macd_tags,
            "rsi": rsi_tags,
            "chips": chip_tags,
        },
        "all_tags": all_tags,
        "report_file": str(file_path.relative_to(ROOT_DIR)),
    }


def get_stock_report_and_tags(ticker_input: str, force_update: bool = True) -> dict:
    """主要介面函式：更新並回傳個股標籤資料"""
    sid, resolved_name = resolve_stock_code(ticker_input)

    gen_output = ""
    target_ticker = sid if sid else ticker_input

    if force_update:
        ok, gen_output = run_report_generator(target_ticker)
        if not ok and not sid:
            return {
                "ok": False,
                "error": f"查無此股票代號或更新失敗: {ticker_input}",
                "detail": gen_output,
            }

    # 尋找報表
    report_file = find_latest_report_file(sid) if sid else None
    if not report_file:
        # 如果 force_update 為 False，且尚未有報表，嘗試跑一次
        if not force_update:
            ok, gen_output = run_report_generator(target_ticker)
            report_file = find_latest_report_file(sid) if sid else None

    if not report_file:
        return {
            "ok": False,
            "error": f"找不到 {ticker_input} ({sid}) 的 HTML 報表檔案",
            "detail": gen_output,
        }

    try:
        data = extract_stock_tags_from_file(report_file)
        data["ok"] = True
        return data
    except Exception as e:
        logger.exception("解析標籤失敗")
        return {
            "ok": False,
            "error": f"解析報表標籤失敗: {e}",
            "detail": gen_output,
        }
