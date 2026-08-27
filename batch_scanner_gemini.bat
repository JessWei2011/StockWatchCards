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
echo [Gemini Scanner] 正在執行專屬進化版雙軌動能掃描...
echo =================================================================
%PYTHON_CMD% batch_scanner_gemini.py
echo.
echo [Gemini Scanner] 執行完畢，榜單已輸出至 stock_winrate_ranking_gemini.md
pause
