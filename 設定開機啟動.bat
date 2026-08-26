@echo off
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0設定開機啟動.ps1"
if errorlevel 1 (
  echo [ERROR] Setup failed. Read the message above, then run this file again.
  pause
  exit /b 1
)

pause
