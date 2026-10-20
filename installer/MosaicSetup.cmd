@echo off
title Installazione di Mosaic 2
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
set RC=%ERRORLEVEL%
echo.
if %RC% NEQ 0 (
    echo Installazione terminata con errori ^(codice %RC%^). Vedi setup.log nella cartella di installazione.
) else (
    echo Installazione completata.
)
echo.
pause
exit /b %RC%
