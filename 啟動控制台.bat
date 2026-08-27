@echo off
setlocal
cd /d "%~dp0"

set "PYTHONW_CMD="

rem 1. 優先檢查 PATH 中的 pythonw
where pythonw >nul 2>&1 && set "PYTHONW_CMD=pythonw"

rem 2. 檢查使用者目錄下的 Python 安裝路徑
if not defined PYTHONW_CMD (
  for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if exist "%%D\pythonw.exe" set "PYTHONW_CMD=%%D\pythonw.exe"
  )
)

rem 3. 檢查 py 啟動器
if not defined PYTHONW_CMD (
  where py >nul 2>&1 && set "PYTHONW_CMD=py -3w"
)

rem 若依然找不到，執行設定腳本自動偵測與安裝套件
if not defined PYTHONW_CMD (
  echo [提示] 正在設定 Python 環境與套件...
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0設定開機啟動.ps1"
  exit /b
)

rem 在背景啟動控制台，不留黑底命令視窗
start "" %PYTHONW_CMD% "%~dp0控制台.pyw"
