@echo off
rem Prepara questo PC come stazione centrale di WORK-GUARDIAN (doppio clic).
rem Per riportare la scheda Ethernet all'uso normale: configura_pc.bat ripristina
if /i "%~1"=="ripristina" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0configura_pc.ps1" -Ripristina
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0configura_pc.ps1"
)
