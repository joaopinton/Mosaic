<#
.SYNOPSIS
    Installa Mosaic 2: Python (se manca), ambiente virtuale, dipendenze, modelli e programmi portabili.

.DESCRIPTION
    Idempotente: si può rilanciare per riprendere o aggiornare l'installazione.
    - Dentro MosaicSetup.exe: chiede il disco di destinazione, estrae payload.zip e installa.
    - Dalla cartella del progetto: installa sul posto.

.PARAMETER InstallDir  Cartella di installazione (se omessa, la sceglie una finestra di dialogo).
.PARAMETER NoLLM       Non scarica llama.cpp e il modello Qwen2.5-VL (circa 2,8 GB).
.PARAMETER SkipAssets  Salta il download di modelli e programmi portabili.
.PARAMETER Check       Controlla solo i prerequisiti, senza installare nulla.
.PARAMETER Quiet       Nessuna finestra di dialogo né domande (usa la cartella predefinita).
#>
[CmdletBinding()]
param(
    [string]$InstallDir,
    [switch]$NoLLM,
    [switch]$SkipAssets,
    [switch]$Check,
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$PythonVersion = '3.14.4'
$PythonUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-amd64.exe"
$MinPython = [version]'3.12'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Payload = Join-Path $Here 'payload.zip'
$script:LogFile = $null

function Write-Log([string]$Text) {
    if ($script:LogFile) {
        Add-Content -Path $script:LogFile -Value ('{0:HH:mm:ss} {1}' -f (Get-Date), $Text)
    }
}

function Write-Step([string]$Text) {
    Write-Host ''
    Write-Host "==> $Text" -ForegroundColor Cyan
    Write-Log "==> $Text"
}

function Write-Warn([string]$Text) {
    Write-Host "ATTENZIONE: $Text" -ForegroundColor Yellow
    Write-Log "ATTENZIONE: $Text"
}

function Get-FreeGB([string]$Path) {
    try {
        $root = [IO.Path]::GetPathRoot([IO.Path]::GetFullPath($Path))
        return [math]::Round((New-Object IO.DriveInfo($root)).AvailableFreeSpace / 1GB, 1)
    } catch {
        return $null
    }
}

function Select-InstallDir {
    $default = Join-Path ([Environment]::GetFolderPath('UserProfile')) 'Mosaic'
    if ($Quiet) { return $default }
    $picked = $null
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
        $dlg.Description = "Scegli il disco o la cartella in cui installare Mosaic (servono circa 6 GB liberi). Verrà creata la sottocartella 'Mosaic'."
        $dlg.ShowNewFolderButton = $true
        if ($dlg.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) {
            throw 'Installazione annullata.'
        }
        $picked = $dlg.SelectedPath
    } catch [System.IO.FileNotFoundException] {
        $answer = Read-Host "Cartella di installazione [$default]"
        $picked = if ($answer) { $answer } else { $default }
    }
    if ((Split-Path $picked -Leaf) -ieq 'Mosaic') { return $picked }
    return (Join-Path $picked 'Mosaic')
}

function Test-Python([string]$Exe, [string[]]$Pre = @()) {
    if (-not (Test-Path $Exe) -and -not (Get-Command $Exe -ErrorAction SilentlyContinue)) { return $null }
    try {
        $probe = "import sys, venv, ensurepip; print('%d.%d|%d|%s' % (sys.version_info[0], sys.version_info[1], int(sys.maxsize > 2**32), sys.executable))"
        $out = & $Exe @Pre -c $probe 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
        $parts = ("$out").Trim().Split('|', 3)
        if ([version]$parts[0] -lt $MinPython -or $parts[1] -ne '1') { return $null }
        if ($parts[2] -match '\\WindowsApps\\') { return $null }
        return $parts[2]
    } catch {
        return $null
    }
}

