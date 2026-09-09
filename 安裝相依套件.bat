@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

where py >nul 2>&1 && (
  py -3 -m pip install --upgrade pip
  py -3 -m pip install -r requirements.txt
  set "RUN_STATUS=!errorlevel!"
  pause
  exit /b !RUN_STATUS!
)
where python >nul 2>&1 && (
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt
  set "RUN_STATUS=!errorlevel!"
  pause
  exit /b !RUN_STATUS!
)

echo [ERROR] Python 3.10 or later was not found.
pause
exit /b 1
