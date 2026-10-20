<#
.SYNOPSIS
    Disinstalla Mosaic: chiude l'app, rimuove collegamenti, voce in "App" e la cartella di installazione.
    Le foto originali (nelle loro cartelle) non vengono mai toccate.
#>
$ErrorActionPreference = 'Stop'
$Dir = Split-Path -Parent $MyInvocation.MyCommand.Path

Add-Type -AssemblyName System.Windows.Forms
$answer = [System.Windows.Forms.MessageBox]::Show(
    "Disinstallare Mosaic da`n$Dir ?`n`nVerranno rimossi programma, modelli e catalogo (database, miniature).`nLe tue foto e i tuoi video NON vengono toccati.",
    'Disinstalla Mosaic', 'YesNo', 'Warning')
if ($answer -ne 'Yes') { exit 0 }

# chiude Mosaic e i programmi portabili avviati dalla cartella (llama-server, ffmpeg, exiftool)
Get-CimInstance Win32_Process | Where-Object {
    ($_.ExecutablePath -and $_.ExecutablePath.StartsWith($Dir, [StringComparison]::OrdinalIgnoreCase)) -or
    ($_.CommandLine -and $_.CommandLine -match 'mosaic\.(desktop|app)' -and $_.CommandLine -like "*$Dir*")
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2

foreach ($lnk in (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Mosaic.lnk'),
                 (Join-Path ([Environment]::GetFolderPath('Programs')) 'Mosaic.lnk')) {
    Remove-Item $lnk -Force -ErrorAction SilentlyContinue
}
Remove-Item 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Mosaic' -Recurse -Force -ErrorAction SilentlyContinue

Set-Location $env:TEMP
try {
    Remove-Item $Dir -Recurse -Force
    [System.Windows.Forms.MessageBox]::Show('Mosaic è stato disinstallato.', 'Disinstalla Mosaic') | Out-Null
} catch {
    [System.Windows.Forms.MessageBox]::Show("Alcuni file non sono stati rimossi (forse in uso):`n$Dir`n`nPuoi eliminare la cartella a mano.",
        'Disinstalla Mosaic', 'OK', 'Warning') | Out-Null
}
