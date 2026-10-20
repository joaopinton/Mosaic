<#
.SYNOPSIS
    Compila Mosaic.exe (avvio dell'app desktop) con il csc.exe di .NET Framework 4, già presente in Windows.

.PARAMETER OutFile  Dove scrivere l'exe (predefinito: Mosaic.exe nella radice del progetto).
#>
[CmdletBinding()]
param([string]$OutFile)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
if (-not $OutFile) { $OutFile = Join-Path $Root 'Mosaic.exe' }

$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path $csc)) { $csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe' }
if (-not (Test-Path $csc)) { throw 'csc.exe di .NET Framework 4 non trovato.' }

$src = Join-Path $PSScriptRoot 'launcher\Mosaic.cs'
$ico = Join-Path $Root 'mosaic.ico'
$out = & $csc /nologo /codepage:65001 /target:winexe /optimize+ /platform:anycpu "/win32icon:$ico" "/out:$OutFile" `
    /reference:System.Windows.Forms.dll $src 2>&1
if ($LASTEXITCODE -ne 0) { throw "Compilazione di Mosaic.exe fallita:`n$($out -join "`n")" }
Write-Host "Creato $OutFile"
