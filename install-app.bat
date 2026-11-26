@echo off
rem Add Brewfather Reports to the Start Menu and desktop, and start the server when you sign in.
rem To remove the shortcuts: python app\desktop.py --uninstall
cd /d "%~dp0"
python -I app\desktop.py --install --autostart %*
pause
