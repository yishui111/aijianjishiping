@echo off
setlocal
rem Lite web UI launcher (port 61814). Requires the analyze API (61812):
rem start it first with the top-level start bat if it is not running.
set "ROOT=%~dp0.."
if not exist "%ROOT%\runtime\Scripts\python.exe" (
  echo [ERROR] runtime\Scripts\python.exe not found. See DEPLOY.md first.
  pause
  exit /b 1
)
echo Starting lite web UI on http://127.0.0.1:61814 ...
echo Keep this window open. Close it to stop the lite web UI.
start "" http://127.0.0.1:61814
"%ROOT%\runtime\Scripts\python.exe" "%~dp0server.py"
pause
