@echo off
rem stop Illustrious official-style UI
echo Stopping Illustrious UI...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'illustrious_ui|app.py' -and $_.CommandLine -match 'illustrious_ui' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Output ('Stopped PID ' + $_.ProcessId) }"
echo Done.
pause
