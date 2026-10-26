<#
.SYNOPSIS
    Installa Mosaic 2: Python (se manca), ambiente virtuale, dipendenze, modelli e programmi portabili.

.DESCRIPTION
    Idempotente: si può rilanciare per riprendere o aggiornare l'installazione.
    - Dentro MosaicSetup.exe: chiede il disco di destinazione, estrae payload.zip e installa.
    - Dalla cartella del progetto: installa sul posto.
    Si adatta al PC: controlla Windows, spazio, RAM e rete; usa un Python già presente solo se è di una versione
    che le dipendenze supportano (3.12-3.14) e, se non funziona, ne installa uno suo dentro la cartella di Mosaic.

.PARAMETER InstallDir  Cartella di installazione (se omessa, la sceglie una finestra di dialogo).
.PARAMETER PythonPath  Python da usare al posto di quello trovato da sé (percorso di python.exe, versione 3.12-3.14).
.PARAMETER NoLLM       Non scarica llama.cpp e il modello Qwen2.5-VL (circa 2,8 GB).
.PARAMETER SkipAssets  Salta il download di modelli e programmi portabili.
.PARAMETER Check       Controlla solo i prerequisiti, senza installare nulla.
.PARAMETER Quiet       Nessuna finestra di dialogo né domande (usa la cartella predefinita).
#>
[CmdletBinding()]
param(
    [string]$InstallDir,
    [string]$PythonPath,
    [switch]$NoLLM,
    [switch]$SkipAssets,
    [switch]$Check,
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]'Tls12, Tls13'
} catch {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
}

$PythonVersion = '3.14.4'
$PythonUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-amd64.exe"
$MinPython = [version]'3.12'
$MaxPython = [version]'3.14'   # numpy, onnxruntime, rawpy e le altre dipendenze native hanno le ruote solo fino alla 3.14
$VCRedistUrl = 'https://aka.ms/vs/17/release/vc_redist.x64.exe'
$LLMMinRamGB = 6
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Payload = Join-Path $Here 'payload.zip'
$script:LogFile = $null
$script:SkipLLM = [bool]$NoLLM

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

function Write-Info([string]$Text) {
    Write-Host $Text
    Write-Log $Text
}

function Get-FreeGB([string]$Path) {
    try {
        $root = [IO.Path]::GetPathRoot([IO.Path]::GetFullPath($Path))
        return [math]::Round((New-Object IO.DriveInfo($root)).AvailableFreeSpace / 1GB, 1)
    } catch {
        return $null
    }
}

# --- il PC ----------------------------------------------------------------------------------------------

function Get-SystemDir {
    # un PowerShell a 32 bit su Windows a 64 bit vedrebbe in System32 le DLL a 32 bit
    if ([Environment]::Is64BitOperatingSystem -and -not [Environment]::Is64BitProcess) { return (Join-Path $env:SystemRoot 'Sysnative') }
    return (Join-Path $env:SystemRoot 'System32')
}

function Test-VCRuntime {
    $sys = Get-SystemDir
    return (Test-Path (Join-Path $sys 'vcruntime140_1.dll')) -and (Test-Path (Join-Path $sys 'msvcp140.dll'))
}

function Set-Proxy {
    # Invoke-WebRequest usa già il proxy di sistema; pip e gli script Python no: gli si passa lo stesso
    try {
        [Net.WebRequest]::DefaultWebProxy.Credentials = [Net.CredentialCache]::DefaultNetworkCredentials
        $uri = [Uri]'https://pypi.org'
        $proxy = [Net.WebRequest]::GetSystemWebProxy().GetProxy($uri)
        if ($proxy -and $proxy.Host -ne $uri.Host -and -not $env:HTTPS_PROXY) {
            $env:HTTPS_PROXY = $proxy.AbsoluteUri
            $env:HTTP_PROXY = $proxy.AbsoluteUri
            Write-Log "Proxy di sistema: $($proxy.AbsoluteUri)"
        }
    } catch {
        Write-Log "Proxy non letto: $($_.Exception.Message)"
    }
}

function Test-Host([string]$Name) {
    try {
        Invoke-WebRequest -Uri "https://$Name/" -Method Head -UseBasicParsing -TimeoutSec 6 | Out-Null
        return $true
    } catch {
        return $null -ne $_.Exception.Response  # il sito ha risposto (anche con un errore): la rete c'è
    }
}

