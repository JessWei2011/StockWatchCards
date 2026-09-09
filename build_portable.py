# -*- coding: utf-8 -*-
"""
Stock2 免安裝綠色便攜包自動打包腳本
功能：
1. 自動抽取本機 Python 3.11 環境（含 yfinance, pandas, mplfinance, pystray 等依賴）
2. 複製專案必要檔案，排除 .git、暫存檔與歷史大快取
3. 產生無腦啟動腳本「🚀啟動股票系統.bat」與關閉腳本「結束股票系統.bat」
4. 自動壓縮為 Stock2_Portable.zip
"""

import os
import sys
import shutil
import zipfile
import subprocess
from pathlib import Path

if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT_DIR = Path(__file__).resolve().parent
DIST_DIR = ROOT_DIR / "dist"
OUTPUT_DIR = DIST_DIR / "Stock2_Portable"
PYTHON_SRC_DIR = Path(sys.executable).resolve().parent

# 專案需要複製的頂層檔案與目錄
INCLUDE_FILES = [
    "reports_manager_server.py",
    "stock_report_generator.py",
    "batch_scanner.py",
    "batch_scanner_gemini.py",
    "控制台.pyw",
    "launch_stock2.py",
    "deploy_mobile.py",
    "requirements.txt",
    "reports_manager.html",
    "render-shared.js",
    "stock_name_dict.json",
    "watchlist.json",
    "scratch_stocks_summary.json",
    "breakout_watchlist.md",
    "breakout_watchlist_gemini.md",
    "stock_winrate_ranking.md",
    "stock_winrate_ranking_gemini.md",
    "export_mobile_site.py",
    "發布手機版.bat",
]

INCLUDE_DIRS = [
    "指標數據",
    "pattern_viewer",
    "mobile_web",
]

def print_step(msg: str):
    print(f"\n{'='*50}\n>> {msg}\n{'='*50}")

def clean_output_dir():
    print_step("正在清理舊的建構目錄...")
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR, ignore_errors=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"輸出目錄就緒: {OUTPUT_DIR}")

def copy_python_env():
    print_step("正在抽取免安裝 Python 環境 (python_env)...")
    target_env = OUTPUT_DIR / "python_env"
    target_env.mkdir(parents=True, exist_ok=True)

    print(f"來源 Python 目錄: {PYTHON_SRC_DIR}")

    # 1. 複製根目錄下的核心 DLL 與 EXE
    for item in PYTHON_SRC_DIR.glob("*"):
        if item.is_file():
            # 複製 .exe, .dll, LICENSE 等
            if item.suffix.lower() in [".exe", ".dll", ".txt"]:
                shutil.copy2(item, target_env / item.name)

    # 2. 複製 DLLs 與 Lib 目錄 (依賴套件核心)
    for sub in ["DLLs", "Lib"]:
        src_sub = PYTHON_SRC_DIR / sub
        dst_sub = target_env / sub
        if src_sub.exists():
            print(f"正在複製 {sub} (請稍候)...")
            shutil.copytree(
                src_sub,
                dst_sub,
                ignore=shutil.ignore_patterns(
                    "__pycache__", "*.pyc", "*.pyo", "test", "tests"
                ),
                dirs_exist_ok=True,
            )

    print("Python 環境抽取完成！")

def copy_project_files():
    print_step("正在複製 Stock2 核心程式與資源...")

    # 複製單獨檔案
    for filename in INCLUDE_FILES:
        src = ROOT_DIR / filename
        if src.exists():
            shutil.copy2(src, OUTPUT_DIR / filename)
            print(f"已複製: {filename}")
        else:
            print(f"[略過] 未找到檔案: {filename}")

    # 複製目錄
    for dirname in INCLUDE_DIRS:
        src = ROOT_DIR / dirname
        dst = OUTPUT_DIR / dirname
        if src.exists():
            print(f"正在複製目錄: {dirname}...")
            shutil.copytree(
                src,
                dst,
                ignore=shutil.ignore_patterns(
                    ".git", ".gitignore", "__pycache__", "*.pyc", "*.pyo", "*.log"
                ),
                dirs_exist_ok=True,
            )

    # 複製原有的 reports 內容（包含所有已分析個股 HTML 報表、線圖、Markdown 分析報告）
    src_reports = ROOT_DIR / "reports"
    dst_reports = OUTPUT_DIR / "reports"
    if src_reports.exists():
        print("正在複製原有 reports 報表資料...")
        shutil.copytree(
            src_reports,
            dst_reports,
            ignore=shutil.ignore_patterns("__pycache__", "*.tmp", "*.log"),
            dirs_exist_ok=True,
        )
        report_count = len(list(dst_reports.glob("*.html")))
        print(f"已成功複製原有 reports 內容！共 {report_count} 份個股報表。")
    else:
        dst_reports.mkdir(exist_ok=True)

    # 複製原有的 cache 快取資料 (大戶持股、歷史行情快取，加速朋友使用)
    src_cache = ROOT_DIR / "cache"
    dst_cache = OUTPUT_DIR / "cache"
    if src_cache.exists():
        print("正在複製原有 cache 快取資料...")
        shutil.copytree(
            src_cache,
            dst_cache,
            ignore=shutil.ignore_patterns("__pycache__", "*.tmp"),
            dirs_exist_ok=True,
        )
        print("已成功複製原有 cache 快取！")
    else:
        dst_cache.mkdir(exist_ok=True)

