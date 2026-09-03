@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo =================================================================
echo 👑 [AI Self-Evolution] 正在執行每日盤後覆盤與獨有勝率榜自我進化...
echo =================================================================
py -3.11 evolution_engine.py
if errorlevel 1 (
  python evolution_engine.py
)
echo.
echo [AI Self-Evolution] 執行完畢！
echo - 實戰榜單已更新至：stock_winrate_ranking_evolution.md
echo - 覆盤日記已更新至：evolution_log.md
echo =================================================================
pause