function Get-Machine([string]$Dir) {
    $os = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
    $ram = (Get-CimInstance Win32_ComputerSystem -ErrorAction SilentlyContinue).TotalPhysicalMemory
    $arch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    return [pscustomobject]@{
        Windows = if ($os) { "$($os.Caption) (build $($os.BuildNumber))" } else { [Environment]::OSVersion.VersionString }
        Build   = [Environment]::OSVersion.Version.Build
        Is64    = [Environment]::Is64BitOperatingSystem
        Arch    = $arch
        RamGB   = if ($ram) { [math]::Round($ram / 1GB, 1) } else { $null }
        FreeGB  = Get-FreeGB $Dir
        PS      = $PSVersionTable.PSVersion.ToString()
        VCRuntime = Test-VCRuntime
    }
}

# Controlli che non dipendono da Python. Ritorna i problemi che bloccano e gli avvisi (che si risolvono da sé o si segnalano).
function Test-Machine([string]$Dir, [bool]$NeedNetwork = $true) {
    $m = Get-Machine $Dir
    $problems = @()
    $warnings = @()
    Write-Info ("Windows             : {0}, {1}" -f $m.Windows, $m.Arch)
    Write-Info ("PowerShell          : {0}" -f $m.PS)
    Write-Info ("RAM                 : {0} GB" -f $m.RamGB)
    Write-Info ("Spazio libero       : {0} GB" -f $m.FreeGB)
    if (-not $m.Is64 -or $m.Arch -eq 'x86') { $problems += 'Mosaic richiede Windows a 64 bit.' }
    if ($m.Build -lt 10240) { $problems += 'Mosaic richiede Windows 10 o Windows 11 (Python 3.12+ non funziona su versioni precedenti).' }
    if ($m.Arch -eq 'ARM64') { $warnings += 'PC con processore ARM: uso Python x64 in emulazione (più lento, ma funziona).' }
    if ($null -ne $m.RamGB -and $m.RamGB -lt $LLMMinRamGB -and -not $script:SkipLLM) {
        $script:SkipLLM = $true
        $warnings += "Solo $($m.RamGB) GB di RAM: salto il modello LLM locale (serve a scrivere le descrizioni delle foto e sotto $LLMMinRamGB GB sarebbe troppo lento). Ricerca, volti e album funzionano lo stesso."
    }
    if ($Dir -match '(?i)\\OneDrive\b') { $warnings += 'La cartella è sincronizzata con OneDrive: con migliaia di file e il database può rallentare o dare errori. Meglio una cartella fuori da OneDrive.' }
    if ($Dir.Length -gt 90) { $warnings += 'Il percorso di installazione è molto lungo: scegli una cartella più corta se compaiono errori sui nomi dei file.' }
    if ($NeedNetwork) {
        Set-Proxy
        $offline = -not (Test-Host 'pypi.org') -or -not (Test-Host 'files.pythonhosted.org')
        if ($offline) {
            $problems += 'Non riesco a raggiungere pypi.org: serve Internet (o il proxy dell''azienda) per scaricare le dipendenze. Controlla la connessione, il proxy o l''antivirus e riprova.'
        } else {
            foreach ($site in 'github.com', 'huggingface.co') {
                if (-not (Test-Host $site)) { $warnings += "$site non raggiungibile: il download dei modelli potrebbe non riuscire (rete filtrata?)." }
            }
        }
    }
    if (-not $m.VCRuntime) { $warnings += 'Mancano i componenti Microsoft Visual C++ 2015-2022: li installo da solo (Windows può chiedere il permesso).' }
    foreach ($w in $warnings) { Write-Warn $w }
    foreach ($p in $problems) { Write-Host "ERRORE: $p" -ForegroundColor Red; Write-Log "ERRORE: $p" }
    return [pscustomobject]@{ Problems = $problems; Warnings = $warnings; Machine = $m }
}

# --- download -------------------------------------------------------------------------------------------

function Get-File([string]$Url, [string]$Out, [int]$Tries = 3) {
    for ($i = 1; $i -le $Tries; $i++) {
        try {
            Invoke-WebRequest -Uri $Url -OutFile $Out -UseBasicParsing -TimeoutSec 900
            if ((Test-Path $Out) -and (Get-Item $Out).Length -gt 0) { return }
        } catch {
            Write-Log "Download $i/$Tries di ${Url}: $($_.Exception.Message)"
            Start-Sleep -Seconds (2 * $i)
        }
    }
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue  # incluso in Windows 10 dalla 1803
    if ($curl) {
        & $curl.Source -L --fail --retry 3 --silent --show-error -o $Out $Url
        if ($LASTEXITCODE -eq 0 -and (Test-Path $Out) -and (Get-Item $Out).Length -gt 0) { return }
    }
    throw "Download non riuscito: $Url (controlla la connessione, il proxy o l'antivirus)."
}

