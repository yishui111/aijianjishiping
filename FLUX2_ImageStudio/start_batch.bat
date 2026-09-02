@echo off
rem ============================================================
rem  FLUX2 batch image generation
rem  Usage: start_batch.bat <script.json> [character_dir]
rem  Example: start_batch.bat script.json chars
rem  (chars dir contains <char_id>.png, e.g. c1.png c2.png)
rem  8G GPU: add --width 640 --height 384 --steps 16
rem ============================================================
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%ComfyUI_windows_portable\python_embeded\python.exe"
set "COMFY_URL=http://127.0.0.1:8189"
if not exist "%PY%" ( echo [ERROR] ComfyUI python not found & pause & exit /b 1 )

if "%~1"=="" ( echo [ERROR] Missing script.json & echo Usage: start_batch.bat script.json [character_dir] & pause & exit /b 1 )

rem check engine (port 8189)
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8189/system_stats' -UseBasicParsing -TimeoutSec 3; if ($r.StatusCode -eq 200) { exit 0 } } catch { exit 1 }"
if %errorlevel%==0 (
  echo        ComfyUI engine running.
) else (
  echo [ERROR] ComfyUI not running! Start it with start_flux2.bat first.
  pause
  exit /b 1
)

echo Starting batch image generation...
"%PY%" pipeline\scripts\batch_gen.py %*
pause
