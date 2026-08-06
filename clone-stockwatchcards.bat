@echo off
setlocal

set "REPO_URL=https://github.com/JessWei2011/StockWatchCards.git"
set "TARGET_DIR=%USERPROFILE%\StockWatchCards"

echo ============================================
echo   StockWatchCards Repo 下載/更新工具
echo ============================================
echo.

where git >nul 2>nul
if errorlevel 1 (
    echo [錯誤] 這台電腦沒有安裝 Git。
    echo 請先到 https://git-scm.com/downloads 下載安裝，
    echo 安裝完成後再重新執行這個檔案一次。
    echo.
    pause
    exit /b 1
)

if exist "%TARGET_DIR%\.git" (
    echo 資料夾已存在：%TARGET_DIR%
    echo 改為抓取最新內容...
    echo.
    cd /d "%TARGET_DIR%"
    git pull
) else (
    echo 準備下載到：%TARGET_DIR%
    echo.
    git clone "%REPO_URL%" "%TARGET_DIR%"
)

echo.
if errorlevel 1 (
    echo [失敗] 請把上面的錯誤訊息截圖給 Claude 看。
    echo 常見原因：需要先登入 GitHub 帳號授權。
) else (
    echo 完成！資料夾位置：%TARGET_DIR%
    echo 之後請用 Claude Code 開啟這個資料夾，就可以開始貼分析文字了。
)
echo.
pause