function Assert-Signature([string]$Path, [string]$Signer) {
    $sig = Get-AuthenticodeSignature $Path
    if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch $Signer) {
        Remove-Item $Path -Force -ErrorAction SilentlyContinue
        throw "La firma digitale di $(Split-Path $Path -Leaf) non è valida (stato: $($sig.Status))."
    }
}

function Install-VCRuntime {
    Write-Step 'Componenti Microsoft Visual C++ (servono a numpy, OpenCV e onnxruntime)'
    $setup = Join-Path $env:TEMP 'vc_redist.x64.exe'
    try {
        Get-File $VCRedistUrl $setup
        Assert-Signature $setup 'Microsoft Corporation'
        $proc = Start-Process -FilePath $setup -ArgumentList '/install', '/quiet', '/norestart' -Verb RunAs -Wait -PassThru
        if ($proc.ExitCode -notin 0, 1638, 3010) { throw "codice $($proc.ExitCode)" }
        Write-Info 'Componenti Visual C++ installati.'
    } catch {
        Write-Warn "Visual C++ non installato ($($_.Exception.Message)). Se Mosaic non parte, installa 'Microsoft Visual C++ Redistributable x64' da https://aka.ms/vs/17/release/vc_redist.x64.exe"
    } finally {
        Remove-Item $setup -Force -ErrorAction SilentlyContinue
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

# --- Python ---------------------------------------------------------------------------------------------

# Prova un interprete: ritorna @{Ok; Path; Version; Why}. Accetta solo 64 bit x64, 3.12-3.14, con venv e ensurepip.
function Test-Python([string]$Exe, [string[]]$Pre = @()) {
    $none = [pscustomobject]@{ Ok = $false; Path = $null; Version = $null; Why = 'non trovato' }
    if (-not (Test-Path $Exe) -and -not (Get-Command $Exe -ErrorAction SilentlyContinue)) { return $none }
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $probe = "import sys, platform, importlib.util as u; print('%d.%d|%d|%s|%d|%s' % (sys.version_info[0], sys.version_info[1], int(sys.maxsize > 2**32), platform.machine(), int(bool(u.find_spec('venv') and u.find_spec('ensurepip'))), sys.executable))"
        $out = & $Exe @Pre -c $probe 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $out) { return $none }
        $p = ("$out").Trim().Split('|', 5)
        $version = [version]$p[0]
        $why = $null
        if ($p[4] -match '\\WindowsApps\\') { $why = 'è la versione del Microsoft Store (non adatta a creare ambienti virtuali)' }
        elseif ($p[1] -ne '1') { $why = 'è a 32 bit' }
        elseif ($p[2] -notin 'AMD64', 'x86_64') { $why = "ha architettura $($p[2]), servono le ruote x64" }
        elseif ($p[3] -ne '1') { $why = 'manca venv/ensurepip (installazione incompleta)' }
        elseif ($version -lt $MinPython) { $why = "è troppo vecchio (servono Python $MinPython-$MaxPython)" }
        elseif ($version -gt $MaxPython) { $why = "è troppo nuovo: le dipendenze native non hanno ancora le ruote (servono Python $MinPython-$MaxPython)" }
        return [pscustomobject]@{ Ok = (-not $why); Path = $p[4]; Version = $p[0]; Why = $why }
    } catch {
        return $none
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Get-PythonCandidates([string]$Dir) {
    $list = @()
    if ($PythonPath) { $list += , @{ Exe = $PythonPath; Pre = @() } }
    $list += , @{ Exe = (Join-Path $Dir 'python\python.exe'); Pre = @() }
    foreach ($v in '3.14', '3.13', '3.12') { $list += , @{ Exe = 'py'; Pre = @("-$v") } }
    foreach ($root in 'HKCU:\Software\Python\PythonCore', 'HKLM:\SOFTWARE\Python\PythonCore', 'HKLM:\SOFTWARE\WOW6432Node\Python\PythonCore') {
        foreach ($v in '3.14', '3.13', '3.12') {
            $key = "$root\$v\InstallPath"
            $exe = (Get-ItemProperty -Path $key -ErrorAction SilentlyContinue).ExecutablePath
            if (-not $exe) {
                $base = (Get-ItemProperty -Path $key -ErrorAction SilentlyContinue).'(default)'
                if ($base) { $exe = Join-Path $base 'python.exe' }
            }
            if ($exe) { $list += , @{ Exe = $exe; Pre = @() } }
        }
    }
    foreach ($v in '314', '313', '312') {
        $list += , @{ Exe = (Join-Path $env:LOCALAPPDATA "Programs\Python\Python$v\python.exe"); Pre = @() }
    }
    $list += , @{ Exe = 'python'; Pre = @() }
    $list += , @{ Exe = 'python3'; Pre = @() }
    return $list
}

# Tutti i Python adatti (senza doppioni, nell'ordine di preferenza); i rifiutati finiscono nel log con il motivo.
function Find-Pythons([string]$Dir) {
    $seen = @{}
    $good = @()
    foreach ($c in (Get-PythonCandidates $Dir)) {
        $r = Test-Python $c.Exe $c.Pre
        if (-not $r.Path) { continue }
        $key = $r.Path.ToLowerInvariant()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        if ($r.Ok) {
            $good += $r
        } else {
            Write-Log "Python $($r.Version) in $($r.Path) scartato: $($r.Why)"
            if ($script:ShowRejected) { Write-Info "  scartato Python $($r.Version) ($($r.Path)): $($r.Why)" }
        }
    }
    return $good
}

function Install-PythonWithWinget {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) { return }
    Write-Info 'Provo con winget...'
    & $winget.Source install -e --id Python.Python.3.14 --scope user --silent --accept-package-agreements --accept-source-agreements 2>&1 |
        ForEach-Object { Write-Log "$_" }
}

function Install-Python([string]$Dir) {
    Write-Step "Installo Python $PythonVersion (ufficiale, da python.org) dentro la cartella di Mosaic"
    $target = Join-Path $Dir 'python'
    $installer = Join-Path $env:TEMP "python-$PythonVersion-amd64.exe"
    $pyLog = Join-Path $Dir 'python-install.log'
    try {
        Get-File $PythonUrl $installer
        Assert-Signature $installer 'Python Software Foundation'
        Write-Info "Installo Python in $target (solo per questo utente, senza toccare il PATH)"
        $proc = Start-Process -FilePath $installer -Wait -PassThru -ArgumentList @(
            '/quiet', '/log', "`"$pyLog`"", 'InstallAllUsers=0', 'PrependPath=0', 'Include_launcher=0',
            'Include_test=0', 'Include_doc=0', 'AssociateFiles=0', 'Shortcuts=0', "TargetDir=$target"
        )
        if ($proc.ExitCode -notin 0, 3010) { throw "codice $($proc.ExitCode) (dettagli in $pyLog)" }
        $r = Test-Python (Join-Path $target 'python.exe')
        if (-not $r.Ok) { throw "Python installato ma non utilizzabile ($($r.Why))" }
        return $r.Path
    } catch {
        Write-Warn "Installazione di Python da python.org non riuscita: $($_.Exception.Message)"
        Install-PythonWithWinget
        $found = @(Find-Pythons $Dir)
        if ($found.Count) { return $found[0].Path }
        throw ("Non riesco a installare Python. Installa a mano Python $MinPython-$MaxPython a 64 bit da https://www.python.org/downloads/ " +
               "(non quello del Microsoft Store), poi rilancia l'installazione con:`n  setup.ps1 -PythonPath <percorso di python.exe>")
    } finally {
        Remove-Item $installer -Force -ErrorAction SilentlyContinue
    }
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

# Esegue un comando e ritorna il suo output (senza scriverlo a video); $null se fallisce.
function Get-Output([string]$Exe, [string[]]$ArgList) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & $Exe @ArgList 2>&1 | ForEach-Object { "$_" }
        if ($LASTEXITCODE -ne 0) { Write-Log ($out -join "`n"); return $null }
        return ($out -join "`n")
    } finally {
        $ErrorActionPreference = $previous
    }
}

# L'ambiente virtuale esistente va bene se il suo Python parte e ha una versione supportata.
function Test-Venv([string]$Venv) {
    $exe = Join-Path $Venv 'Scripts\python.exe'
    if (-not (Test-Path $exe)) { return $false }
    $r = Test-Python $exe
    return $r.Ok
}

$CoreImports = 'import numpy, scipy, PIL, cv2, onnxruntime, rawpy, yaml, fastapi, uvicorn, httpx, watchdog'

function New-Environment([string]$Python, [string]$Venv, [string]$Requirements) {
    $venvPython = Join-Path $Venv 'Scripts\python.exe'
    if ((Test-Path $Venv) -and -not (Test-Venv $Venv)) {
        Write-Warn 'L''ambiente virtuale esistente non è utilizzabile (Python spostato, disinstallato o di un''altra versione): lo ricreo.'
        Remove-Item -Recurse -Force $Venv
    }
    if (-not (Test-Path $venvPython)) { Invoke-Logged $Python @('-m', 'venv', $Venv) }
    $pipArgs = @('--progress-bar', 'off', '--disable-pip-version-check', '--retries', '5', '--timeout', '60')
    Invoke-Logged $venvPython (@('-m', 'pip', 'install', '--upgrade', 'pip') + $pipArgs)
    try {
        Invoke-Logged $venvPython (@('-m', 'pip', 'install', '--prefer-binary', '-r', $Requirements) + $pipArgs)
    } catch {
        Write-Warn "Installazione delle dipendenze non riuscita, riprovo ($($_.Exception.Message))"
        Start-Sleep -Seconds 3
        Invoke-Logged $venvPython (@('-m', 'pip', 'install', '--prefer-binary', '-r', $Requirements) + $pipArgs)
    }
    # le librerie native non si caricano senza i componenti Visual C++: si installano e si riprova
    $loaded = Get-Output $venvPython @('-c', $CoreImports)
    if ($null -eq $loaded -and -not (Test-VCRuntime)) {
        Install-VCRuntime
        $loaded = Get-Output $venvPython @('-c', $CoreImports)
    }
    if ($null -eq $loaded) { throw 'Le librerie sono installate ma non si caricano (vedi setup.log).' }
    return $venvPython
}

function Initialize-Environment([string]$Dir, [string]$Venv, [string]$Requirements) {
    $tried = @()
    foreach ($candidate in (Find-Pythons $Dir)) {
        Write-Info "Provo con Python $($candidate.Version): $($candidate.Path)"
        try {
            return (New-Environment $candidate.Path $Venv $Requirements)
        } catch {
            Write-Warn "Con questo Python non ha funzionato: $($_.Exception.Message)"
            $tried += $candidate.Path
            if (Test-Path $Venv) { Remove-Item -Recurse -Force $Venv -ErrorAction SilentlyContinue }
        }
    }
    $private = Join-Path $Dir 'python\python.exe'
    if ($tried -contains $private -and (Test-Path $private)) {
        throw 'Anche con il Python di Mosaic l''installazione delle dipendenze è fallita (vedi setup.log).'
    }
    $python = Install-Python $Dir
    Write-Info "Uso $python"
    return (New-Environment $python $Venv $Requirements)
}

# --- collegamenti, WebView2, disinstallazione ---------------------------------------------------------

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
        Get-File 'https://go.microsoft.com/fwlink/p/?LinkId=2124703' $setup
        Assert-Signature $setup 'Microsoft Corporation'
        $proc = Start-Process -FilePath $setup -ArgumentList '/silent', '/install' -Wait -PassThru
        if ($proc.ExitCode -ne 0) { throw "codice $($proc.ExitCode)" }
        Write-Host 'WebView2 installato.'
    } catch {
        Write-Warn "WebView2 non installato ($($_.Exception.Message)): Mosaic si aprirà in una finestra di Edge."
    } finally {
        Remove-Item $setup -Force -ErrorAction SilentlyContinue
    }
}

