@echo off
rem ============================================================
rem  Anime batch image generation
rem  Usage: start_batch_anime.bat <script.json> [character_dir]
rem  Example: start_batch_anime.bat script.json chars
rem  (chars dir contains <char_id>.png, e.g. c1.png c2.png)
rem ============================================================
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%ComfyUI_windows_portable\python_embeded\python.exe"
set "COMFY_URL=http://127.0.0.1:8188"
if not exist "%PY%" ( echo [ERROR] ComfyUI python not found & pause & exit /b 1 )

if "%~1"=="" ( echo [ERROR] Missing script.json & echo Usage: start_batch_anime.bat script.json [character_dir] & pause & exit /b 1 )

powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8188/system_stats' -UseBasicParsing -TimeoutSec 3; if ($r.StatusCode -eq 200) { exit 0 } } catch { exit 1 }"
if %errorlevel%==0 (
  echo        ComfyUI engine running.
) else (
  echo [ERROR] ComfyUI not running! Start it with start_anime.bat first.
  pause
  exit /b 1
)

echo Starting anime batch generation...
"%PY%" pipeline\scripts\batch_gen_anime.py %*
pause
