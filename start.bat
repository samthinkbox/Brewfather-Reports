@echo off
rem Start Brewfather Reports, then open http://localhost:8765/admin.html
cd /d "%~dp0"
python app\server.py %*
pause
