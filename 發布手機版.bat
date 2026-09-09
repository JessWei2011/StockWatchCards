@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>&1 && (
  py -3 deploy_mobile.py
  exit /b %errorlevel%
)
python deploy_mobile.py
