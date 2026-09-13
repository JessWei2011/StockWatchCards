@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ================================================================
echo 🏦 正在抓取 TWSE T86 與 TPEx 三大法人盤後資料...
echo ================================================================
where py >nul 2>&1 && (
  py -3 institutional_chip_strategy.py
  exit /b %errorlevel%
)
python institutional_chip_strategy.py
