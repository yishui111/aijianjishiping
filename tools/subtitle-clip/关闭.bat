@echo off
cd /d "%~dp0"
echo ===== Stop AI subtitle clip studio =====
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1"
pause
