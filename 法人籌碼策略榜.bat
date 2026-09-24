@echo off
setlocal
cd /d "%~dp0"

echo ================================================================
echo Fetching TWSE T86 and TPEx institutional investor data...
echo ================================================================
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

%PYTHON_CMD% institutional_chip_strategy.py
set "STATUS=%ERRORLEVEL%"
if not "%STATUS%"=="0" pause
exit /b %STATUS%