def create_launcher_scripts():
    print_step("正在生成無腦啟動腳本...")

    # 1. 啟動腳本 (使用萬用字元 *.pyw 確保在任何語系的 Windows 都不會因檔名編碼失敗)
    launcher_bat = OUTPUT_DIR / "啟動股票系統.bat"
    launcher_content = """@echo off
setlocal
cd /d "%~dp0"

echo [Stock2] 正在啟動股票分析系統，請稍候...

:: 優先使用隨附的免安裝 Python
set "PYW=%~dp0python_env\\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw.exe"

:: 背景靜態啟動控制台 (*.pyw 自動匹配，無黑窗)
for %%F in ("%~dp0*.pyw") do (
    start "" "%PYW%" "%%~fF"
)

:: 等候 2 秒讓後端伺服器 (Port 8935) 初始化
timeout /t 2 /nobreak >nul

:: 自動開啟預設瀏覽器進入個股分析中心
start "" "http://localhost:8935/reports_manager.html"

exit /b 0
"""
    launcher_bat.write_text(launcher_content, encoding="utf-8")
    print("已生成: 啟動股票系統.bat")

    # 2. 結束腳本
    stop_bat = OUTPUT_DIR / "結束股票系統.bat"
    stop_content = """@echo off
setlocal
cd /d "%~dp0"

echo [Stock2] 正在關閉所有股票背景伺服器...

set "PY=%~dp0python_env\\python.exe"
if not exist "%PY%" set "PY=python.exe"

"%PY%" -c "import urllib.request; [urllib.request.urlopen(urllib.request.Request(f'http://localhost:{p}/api/shutdown', method='POST', data=b''), timeout=1) for p in [8935, 8934]]" 2>nul

echo [Stock2] 服務已安全結束。
timeout /t 2 /nobreak >nul
exit /b 0
"""
    stop_bat.write_text(stop_content, encoding="utf-8")
    print("已生成: 結束股票系統.bat")

    # 3. 貼心使用說明
    readme_txt = OUTPUT_DIR / "使用說明.txt"
    readme_content = """==============================================
       Stock2 股票分析系統 - 免安裝綠色便攜版
==============================================

【使用方式】
1. 雙擊執行「啟動股票系統.bat」
2. 等候 2~3 秒，瀏覽器會自動彈出個股分析中心！
3. 同時電腦右下角（工作列系統匣）會出現股票圖示。

【關閉方式】
- 在右下角股票圖示上按滑鼠右鍵，選擇「結束統一控制台」
- 或雙擊「結束股票系統.bat」即可。

【注意事項】
- 本軟體已內建專屬 Python 執行環境，本機不需要安裝任何程式。
- 請勿任意刪除 python_env 資料夾。
==============================================
"""
    readme_txt.write_text(readme_content, encoding="utf-8")
    print("已生成: 使用說明.txt")

def create_zip_archive():
    print_step("正在打包為 ZIP 壓縮檔 (Stock2_Portable.zip)...")
    zip_path = DIST_DIR / "Stock2_Portable.zip"
    if zip_path.exists():
        zip_path.unlink()

    total_files = sum(len(files) for _, _, files in os.walk(OUTPUT_DIR))
    processed = 0

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for root, dirs, files in os.walk(OUTPUT_DIR):
            for file in files:
                full_path = Path(root) / file
                rel_path = full_path.relative_to(OUTPUT_DIR.parent)
                zf.write(full_path, rel_path)
                processed += 1
                if processed % 500 == 0 or processed == total_files:
                    pct = int(processed / total_files * 100)
                    print(f"壓縮進度: {pct}% ({processed}/{total_files})")

    file_size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"\n壓縮完成！產出檔案：{zip_path}")
    print(f"檔案大小：{file_size_mb:.2f} MB")

def sync_mobile_snapshot():
    print_step("正在為便攜包預先同步手機版全個股離線快照...")
    try:
        py_exe = OUTPUT_DIR / "python_env" / "python.exe"
        if not py_exe.exists():
            py_exe = Path(sys.executable)
        subprocess.run(
            [str(py_exe), str(OUTPUT_DIR / "export_mobile_site.py")],
            cwd=OUTPUT_DIR,
            check=False,
        )
        print("手機版全個股快照同步完成！")
    except Exception as e:
        print(f"[提示] 手機版資料預先同步略過: {e}")

def main():
    print("開始執行 Stock2 免安裝發布包打包作業...")
    clean_output_dir()
    copy_python_env()
    copy_project_files()
    sync_mobile_snapshot()
    create_launcher_scripts()
    create_zip_archive()
    print_step("全部打包作業順利完成！")
    print(f"發布資料夾位於：{OUTPUT_DIR}")
    print(f"ZIP 壓縮檔位於：{DIST_DIR / 'Stock2_Portable.zip'}")

if __name__ == "__main__":
    main()
