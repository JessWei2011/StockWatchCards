@echo off
cd /d "%~dp0\.."

rem Compatibility entry for old shortcuts. PatternViewer is now a workspace inside
rem the unified stock analysis center and no longer starts its own local server.
call reports_manager.bat
