@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PYTHON_CMD="
where python >nul 2>&1 && set "PYTHON_CMD=python"
if not defined PYTHON_CMD (
  py -3 --version >nul 2>&1 && set "PYTHON_CMD=py -3"
)
if not defined PYTHON_CMD (
  echo [ERROR] Python 3 was not found. Please install Python 3 or add python.exe to PATH.
  pause
  exit /b 1
)

echo =================================================================
echo [ChatGPT Scanner] 正在執行全市場雙軌量化掃描...
echo =================================================================
%PYTHON_CMD% batch_scanner.py
echo.
echo [ChatGPT Scanner] 執行完畢，榜單已輸出至 stock_winrate_ranking.md
pause
