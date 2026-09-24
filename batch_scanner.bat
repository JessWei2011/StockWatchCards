@echo off
cd /d "%~dp0"

set "PYTHON_CMD="
where py >nul 2>&1 && set "PYTHON_CMD=py -3"
if not defined PYTHON_CMD (
  where python >nul 2>&1 && set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
  echo [ERROR] Python 3 was not found. Please install Python 3 or add python.exe to PATH.
  pause
  exit /b 1
)

echo =================================================================
echo [ChatGPT Scanner] Running dual-track quantitative scanner...
echo =================================================================
%PYTHON_CMD% batch_scanner.py
echo.
echo [ChatGPT Scanner] Completed. Output saved to stock_winrate_ranking.md
pause
