@echo off
rem Chiude la missione dell'orologio rimasta attiva (doppio clic).
cd /d "%~dp0.."
py -3.12 demo\main.py --chiudi-missione %*
pause
