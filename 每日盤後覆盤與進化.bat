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
echo.
echo 🏦 正在更新法人籌碼策略榜（失敗不影響既有技術掃描與排行）...
where py >nul 2>&1 && (
  py -3 institutional_chip_strategy.py
  goto institutional_done
)
python institutional_chip_strategy.py
:institutional_done
if errorlevel 1 (
  echo [警告] 法人籌碼策略榜更新失敗，既有技術掃描與排行未受影響。
) else (
  echo - 法人籌碼策略榜已更新至：institutional_chip_strategy_ranking.md
)
echo =================================================================
pause
