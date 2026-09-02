@echo off
cd /d "%~dp0"
echo.
echo ===== AI Video Studio - ONE-CLICK START =====
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_local.ps1"
echo.
echo Done. Press any key to close...
pause >nul