function Get-AppVersion([string]$Dir) {
    try {
        $m = Select-String -Path (Join-Path $Dir 'pyproject.toml') -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
        if ($m) { return $m.Matches[0].Groups[1].Value }
    } catch {
        Write-Log "Versione non letta: $($_.Exception.Message)"
    }
    return '2'
}

function Register-Uninstaller([string]$Dir) {
    $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Mosaic'
    $uninstall = Join-Path $Dir 'uninstall.ps1'
    if (-not (Test-Path $uninstall)) { return }
    New-Item -Path $key -Force | Out-Null
    $values = @{
        DisplayName     = 'Mosaic'
        DisplayVersion  = (Get-AppVersion $Dir)
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

# --- aggiornamento di un'installazione esistente -----------------------------------------------------------

# Chi ha già Mosaic installato (con le foto già categorizzate) lancia lo stesso MosaicSetup.exe: si riconosce la cartella,
# si ferma l'app, si sostituisce il codice e basta. Dati (data\, config.yaml), modelli, programmi e ambiente Python restano.
$UninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Mosaic'
$KeepOnUpdate = @('data', 'models', 'runtime', '.venv', 'python', 'config.yaml', 'setup.log')   # mai toccati (models.yaml e labels_it.yaml sì)
$MinUpdateGB = 0.5

function Test-ExistingInstall([string]$Dir) {
    if (-not $Dir -or -not (Test-Path -LiteralPath $Dir)) { return $false }
    return (Test-Path (Join-Path $Dir 'mosaic\desktop.py')) -and ((Test-Path (Join-Path $Dir '.venv\Scripts\python.exe')) -or
            (Test-Path (Join-Path $Dir 'config.yaml')) -or (Test-Path (Join-Path $Dir 'data\mosaic.db')))
}

# La cartella dove Mosaic è già installato (dal registro, o la cartella di setup stessa), oppure $null.
function Get-ExistingInstall {
    try {
        $loc = (Get-ItemProperty -Path $UninstallKey -Name InstallLocation -ErrorAction SilentlyContinue).InstallLocation
        if ($loc -and (Test-ExistingInstall $loc)) { return [IO.Path]::GetFullPath($loc) }
    } catch {
        Write-Log "Registro non letto: $($_.Exception.Message)"
    }
    $default = Join-Path ([Environment]::GetFolderPath('UserProfile')) 'Mosaic'
    if (Test-ExistingInstall $default) { return $default }
    return $null
}

function Get-InstalledVersion([string]$Dir) {
    $file = Join-Path $Dir 'installed-version.txt'
    if (Test-Path $file) { return ((Get-Content -LiteralPath $file -TotalCount 1) | Out-String).Trim() }
    return $null
}

# Chiude Mosaic e i programmi che lancia (llama-server, exiftool, ffmpeg) se partono da questa cartella: con l'app aperta
# i file sono bloccati e il vecchio codice resterebbe in memoria.
function Stop-Mosaic([string]$Dir) {
    $prefix = ([IO.Path]::GetFullPath($Dir)).TrimEnd('\') + '\'
    $running = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.ExecutablePath -and $_.ExecutablePath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -and $_.ProcessId -ne $PID })
    if (-not $running.Count) { return 0 }
    Write-Info "Chiudo Mosaic e i suoi programmi ($($running.Count) processi)..."
    foreach ($p in $running) {
        try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch { Write-Log "Processo $($p.ProcessId) non chiuso: $($_.Exception.Message)" }
    }
    for ($i = 0; $i -lt 20; $i++) {
        $left = @($running | Where-Object { Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue })
        if (-not $left.Count) { break }
        Start-Sleep -Milliseconds 500
    }
    return $running.Count
}

# Toglie il codice vecchio (e gli asset dell'interfaccia con l'hash nel nome) prima di estrarre il nuovo.
function Remove-OldApp([string]$Dir) {
    foreach ($rel in 'mosaic', 'frontend\dist') {
        $path = Join-Path $Dir $rel
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction Stop }
    }
}

