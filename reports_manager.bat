@echo off
cd /d "%~dp0"

rem Always kill whatever is already on 8935 first, so an old/stale server.py process
rem never lingers and silently serves outdated routes after this script gets updated.
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8935 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
start "Reports Manager Local Server" /min cmd /c "python reports_manager_server.py"
timeout /t 1 >nul
start "" "http://localhost:8935/reports_manager.html"

timeout /t 2 >nul
exit
