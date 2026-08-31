@echo off
setlocal
chcp 65001 >nul
title Stock2 全個股手機版發布工具

echo ===================================================
echo     Stock2 全個股手機分析中心 - 一鍵發布作業
echo ===================================================
echo.

set "PROJECT_ROOT=%~dp0"
set "MOBILE_DIR=%PROJECT_ROOT%mobile_web"

set "PY_EXE="
where python >nul 2>&1 && set "PY_EXE=python"
if not defined PY_EXE (
    if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PY_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
)
if not defined PY_EXE (
    py -3 --version >nul 2>&1 && set "PY_EXE=py -3"
)
if not defined PY_EXE (
    echo [錯誤] 找不到 Python 執行檔，請確認已安裝 Python 3。
    goto FAIL
)

echo [1/3] 正在執行全個股快照資料匯出 export_mobile_site.py ...
"%PY_EXE%" "%PROJECT_ROOT%export_mobile_site.py"
if errorlevel 1 goto EXPORT_FAIL

echo.
echo [2/3] 正在驗證手機版必要靜態檔案與個股目錄...
if not exist "%MOBILE_DIR%\public\data\index.json" (
    echo [錯誤] 找不到 %MOBILE_DIR%\public\data\index.json
    goto FAIL
)
if not exist "%MOBILE_DIR%\public\data\stocks" (
    echo [錯誤] 找不到 %MOBILE_DIR%\public\data\stocks 目錄
    goto FAIL
)
if not exist "%MOBILE_DIR%\public\index.html" (
    echo [錯誤] 找不到 %MOBILE_DIR%\public\index.html
    goto FAIL
)
if not exist "%MOBILE_DIR%\public\app.js" (
    echo [錯誤] 找不到 %MOBILE_DIR%\public\app.js
    goto FAIL
)

"%PY_EXE%" -c "import json, sys, pathlib; p = pathlib.Path(r'%MOBILE_DIR%\public\data'); idx = json.loads((p/'index.json').read_text(encoding='utf-8')); stocks = [d for d in (p/'stocks').iterdir() if d.is_dir()]; cnt = idx.get('count', len(idx.get('stocks', []))); print(f'[OK] 索引確認：{cnt} 檔個股，磁碟目錄：{len(stocks)} 個。'); sys.exit(0 if cnt > 0 and cnt == len(stocks) else 1)"
if errorlevel 1 (
    echo [錯誤] 個股索引與輸出目錄數量不一致，終止發布！
    goto FAIL
)
echo [OK] 靜態檔案與全個股資料完整性檢查通過。

echo.
echo [3/3] 正在部署至 Cloudflare Worker stock2-mobile ...
cd /d "%MOBILE_DIR%"
call "node_modules\.bin\wrangler.cmd" deploy
if errorlevel 1 goto DEPLOY_FAIL

cd /d "%PROJECT_ROOT%"
echo.
echo ===================================================
echo [成功] 全個股手機分析中心已成功發布上線！
echo 網址: https://stock2-mobile.lilis0501.workers.dev/
echo ===================================================
echo.
if "%1"=="--no-pause" exit /b 0
pause
exit /b 0

:EXPORT_FAIL
echo.
echo [錯誤] 資料匯出失敗，已終止發布。
goto FAIL

:DEPLOY_FAIL
cd /d "%PROJECT_ROOT%"
echo.
echo [錯誤] Cloudflare 部署失敗，請檢查網路或憑證。
goto FAIL

:FAIL
echo.
echo 發布未完成。
if "%1"=="--no-pause" exit /b 1
pause
exit /b 1
