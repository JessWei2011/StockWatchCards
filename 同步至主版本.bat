@echo off
setlocal
cd /d "%~dp0"

echo This machine will be updated from the canonical main branch.
echo Reports manager will open automatically after completion.
echo Note: Local unpushed tracked changes will be overwritten by main.
choice /C YN /M "Start sync now? [Y/N]"
if errorlevel 2 (
    echo Cancelled. No files were changed.
    pause
    exit /b 0
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0sync_to_canonical_main.ps1" -Confirm
set "SCRIPT_EXIT=%ERRORLEVEL%"

if not "%SCRIPT_EXIT%"=="0" (
    echo [ERROR] Sync did not complete successfully.
    pause
    exit /b %SCRIPT_EXIT%
)

call "%~dp0reports_manager.bat"
exit /b 0