function Get-RequirementsHash([string]$File) {
    $text = (Get-Content -LiteralPath $File -Raw) -replace "`r`n", "`n"
    $sha = [Security.Cryptography.SHA256]::Create()
    return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($text))) -replace '-', '').ToLower()
}

# L'ambiente Python va bene com'è se le dipendenze richieste non sono cambiate e le librerie si caricano.
function Test-EnvironmentCurrent([string]$Venv, [string]$Requirements) {
    $marker = Join-Path $Venv 'requirements.sha256'
    if (-not (Test-Path $marker) -or -not (Test-Venv $Venv)) { return $false }
    if ((Get-Content -LiteralPath $marker -TotalCount 1).Trim() -ne (Get-RequirementsHash $Requirements)) { return $false }
    return $null -ne (Get-Output (Join-Path $Venv 'Scripts\python.exe') @('-c', $CoreImports))
}

function Save-RequirementsHash([string]$Venv, [string]$Requirements) {
    Set-Content -LiteralPath (Join-Path $Venv 'requirements.sha256') -Value (Get-RequirementsHash $Requirements) -Encoding ASCII
}

function Show-Check {
    Write-Step 'Controllo prerequisiti (nessuna modifica)'
    $dir = if ($InstallDir) { $InstallDir } else { $Here }
    Test-Machine $dir | Out-Null
    $script:ShowRejected = $true
    $found = @(Find-Pythons $dir)
    if ($found) {
        Write-Info ("Python {0}-{1}  : {2} (versione {3})" -f $MinPython, $MaxPython, $found[0].Path, $found[0].Version)
    } else {
        Write-Info ("Python {0}-{1}  : nessuno adatto (verrà installato Python {2} dentro la cartella di Mosaic)" -f $MinPython, $MaxPython, $PythonVersion)
    }
    Write-Info ("payload.zip         : {0}" -f $(if (Test-Path $Payload) { 'presente' } else { 'assente (installazione sul posto)' }))
    Write-Info ("assets.yaml         : {0}" -f $(if ((Test-Path (Join-Path $Here 'assets.yaml')) -or (Test-Path $Payload)) { 'ok' } else { 'MANCANTE' }))
}

