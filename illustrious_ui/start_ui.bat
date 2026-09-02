@echo off
rem ============================================================
rem  Illustrious official-style UI (Gradio)
rem  Open http://127.0.0.1:7860
rem  Require: anime engine (8188) running first
rem ============================================================
setlocal
set "ROOT=%~dp0"
if not exist "%ROOT%venv\Scripts\python.exe" ( echo [ERROR] venv not found. Run setup first. & pause & exit /b 1 )
echo Starting Illustrious UI: http://127.0.0.1:7860  (Ctrl+C to stop)
start "" powershell -NoProfile -Command "Start-Sleep -Seconds 8; Start-Process 'http://127.0.0.1:7860'"
"%ROOT%venv\Scripts\python.exe" "%ROOT%app.py"
pause
