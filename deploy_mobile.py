#!/usr/bin/env python3
"""Windows 與 macOS 共用的手機版資料匯出與 Cloudflare 部署入口。"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
MOBILE_DIR = ROOT_DIR / "mobile_web"
PUBLIC_DIR = MOBILE_DIR / "public"
DATA_DIR = PUBLIC_DIR / "data"


def fail(message: str) -> int:
    print(f"[錯誤] {message}")
    return 1


def main() -> int:
    print("[1/3] 正在匯出全個股快照資料…")
    export_result = subprocess.run([sys.executable, str(ROOT_DIR / "export_mobile_site.py")], cwd=ROOT_DIR)
    if export_result.returncode:
        return fail("資料匯出失敗，已終止發布。")

    print("[2/3] 正在驗證手機版靜態檔案…")
    required = [DATA_DIR / "index.json", DATA_DIR / "stocks", PUBLIC_DIR / "index.html", PUBLIC_DIR / "app.js"]
    missing = [str(path.relative_to(ROOT_DIR)) for path in required if not path.exists()]
    if missing:
        return fail("缺少必要檔案：" + "、".join(missing))
    try:
        index = json.loads((DATA_DIR / "index.json").read_text(encoding="utf-8"))
        stock_dirs = [path for path in (DATA_DIR / "stocks").iterdir() if path.is_dir()]
        expected_count = index.get("count", len(index.get("stocks", [])))
        if expected_count <= 0 or expected_count != len(stock_dirs):
            return fail("個股索引與輸出目錄數量不一致。")
    except (OSError, ValueError) as error:
        return fail(f"無法驗證手機版資料：{error}")

    print(f"[OK] 索引確認：{expected_count} 檔個股。")
    print("[3/3] 正在部署至 Cloudflare Worker…")
    wrangler = shutil.which("wrangler")
    if wrangler:
        command = [wrangler, "deploy"]
    else:
        npx = shutil.which("npx")
        if not npx:
            return fail("找不到 Node.js／npx。請安裝 Node.js 20 以上後重試。")
        command = [npx, "wrangler", "deploy"]
    result = subprocess.run(command, cwd=MOBILE_DIR)
    if result.returncode:
        return fail("Cloudflare 部署失敗，請檢查登入憑證與網路。")

    print("[成功] 手機分析中心已發布。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