function Find-Python([string]$Dir) {
    $candidates = @(
        @{ Exe = (Join-Path $Dir 'python\python.exe'); Pre = @() },
        @{ Exe = 'py'; Pre = @('-3.14') },
        @{ Exe = 'py'; Pre = @('-3.13') },
        @{ Exe = 'py'; Pre = @('-3.12') },
        @{ Exe = 'python'; Pre = @() }
    )
    foreach ($c in $candidates) {
        $found = Test-Python $c.Exe $c.Pre
        if ($found) { return $found }
    }
    return $null
}

function Install-Python([string]$Dir) {
    Write-Step "Python $PythonVersion non trovato: scarico l'installer ufficiale da python.org"
    $installer = Join-Path $env:TEMP "python-$PythonVersion-amd64.exe"
    Invoke-WebRequest -Uri $PythonUrl -OutFile $installer -UseBasicParsing
    $sig = Get-AuthenticodeSignature $installer
    if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
        Remove-Item $installer -Force
        throw "La firma dell'installer di Python non è valida (stato: $($sig.Status))."
    }
    $target = Join-Path $Dir 'python'
    Write-Host "Installo Python in $target (solo per questo utente, senza toccare il PATH)"
    $proc = Start-Process -FilePath $installer -Wait -PassThru -ArgumentList @(
        '/quiet', 'InstallAllUsers=0', 'PrependPath=0', 'Include_launcher=0',
        'Include_test=0', 'Include_doc=0', 'AssociateFiles=0', 'Shortcuts=0',
        "TargetDir=$target"
    )
    Remove-Item $installer -Force -ErrorAction SilentlyContinue
    if ($proc.ExitCode -notin 0, 3010) { throw "L'installazione di Python è fallita (codice $($proc.ExitCode))." }
    $exe = Join-Path $target 'python.exe'
    if (-not (Test-Python $exe)) { throw 'Python installato ma non utilizzabile.' }
    return $exe
}

function Invoke-Logged([string]$Exe, [string[]]$ArgList) {
    Write-Log "$Exe $($ArgList -join ' ')"
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Exe @ArgList 2>&1 | ForEach-Object { $line = "$_"; Write-Host $line; Write-Log $line }
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($code -ne 0) { throw "Comando fallito (codice $code): $Exe $($ArgList -join ' ')" }
}

function New-Shortcut([string]$Path, [string]$Target, [string]$Dir) {
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut($Path)
    $lnk.TargetPath = $Target
    $lnk.WorkingDirectory = $Dir
    $lnk.Description = 'Mosaic - foto e video in locale'
    $ico = Join-Path $Dir 'mosaic.ico'
    if (Test-Path $ico) { $lnk.IconLocation = $ico }
    $lnk.Save()
}

