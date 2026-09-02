@echo off
rem ============================================================
rem  AI Image Studio - Unified Control Menu (start/stop all)
rem  All projects are in this folder (aijianjishiping)
rem  ----------------------------------------------------------
rem   [1] Anime (Illustrious v1.0 + IPAdapter)   engine 8188
rem   [2] Realistic (FLUX2 Klein Q8_0)           engine 8189
rem   [3] Official-style UI (Gradio, anime)      port 7860
rem   [4] Model pad (minimal dialog+model)       port 8095
rem   [5] Camera studio (JSON->clips->video)     port 8094
rem   [6] AI Video Studio (existing project)     local
rem  ----------------------------------------------------------
rem  NOTE: Only run ONE image engine at a time (VRAM limit)
rem ============================================================
setlocal EnableDelayedExpansion

:menu
cls
echo ================================================================
echo    AI Image Studio - Control Menu
echo ================================================================
echo.
echo   Engine status:
call :check_port 8188 "Anime engine"
call :check_port 8189 "Realistic engine"
call :check_port 7860 "Official UI"
call :check_port 8095 "Model pad"
call :check_port 8094 "Camera studio"
echo.
echo   [1] Start Anime image (engine 8188 + workbench 8093)
echo   [2] Start Realistic image (engine 8189 + workbench 8092)
echo   [3] Start Official-style UI (7860)
echo   [4] Start Model pad (8095)
echo   [5] Start Camera studio (8094)
echo   [6] Start AI Video Studio (no Docker)
echo.
echo   [S] Stop everything
echo   [H] Open help doc
echo   [Q] Quit
echo.
set /p C=  Choose:

if /i "%C%"=="1" goto start_anime
if /i "%C%"=="2" goto start_flux2
if /i "%C%"=="3" goto start_ui
if /i "%C%"=="4" goto start_pad
if /i "%C%"=="5" goto start_camera
if /i "%C%"=="6" goto start_avs
if /i "%C%"=="s" goto stop_all
if /i "%C%"=="h" goto help
if /i "%C%"=="q" exit /b 0
echo   Invalid choice
timeout /t 1 /nobreak >nul
goto menu

:start_anime
echo.
echo   Starting Anime system (engine 8188 + workbench 8093)...
call "%~dp0Illustrious_ImageStudio\start_anime.bat"
goto menu

:start_flux2
echo.
echo   Starting Realistic system (engine 8189 + workbench 8092)...
call "%~dp0FLUX2_ImageStudio\start_flux2.bat"
goto menu

:start_ui
echo.
echo   Starting Official-style UI (7860)...
call "%~dp0illustrious_ui\start_ui.bat"
goto menu

:start_pad
echo.
echo   Starting Model pad (8095)...
call "%~dp0model_pad\start_model_pad.bat"
goto menu

:start_camera
echo.
echo   Starting Camera studio (8094)...
call "%~dp0Camera_Studio\start_camera.bat"
goto menu

:start_avs
echo.
echo   Starting AI Video Studio (no Docker)...
call "%~dp0ai-video-studio\start_local.bat"
goto menu

:stop_all
echo.
echo   Stopping all engines and UIs...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'ComfyUI\\main.py|flux2_workbench.py|anime_workbench.py|camera_workbench.py|model_pad.py|illustrious_ui|app.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Output ('Stopped PID ' + $_.ProcessId) }"
echo   Done
pause
goto menu

:help
echo.
start "" "%~dp0AI_Image_Studio_README.md"
echo   Opened help doc
pause
goto menu

:check_port
netstat -ano | findstr "LISTENING" | findstr ":%1 " >nul 2>&1 && (
  echo    %2: RUNNING
) || (
  echo    %2: stopped
)
exit /b 0
