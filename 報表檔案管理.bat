@echo off
setlocal EnableExtensions DisableDelayedExpansion
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

set "MANAGER_URL=http://localhost:8935/report_manager.html"

for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8935 ^| findstr LISTENING') do goto :open_manager

start "Stock Reports Server" /min %PYTHON_CMD% reports_manager_server.py

rem Wait until the local server is really listening.  This prevents the browser
rem from opening a failed/partially loaded page on slower Windows machines.
for /l %%i in (1,1,12) do (
  for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8935 ^| findstr LISTENING') do goto :open_manager
  timeout /t 1 /nobreak >nul
)

echo Failed to start the local report manager server.
pause
exit /b 1

:open_manager
start "" "%MANAGER_URL%"
endlocal
exit /b 0
