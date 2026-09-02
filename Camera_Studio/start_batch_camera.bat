@echo off
rem ============================================================
rem  Camera batch - JSON list -> clip videos -> merged video
rem  Usage: start_batch_camera.bat <list.json> [--width 832] [--height 480]
rem ============================================================
setlocal
set "ROOT=%~dp0"
if "%~1"=="" ( echo [ERROR] Missing JSON list & echo Usage: start_batch_camera.bat list.json & pause & exit /b 1 )
echo Starting camera batch generation...
python pipeline\scripts\batch_camera.py %*
pause
