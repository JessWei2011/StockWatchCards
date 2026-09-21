@echo off
setlocal
cd /d "%~dp0"

echo This will replace this computer's tracked StockCenter files with origin/main.
echo Local uncommitted changes and local commits not on origin/main will be discarded.
echo Untracked files outside reports and ignored files will be kept.
set /p SYNC_CONFIRM=Type YES to continue: 
if /I not "%SYNC_CONFIRM%"=="YES" (
    echo Cancelled. No files were changed.
    pause
    exit /b 0
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0sync_to_canonical_main.ps1" -Confirm
set "SCRIPT_EXIT=%ERRORLEVEL%"

echo.
if not "%SCRIPT_EXIT%"=="0" echo [ERROR] Sync did not complete.
echo Press any key to close this window.
pause >nul
exit /b %SCRIPT_EXIT%
