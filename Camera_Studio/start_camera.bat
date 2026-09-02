@echo off
rem ============================================================
rem  Camera studio - one-click start (pure ffmpeg, no GPU)
rem  Open http://127.0.0.1:8094
rem ============================================================
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%pipeline\scripts\camera_workbench.py"
if not exist "%ROOT%tools\ffmpeg\bin\ffmpeg.exe" ( echo [ERROR] ffmpeg not found: %ROOT%tools\ffmpeg\bin\ffmpeg.exe & pause & exit /b 1 )
echo Starting camera workbench: http://127.0.0.1:8094  (Ctrl+C to stop)
start "" powershell -NoProfile -Command "Start-Sleep -Seconds 8; Start-Process 'http://127.0.0.1:8094'"
python "%PY%"
pause
