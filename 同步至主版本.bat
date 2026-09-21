@echo off
setlocal
cd /d "%~dp0"

echo 將以主版本更新此電腦，完成後會自動開啟報表檔案管理。
echo 注意：本機尚未推送的已追蹤資料將被主版本覆蓋。
choice /C YN /N /M "是否開始同步"
if errorlevel 2 (
    echo 已取消，沒有變更任何檔案。
    pause
    exit /b 0
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0sync_to_canonical_main.ps1" -Confirm
set "SCRIPT_EXIT=%ERRORLEVEL%"

if not "%SCRIPT_EXIT%"=="0" (
    echo [ERROR] 同步未完成。
    pause
    exit /b %SCRIPT_EXIT%
)

call "%~dp0reports_manager.bat"
exit /b 0