if ($MyInvocation.InvocationName -eq '.') { return }  # caricato per i test: solo le funzioni

try {
    Write-Host ''
    Write-Host '  Mosaic 2 - installazione o aggiornamento (nessun Docker, tutto in locale)' -ForegroundColor Green
    Write-Host ''
    if ($Check) { Show-Check; exit 0 }

    $hasPayload = Test-Path $Payload
    $isUpdate = $false
    if (-not $InstallDir) {
        $existing = if ($hasPayload) { Get-ExistingInstall } else { $null }
        if ($existing) {
            $InstallDir = $existing
            $isUpdate = $true
        } else {
            $InstallDir = if ($hasPayload) { Select-InstallDir } else { $Here }
        }
    } elseif ($hasPayload -and (Test-ExistingInstall $InstallDir)) {
        $isUpdate = $true
    }
    $InstallDir = [IO.Path]::GetFullPath($InstallDir)
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    $script:LogFile = Join-Path $InstallDir 'setup.log'
    Write-Log "Installazione in $InstallDir (aggiornamento: $isUpdate)"
    $summary = New-Object System.Collections.Generic.List[string]
    $oldVersion = if ($isUpdate) { Get-InstalledVersion $InstallDir } else { $null }
    if ($isUpdate) {
        if (-not $oldVersion) { $oldVersion = (Get-ItemProperty -Path $UninstallKey -Name DisplayVersion -ErrorAction SilentlyContinue).DisplayVersion }
        Write-Host "Mosaic è già installato in $InstallDir$(if ($oldVersion) { " (versione $oldVersion)" }): aggiorno il programma e tengo foto, catalogo, modelli e impostazioni." -ForegroundColor Green
    }

    Write-Step 'Controllo del PC'
    $machine = Test-Machine $InstallDir (-not $isUpdate)   # un aggiornamento senza dipendenze nuove funziona anche senza Internet
    if ($machine.Problems.Count) { throw ($machine.Problems -join ' ') }

    # con -SkipAssets non si scaricano modelli né programmi: bastano l'ambiente Python e le dipendenze
    $freeGB = $machine.Machine.FreeGB
    if (-not $SkipAssets -and -not $script:SkipLLM -and $null -ne $freeGB -and $freeGB -lt 6 -and $freeGB -ge 4) {
        $script:SkipLLM = $true
        Write-Warn "Spazio libero $freeGB GB: salto il modello LLM locale (2,8 GB). Per aggiungerlo dopo, libera spazio e rilancia l'installazione."
    }
    $needGB = if ($isUpdate) { $MinUpdateGB } elseif ($SkipAssets) { 1.5 } elseif ($script:SkipLLM) { 4 } else { 6 }
    Write-Host "Cartella: $InstallDir  (spazio libero: $freeGB GB, necessari circa $needGB GB)"
    if ($null -ne $freeGB -and $freeGB -lt $needGB) {
        Write-Warn 'Spazio libero insufficiente.'
        if ($Quiet -or (Read-Host 'Continuare comunque? (s/N)') -notmatch '^[sSyY]') { throw 'Spazio su disco insufficiente.' }
    }

    if ($isUpdate) {
        Write-Step 'Chiudo Mosaic se è aperto'
        $closed = Stop-Mosaic $InstallDir
        if ($closed) { $summary.Add("chiuso Mosaic ($closed processi)") }
        Write-Step 'Tolgo il codice della versione precedente'
        Remove-OldApp $InstallDir
    }

    if ($hasPayload) {
        Write-Step 'Copio i file di Mosaic'
        Expand-Archive -LiteralPath $Payload -DestinationPath $InstallDir -Force
    }

    if (-not $isUpdate -and -not (Test-VCRuntime)) { Install-VCRuntime }

    Write-Step 'Python, ambiente virtuale e dipendenze'
    $venv = Join-Path $InstallDir '.venv'
    $requirements = Join-Path $InstallDir 'requirements.txt'
    if ($isUpdate -and (Test-EnvironmentCurrent $venv $requirements)) {
        $venvPython = Join-Path $venv 'Scripts\python.exe'
        Write-Host 'Dipendenze invariate: tengo l''ambiente Python com''è (nessun download).'
        $summary.Add('ambiente Python invariato')
    } else {
        $venvPython = Initialize-Environment $InstallDir $venv $requirements
        Save-RequirementsHash $venv $requirements
        $summary.Add('dipendenze Python aggiornate')
    }
    Write-Host "Ambiente pronto: $venvPython"

    if ($isUpdate -and -not $SkipAssets) {
        # modelli e programmi già presenti non si ricontrollano in rete: si scarica solo ciò che manca
        $env:MOSAIC_HOME = $InstallDir
        Push-Location $InstallDir
        try {
            $absent = Get-Output $venvPython @('-c', "from mosaic import assets; print(','.join(assets.missing('core')))")
            if ($absent -and $absent.Trim()) {
                Write-Step "Scarico i modelli mancanti: $($absent.Trim())"
                try {
                    Invoke-Logged $venvPython @('-m', 'mosaic.assets', 'fetch', '--group', 'core')
                    $summary.Add("scaricati: $($absent.Trim())")
                } catch {
                    Write-Warn "Modelli mancanti non scaricati (serve Internet): rilancia l'aggiornamento più tardi. ($($_.Exception.Message))"
                }
            } else {
                Write-Host 'Modelli e programmi già presenti: nessun download.'
                $summary.Add('modelli e programmi invariati')
            }
        } finally {
            Pop-Location
        }
    } elseif (-not $SkipAssets) {
        $env:MOSAIC_HOME = $InstallDir
        Push-Location $InstallDir
        try {
            Write-Step 'Modelli e programmi portabili (SigLIP 2, volti, exiftool, ffmpeg)'
            Invoke-Logged $venvPython @('-m', 'mosaic.assets', 'fetch', '--group', 'core')
            if (-not $script:SkipLLM) {
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

    $newVersion = Get-AppVersion $InstallDir
    Set-Content -LiteralPath (Join-Path $InstallDir 'installed-version.txt') -Value $newVersion -Encoding ASCII

    Write-Step 'Collegamenti'
    Write-Launcher $InstallDir
    if ($hasPayload) { Register-Uninstaller $InstallDir }

    Write-Host ''
    if ($isUpdate) {
        $from = if ($oldVersion) { $oldVersion } else { 'precedente' }
        Write-Host "Aggiornamento completato in ${InstallDir}: da $from a $newVersion" -ForegroundColor Green
        Write-Host 'Le tue foto, il catalogo, le persone e le impostazioni sono rimasti come erano.'
        Write-Host '(Se il catalogo ha bisogno di un nuovo formato, Mosaic ne fa una copia in data\backups al primo avvio.)'
        foreach ($line in $summary) { Write-Host "  - $line" }
        Write-Log "Aggiornato da $from a ${newVersion}: $($summary -join '; ')"
    } else {
        Write-Host "Installazione completata in $InstallDir" -ForegroundColor Green
    }
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
    if ($script:LogFile) {
        Write-Host "Dettagli in: $script:LogFile"
        try {
            $copy = Join-Path ([Environment]::GetFolderPath('Desktop')) 'mosaic-setup-log.txt'
            Copy-Item $script:LogFile $copy -Force
            Write-Host "Ne ho messo una copia sul Desktop (mosaic-setup-log.txt): mandala per ricevere aiuto."
        } catch {
            Write-Log "Copia del log non riuscita: $($_.Exception.Message)"
        }
    }
    exit 1
}