function Write-Launcher([string]$Dir) {
    if (-not (Test-Path (Join-Path $Dir 'mosaic\desktop.py'))) {
        Write-Warn "L'applicazione (mosaic\desktop.py) non è ancora presente: installati ambiente, modelli e programmi, nessun collegamento creato."
        return
    }
    # Mosaic.bat resta come avvio di riserva (stessa app desktop, senza Mosaic.exe)
    Set-Content -Path (Join-Path $Dir 'Mosaic.bat') -Encoding ASCII -Value @(
        '@echo off',
        'set MOSAIC_HOME=%~dp0',
        'start "" "%~dp0.venv\Scripts\pythonw.exe" -m mosaic.desktop'
    )
    $exe = Join-Path $Dir 'Mosaic.exe'
    $target = if (Test-Path $exe) { $exe } else { Join-Path $Dir 'Mosaic.bat' }
    New-Shortcut (Join-Path $Dir 'Mosaic.lnk') $target $Dir
    New-Shortcut (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Mosaic.lnk') $target $Dir
    New-Shortcut (Join-Path ([Environment]::GetFolderPath('Programs')) 'Mosaic.lnk') $target $Dir
    Write-Host 'Collegamenti creati sul Desktop e nel menu Start.'
}

function Test-WebView2 {
    $guid = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    foreach ($key in "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\$guid",
                     "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\$guid",
                     "HKCU:\Software\Microsoft\EdgeUpdate\Clients\$guid") {
        $pv = (Get-ItemProperty -Path $key -Name pv -ErrorAction SilentlyContinue).pv
        if ($pv -and $pv -ne '0.0.0.0') { return $pv }
    }
    return $null
}

function Install-WebView2 {
    # la finestra nativa usa Microsoft Edge WebView2 (già presente su Windows 11); senza, Mosaic ripiega su Edge
    $version = Test-WebView2
    if ($version) { Write-Host "WebView2 presente ($version)"; return }
    Write-Step 'Microsoft Edge WebView2 non trovato: lo installo'
    $setup = Join-Path $env:TEMP 'MicrosoftEdgeWebview2Setup.exe'
    try {
        Invoke-WebRequest -Uri 'https://go.microsoft.com/fwlink/p/?LinkId=2124703' -OutFile $setup -UseBasicParsing
        $sig = Get-AuthenticodeSignature $setup
        if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch 'Microsoft Corporation') {
            throw "firma non valida (stato: $($sig.Status))"
        }
        $proc = Start-Process -FilePath $setup -ArgumentList '/silent', '/install' -Wait -PassThru
        if ($proc.ExitCode -ne 0) { throw "codice $($proc.ExitCode)" }
        Write-Host 'WebView2 installato.'
    } catch {
        Write-Warn "WebView2 non installato ($($_.Exception.Message)): Mosaic si aprirà in una finestra di Edge."
    } finally {
        Remove-Item $setup -Force -ErrorAction SilentlyContinue
    }
}

