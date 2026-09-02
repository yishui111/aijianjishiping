@echo off
rem stop camera workbench
echo Stopping camera workbench...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'camera_workbench.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Output ('Stopped PID ' + $_.ProcessId) }"
echo Done.
pause
