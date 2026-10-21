@echo off
title Installazione di Mosaic 2
rem IExpress parte a 32 bit: su Windows a 64 bit si usa il PowerShell a 64 bit (Sysnative) per vedere registro e DLL giusti
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if exist "%SystemRoot%\Sysnative\WindowsPowerShell\v1.0\powershell.exe" set "PS=%SystemRoot%\Sysnative\WindowsPowerShell\v1.0\powershell.exe"
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
set RC=%ERRORLEVEL%
echo.
if %RC% NEQ 0 (
    echo Installazione terminata con errori ^(codice %RC%^). Vedi setup.log nella cartella di installazione:
    echo una copia e' sul Desktop come mosaic-setup-log.txt.
) else (
    echo Installazione completata.
)
echo.
pause
exit /b %RC%
