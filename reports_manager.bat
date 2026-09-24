@echo off
cd /d "%~dp0"

rem Always kill whatever is already on 8935 first, so an old/stale server.py process
rem never lingers and silently serves outdated routes after this script gets updated.
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8935 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1

rem The macro dashboard is now served by reports_manager_server.py on 8935.
rem Clean up any legacy standalone macro server that may still be listening on 8934.
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8934 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1

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

start "Stock Analysis Center" /min %PYTHON_CMD% reports_manager_server.py
ping 127.0.0.1 -n 2 >nul
start "" "http://localhost:8935/reports_manager.html"

ping 127.0.0.1 -n 2 >nul
exit
