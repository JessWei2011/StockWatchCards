@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

where py >nul 2>&1 && (
  py -3 launch_stock2.py
  set "RUN_STATUS=!errorlevel!"
  if not "!RUN_STATUS!"=="0" pause
  exit /b !RUN_STATUS!
)
where python >nul 2>&1 && (
  python launch_stock2.py
  set "RUN_STATUS=!errorlevel!"
  if not "!RUN_STATUS!"=="0" pause
  exit /b !RUN_STATUS!
)

echo [ERROR] Python 3.10 or later was not found.
pause
exit /b 1
