@echo off
rem Avvia Mosaic come app desktop (finestra nativa). Mosaic.exe fa lo stesso.
pushd "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Mosaic non e' ancora installato in questa cartella.
    echo Esegui setup.ps1 ^(clic destro, "Esegui con PowerShell"^) e poi riprova.
    pause
    popd
    exit /b 1
)
set MOSAIC_HOME=%~dp0
start "" ".venv\Scripts\pythonw.exe" -m mosaic.desktop
popd
