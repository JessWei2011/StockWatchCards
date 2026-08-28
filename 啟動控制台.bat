@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_DIR=%LOCALAPPDATA%\Programs\Python\Python311"
set "PYTHON_EXE=%PYTHON_DIR%\python.exe"
set "PYTHONW_EXE=%PYTHON_DIR%\pythonw.exe"

if not exist "%PYTHONW_EXE%" (
  echo [ERROR] Python 3.11 was not found: %PYTHONW_EXE%
  echo Install Python 3.11, then run this launcher again.
  pause
  exit /b 1
)

"%PYTHON_EXE%" -c "import pystray, PIL" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python 3.11 is missing pystray or Pillow.
  echo Run the startup setup batch file to repair the installation.
  pause
  exit /b 1
)

for %%F in (*.pyw) do (
  start "" "%PYTHONW_EXE%" "%%~fF"
  exit /b 0
)

echo [ERROR] Controller file (*.pyw) was not found.
pause
exit /b 1
