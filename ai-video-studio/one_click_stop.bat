@echo off
cd /d "%~dp0"
echo.
echo ===== AI Video Studio - ONE-CLICK STOP =====
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_local.ps1"
echo.
pause >nul
