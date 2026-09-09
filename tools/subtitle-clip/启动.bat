@echo off
cd /d "%~dp0"
echo ===== AI subtitle clip studio =====
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
echo.
pause
