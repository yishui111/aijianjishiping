@echo off
rem ============================================================
rem  Anime Image System - one-click start
rem  1) ComfyUI engine (Illustrious v1.0, port 8188)
rem  2) Anime workbench -> http://127.0.0.1:8093
rem  Close window to stop; or use stop_anime.bat
rem ============================================================
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%ComfyUI_windows_portable\python_embeded\python.exe"

if not exist "%PY%" ( echo [ERROR] ComfyUI python not found: %PY% & pause & exit /b 1 )

rem ---------- 1. ComfyUI engine ----------
echo [1/2] Starting ComfyUI engine (8188) ...
start "ComfyUI-Anime" /min cmd /c ""%PY%" -s "%ROOT%ComfyUI_windows_portable\ComfyUI\main.py" --windows-standalone-build --disable-dynamic-vram --port 8188 > "%ROOT%comfy_8188.log" 2>&1"

set /a tries=0
:wait_comfy
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8188/system_stats' -UseBasicParsing -TimeoutSec 3; if ($r.StatusCode -eq 200) { exit 0 } } catch { exit 1 }"
if %errorlevel%==0 goto comfy_ok
set /a tries+=1
if %tries% GEQ 30 ( echo [ERROR] ComfyUI not ready in 60s, see comfy_8188.log & pause & exit /b 1 )
timeout /t 2 /nobreak >nul
goto wait_comfy
:comfy_ok
echo        ComfyUI ready.

rem ---------- 2. Anime workbench ----------
echo [2/2] Starting Anime workbench: http://127.0.0.1:8093  (close window to stop)
start "" powershell -NoProfile -Command "Start-Sleep -Seconds 8; Start-Process 'http://127.0.0.1:8093'"
"%PY%" pipeline\scripts\anime_workbench.py
pause
