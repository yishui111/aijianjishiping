@echo off
rem ============================================================
rem  FLUX2 Image System - one-click start
rem  1) ComfyUI engine (FLUX2 Klein 9B, port 8189)
rem  2) Image workbench -> http://127.0.0.1:8092
rem  Close window to stop; or use stop_flux2.bat
rem  Model precision: default Q8_0 (16G GPU); 8G GPU: set FLUX2_QUANT=Q5_K_M
rem ============================================================
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%ComfyUI_windows_portable\python_embeded\python.exe"
set "COMFY_PORT=8189"
set "COMFY_URL=http://127.0.0.1:8189"
if "%FLUX2_QUANT%"=="" set "FLUX2_QUANT=Q8_0"

if not exist "%PY%" ( echo [ERROR] ComfyUI python not found: %PY% & pause & exit /b 1 )

rem ---------- 1. ComfyUI engine ----------
echo [1/2] Starting ComfyUI engine (%COMFY_PORT%) ...
start "ComfyUI-FLUX2" /min cmd /c ""%PY%" -s "%ROOT%ComfyUI_windows_portable\ComfyUI\main.py" --windows-standalone-build --disable-dynamic-vram --port %COMFY_PORT% > "%ROOT%comfy_%COMFY_PORT%.log" 2>&1"

set /a tries=0
:wait_comfy
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:%COMFY_PORT%/system_stats' -UseBasicParsing -TimeoutSec 3; if ($r.StatusCode -eq 200) { exit 0 } } catch { exit 1 }"
if %errorlevel%==0 goto comfy_ok
set /a tries+=1
if %tries% GEQ 30 ( echo [ERROR] ComfyUI not ready in 60s, see comfy_%COMFY_PORT%.log & pause & exit /b 1 )
timeout /t 2 /nobreak >nul
goto wait_comfy
:comfy_ok
echo        ComfyUI ready.

rem ---------- 2. Image workbench ----------
echo [2/2] Starting Image workbench: http://127.0.0.1:8092  (close window to stop)
start "" powershell -NoProfile -Command "Start-Sleep -Seconds 8; Start-Process 'http://127.0.0.1:8092'"
"%PY%" pipeline\scripts\flux2_workbench.py
pause
