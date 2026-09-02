@echo off
rem ============================================================
rem  aijianjishiping - Stop everything
rem  Kills only this project's python processes (by cmdline)
rem ============================================================
echo Stopping all engines and workbenches...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'ComfyUI\\main.py|flux2_workbench.py|anime_workbench.py|camera_workbench.py|model_pad.py|illustrious_ui|app.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Output ('Stopped PID ' + $_.ProcessId) }"
echo Done.
pause
