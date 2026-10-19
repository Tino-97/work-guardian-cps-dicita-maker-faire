@echo off
rem Avvia la demo con Python 3.12 (doppio clic). Accetta le stesse opzioni di main.py,
rem per esempio: avvia_demo.bat --no-drone
cd /d "%~dp0.."
py -3.12 demo\main.py %*
if errorlevel 1 pause
