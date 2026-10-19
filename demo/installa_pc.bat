@echo off
rem Installa su questo PC Python 3.12, le librerie del progetto, PyTorch per la GPU
rem e Mosquitto, poi prepara la rete (doppio clic). Si puo' rieseguire senza danni.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installa_pc.ps1"