function Register-Uninstaller([string]$Dir) {
    $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Mosaic'
    $uninstall = Join-Path $Dir 'uninstall.ps1'
    if (-not (Test-Path $uninstall)) { return }
    New-Item -Path $key -Force | Out-Null
    $values = @{
        DisplayName     = 'Mosaic'
        DisplayVersion  = '2.0.0'
        Publisher       = 'Mosaic'
        InstallLocation = $Dir
        DisplayIcon     = (Join-Path $Dir 'mosaic.ico')
        UninstallString = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$uninstall`""
    }
    foreach ($name in $values.Keys) { Set-ItemProperty -Path $key -Name $name -Value $values[$name] }
    Set-ItemProperty -Path $key -Name NoModify -Value 1 -Type DWord
    Set-ItemProperty -Path $key -Name NoRepair -Value 1 -Type DWord
    Write-Host "Disinstallazione disponibile in Impostazioni > App."
}

function Show-Check {
    Write-Step 'Controllo prerequisiti (nessuna modifica)'
    $dir = if ($InstallDir) { $InstallDir } else { $Here }
    $python = Find-Python $dir
    Write-Host ("Python >= {0}      : {1}" -f $MinPython, $(if ($python) { $python } else { "non trovato (verrà installato Python $PythonVersion)" }))
    Write-Host ("payload.zip         : {0}" -f $(if (Test-Path $Payload) { 'presente' } else { 'assente (installazione sul posto)' }))
    Write-Host ("assets.yaml         : {0}" -f $(if ((Test-Path (Join-Path $Here 'assets.yaml')) -or (Test-Path $Payload)) { 'ok' } else { 'MANCANTE' }))
    Write-Host ("Spazio libero       : {0} GB su {1}" -f (Get-FreeGB $dir), [IO.Path]::GetPathRoot([IO.Path]::GetFullPath($dir)))
}

try {
    Write-Host ''
    Write-Host '  Mosaic 2 - installazione completa (nessun Docker, tutto in locale)' -ForegroundColor Green
    Write-Host ''
    if ($Check) { Show-Check; exit 0 }

    $hasPayload = Test-Path $Payload
    if (-not $InstallDir) {
        $InstallDir = if ($hasPayload) { Select-InstallDir } else { $Here }
    }
    $InstallDir = [IO.Path]::GetFullPath($InstallDir)
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    $script:LogFile = Join-Path $InstallDir 'setup.log'
    Write-Log "Installazione in $InstallDir"

    # con -SkipAssets non si scaricano modelli né programmi: bastano l'ambiente Python e le dipendenze
    $needGB = if ($SkipAssets) { 1.5 } elseif ($NoLLM) { 4 } else { 6 }
    $freeGB = Get-FreeGB $InstallDir
    Write-Host "Cartella: $InstallDir  (spazio libero: $freeGB GB, necessari circa $needGB GB)"
    if ($null -ne $freeGB -and $freeGB -lt $needGB) {
        Write-Warn 'Spazio libero insufficiente.'
        if ($Quiet -or (Read-Host 'Continuare comunque? (s/N)') -notmatch '^[sSyY]') { throw 'Spazio su disco insufficiente.' }
    }

    if ($hasPayload) {
        Write-Step 'Copio i file di Mosaic'
        Expand-Archive -LiteralPath $Payload -DestinationPath $InstallDir -Force
    }

    Write-Step 'Python'
    $python = Find-Python $InstallDir
    if (-not $python) { $python = Install-Python $InstallDir }
    Write-Host "Uso $python"

    Write-Step 'Ambiente virtuale e dipendenze'
    $venv = Join-Path $InstallDir '.venv'
    $venvPython = Join-Path $venv 'Scripts\python.exe'
    if (-not (Test-Path $venvPython)) { Invoke-Logged $python @('-m', 'venv', $venv) }
    Invoke-Logged $venvPython @('-m', 'pip', 'install', '--upgrade', 'pip', '--progress-bar', 'off')
    Invoke-Logged $venvPython @('-m', 'pip', 'install', '-r', (Join-Path $InstallDir 'requirements.txt'), '--progress-bar', 'off')

    if (-not $SkipAssets) {
        $env:MOSAIC_HOME = $InstallDir
        Push-Location $InstallDir
        try {
            Write-Step 'Modelli e programmi portabili (SigLIP 2, volti, exiftool, ffmpeg)'
            Invoke-Logged $venvPython @('-m', 'mosaic.assets', 'fetch', '--group', 'core')
            if (-not $NoLLM) {
                Write-Step 'LLM locale (llama.cpp + Qwen2.5-VL-3B, circa 2,8 GB)'
                try {
                    Invoke-Logged $venvPython @('-m', 'mosaic.assets', 'fetch', '--group', 'llm')
                } catch {
                    Write-Warn "Download dell'LLM non completato: rilancia setup.ps1 per riprendere. ($($_.Exception.Message))"
                }
            }
            Write-Step 'Stato finale'
            Invoke-Logged $venvPython @('-m', 'mosaic.assets', 'status')
        } finally {
            Pop-Location
        }
    }

    Write-Step 'Finestra nativa (WebView2)'
    Install-WebView2

    Write-Step 'Collegamenti'
    Write-Launcher $InstallDir
    if ($hasPayload) { Register-Uninstaller $InstallDir }

    Write-Host ''
    Write-Host "Installazione completata in $InstallDir" -ForegroundColor Green
    Write-Host "Log: $script:LogFile"
    $app = Join-Path $InstallDir 'Mosaic.exe'
    if (-not $Quiet -and $hasPayload -and (Test-Path $app)) {
        Write-Host 'Avvio Mosaic...'
        Start-Process -FilePath $app -WorkingDirectory $InstallDir
    }
    exit 0
} catch {
    Write-Host ''
    Write-Host "ERRORE: $($_.Exception.Message)" -ForegroundColor Red
    Write-Log "ERRORE: $($_.Exception.Message)"
    if ($script:LogFile) { Write-Host "Dettagli in: $script:LogFile" }
    exit 1
}
