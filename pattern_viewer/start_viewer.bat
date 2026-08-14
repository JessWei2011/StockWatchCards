@echo off
cd /d "%~dp0"

rem Always kill whatever is already listening on 8888-8897 first, so a stale server.py
rem process never lingers and silently serves outdated routes after this script gets updated.
for /L %%p in (8888,1,8897) do (
  for /f "tokens=5" %%a in ('netstat -aon ^| findstr :%%p ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
)
rem server.py opens the browser itself once it's up, no need to do it here.
start "PatternViewer Local Server" /min cmd /c "python server.py"

timeout /t 2 >nul
exit
