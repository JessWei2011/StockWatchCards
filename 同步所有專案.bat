@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

where git >nul 2>&1
if errorlevel 1 (
  echo [ERROR] 找不到 Git，請先安裝 Git for Windows。
  goto :failed
)

if not exist ".git" (
  echo [ERROR] 目前資料夾不是 Stock2 Git repository：%CD%
  goto :failed
)

if not exist "指標數據\.git" (
  echo [ERROR] 找不到「指標數據」的獨立 Git repository。
  goto :failed
)

echo [1/2] 正在更新 Stock2...
git pull --ff-only
if errorlevel 1 (
  echo [ERROR] Stock2 更新失敗，已停止後續同步。
  goto :failed
)

echo.
echo [2/2] 正在更新總經指標數據...
git -C "指標數據" pull --ff-only
if errorlevel 1 (
  echo [ERROR] 總經指標數據更新失敗。
  goto :failed
)

echo.
echo [OK] Stock2 與總經指標數據都已同步完成。
pause
exit /b 0

:failed
echo.
echo 請保留上方錯誤訊息，以便檢查本機修改或 Git 衝突。
pause
exit /b 1
