@echo off
setlocal
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0commit_push_all.ps1" %*
set "SCRIPT_EXIT=%ERRORLEVEL%"

echo.
if not "%SCRIPT_EXIT%"=="0" echo [ERROR] Commit and push did not complete.
echo Press any key to close this window.
pause >nul
exit /b %SCRIPT_EXIT%
