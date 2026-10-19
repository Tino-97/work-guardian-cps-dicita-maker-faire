@echo off
rem Prepara questo PC come stazione centrale (doppio clic): Python 3.12, librerie,
rem PyTorch per la GPU, Mosquitto, rete dell'orologio e firewall. Si puo' rieseguire.
rem Per riportare la scheda Ethernet all'uso normale: prepara_pc.bat ripristina
if /i "%~1"=="ripristina" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0prepara_pc.ps1" -Ripristina
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0prepara_pc.ps1"
)
