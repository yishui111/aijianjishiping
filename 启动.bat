@echo off
rem AI Video Studio - start (delegates to tools\subtitle-clip)
setlocal
set "SL=%~dp0tools\subtitle-clip"
if not exist "%SL%\start.ps1" (
  echo [ERROR] subtitle-clip not found at: %SL%
  echo Please make sure the folder tools\subtitle-clip exists.
  echo.
  pause
  exit /b 1
)
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%PS%" set "PS=powershell"
cd /d "%SL%"
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%SL%\start.ps1"
if errorlevel 1 (
  echo.
  echo [ERROR] startup script returned an error. See logs\ under tools\subtitle-clip.
  pause
  exit /b 1
)
pause
