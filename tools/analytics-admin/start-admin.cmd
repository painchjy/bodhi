@echo off
setlocal
cd /d "%~dp0..\.."
start "analytics-admin" /min python tools\analytics-admin\server.py --host 127.0.0.1 --port 8781
timeout /t 2 >nul
start "" http://127.0.0.1:8781/
endlocal
