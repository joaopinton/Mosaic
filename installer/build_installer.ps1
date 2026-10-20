<#
.SYNOPSIS
    Costruisce dist\MosaicSetup.exe (self-extracting di Windows, via IExpress: nessun download).

.DESCRIPTION
    L'exe contiene setup.ps1, MosaicSetup.cmd e payload.zip (codice, docs, assets.yaml, requirements,
    catalogo legacy). Modelli e programmi NON sono inclusi: li scarica l'installer sul PC di destinazione.
#>
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Dist = Join-Path $Root 'dist'
$Work = Join-Path ([IO.Path]::GetTempPath()) ('mosaic_build_' + [guid]::NewGuid().ToString('N').Substring(0, 8))
$Stage = Join-Path $Work 'stage'
$Pack = Join-Path $Work 'pack'
$Exe = Join-Path $Dist 'MosaicSetup.exe'

New-Item -ItemType Directory -Force -Path $Stage, $Pack, $Dist | Out-Null
try {
    foreach ($item in 'assets.yaml', 'requirements.txt', 'pyproject.toml', 'README.md', 'mosaic.ico', 'Mosaic.bat', 'setup.ps1', '.gitignore') {
        Copy-Item (Join-Path $Root $item) $Stage
    }
    if (-not (Test-Path (Join-Path $Root 'frontend\dist\index.html'))) {
        throw "Manca frontend\dist: compila l'interfaccia con 'npm run build' in frontend\ prima di creare l'installer."
    }
    robocopy (Join-Path $Root 'mosaic') (Join-Path $Stage 'mosaic') /E /XD __pycache__ /NFL /NDL /NJH /NJS | Out-Null
    robocopy (Join-Path $Root 'docs') (Join-Path $Stage 'docs') /E /NFL /NDL /NJH /NJS | Out-Null
    robocopy (Join-Path $Root 'frontend\dist') (Join-Path $Stage 'frontend\dist') /E /NFL /NDL /NJH /NJS | Out-Null
    robocopy (Join-Path $Root '_legacy') (Join-Path $Stage '_legacy') /E /XD modelli /XF features_cache.json /NFL /NDL /NJH /NJS | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $Stage 'models'), (Join-Path $Stage 'runtime'), (Join-Path $Stage 'data') | Out-Null
    foreach ($model in 'models.yaml', 'labels_it.yaml') { Copy-Item (Join-Path $Root "models\$model") (Join-Path $Stage 'models') }
    Copy-Item (Join-Path $PSScriptRoot 'uninstall.ps1') $Stage
    & (Join-Path $PSScriptRoot 'build_launcher.ps1') -OutFile (Join-Path $Stage 'Mosaic.exe')

    Compress-Archive -Path (Join-Path $Stage '*') -DestinationPath (Join-Path $Pack 'payload.zip') -CompressionLevel Optimal
    Copy-Item (Join-Path $Root 'setup.ps1') $Pack
    Copy-Item (Join-Path $PSScriptRoot 'MosaicSetup.cmd') $Pack

    $sed = Join-Path $Work 'mosaic.sed'
    Set-Content -Path $sed -Encoding ASCII -Value @"
[Version]
Class=IEXPRESS
SEDVersion=3
[Options]
PackagePurpose=InstallApp
ShowInstallProgramWindow=0
HideExtractAnimation=1
UseLongFileName=1
InsideCompressed=0
CAB_FixedSize=0
CAB_ResvCodeSigning=0
RebootMode=N
InstallPrompt=%InstallPrompt%
DisplayLicense=%DisplayLicense%
FinishMessage=%FinishMessage%
TargetName=%TargetName%
FriendlyName=%FriendlyName%
AppLaunched=%AppLaunched%
PostInstallCmd=%PostInstallCmd%
AdminQuietInstCmd=%AdminQuietInstCmd%
UserQuietInstCmd=%UserQuietInstCmd%
SourceFiles=SourceFiles
[Strings]
InstallPrompt=
DisplayLicense=
FinishMessage=
TargetName=$Exe
FriendlyName=Installazione di Mosaic
AppLaunched=cmd /c MosaicSetup.cmd
PostInstallCmd=<None>
AdminQuietInstCmd=
UserQuietInstCmd=
FILE0="MosaicSetup.cmd"
FILE1="setup.ps1"
FILE2="payload.zip"
[SourceFiles]
SourceFiles0=$Pack\
[SourceFiles0]
%FILE0%=
%FILE1%=
%FILE2%=
"@

    if (Test-Path $Exe) { Remove-Item $Exe -Force }
    $iexpress = Join-Path $env:WINDIR 'System32\iexpress.exe'
    # IExpress è un programma a finestra: Start-Process lo fa uscire con codice 1, mentre 'start /wait' via cmd funziona.
    # IExpress non accetta virgolette attorno al file .sed: uso il percorso breve 8.3 (senza spazi) e nessuna virgoletta.
    $shortSed = (New-Object -ComObject Scripting.FileSystemObject).GetFile($sed).ShortPath
    $runner = Join-Path $Work 'run_iexpress.cmd'
    Set-Content -Path $runner -Encoding ASCII -Value ('start /wait iexpress /N /Q {0}' -f $shortSed)
    & cmd.exe /c $runner
    # IExpress può ritornare prima di aver finito di scrivere l'exe: attendo che compaia e si stabilizzi.
    $deadline = (Get-Date).AddSeconds(120)
    $lastSize = -1
    while ((Get-Date) -lt $deadline) {
        if (Test-Path $Exe) {
            $size = (Get-Item $Exe).Length
            if ($size -gt 0 -and $size -eq $lastSize) { break }
            $lastSize = $size
        }
        Start-Sleep -Seconds 2
    }
    if (-not (Test-Path $Exe)) { throw 'IExpress non ha prodotto MosaicSetup.exe.' }

    $size = [math]::Round((Get-Item $Exe).Length / 1MB, 1)
    $hash = (Get-FileHash $Exe -Algorithm SHA256).Hash
    Write-Host "Creato $Exe ($size MB)"
    Write-Host "SHA-256: $hash"
} finally {
    # MOSAIC_KEEP_BUILD=1 conserva la cartella di lavoro per diagnosticare IExpress.
    if ($env:MOSAIC_KEEP_BUILD) { Write-Host "Cartella di lavoro conservata: $Work" }
    else { Remove-Item $Work -Recurse -Force -ErrorAction SilentlyContinue }
}
