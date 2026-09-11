@echo off
rem AI Video Studio - stop (delegates to tools\subtitle-clip)
setlocal
set "SL=%~dp0tools\subtitle-clip"
if not exist "%SL%\stop.ps1" (
  echo [ERROR] subtitle-clip not found at: %SL%
  echo Please make sure the folder tools\subtitle-clip exists.
  echo.
  pause
  exit /b 1
)
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%PS%" set "PS=powershell"
cd /d "%SL%"
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%SL%\stop.ps1"
pause
