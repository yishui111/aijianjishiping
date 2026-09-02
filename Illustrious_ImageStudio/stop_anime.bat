@echo off
rem ============================================================
rem  Anime Image System - stop (only this project's processes)
rem  Engine port 8188
rem ============================================================
echo Stopping Anime system...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'ComfyUI\\main.py' -and $_.CommandLine -match '8188' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Output ('Stopped ComfyUI PID ' + $_.ProcessId) }"
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'anime_workbench.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Output ('Stopped workbench PID ' + $_.ProcessId) }"
echo Done.
pause
