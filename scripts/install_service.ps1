# ZUZZ Windows Service Installer (requires admin)
param(
    [string]$ServiceName = "ZUZZ",
    [string]$Python = "python",
    [string]$Script = (Resolve-Path "..\start.py").Path,
    [string]$WorkDir = (Resolve-Path "..\").Path
)

if (-NOT ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    throw "Run this script as Administrator."
}

$binaryPath = '"' + $Python + '" "' + $Script + '"'
New-Service -Name $ServiceName -BinaryPath $binaryPath -DisplayName "ZUZZ Digital Brain" -Description "Local-first AI assistant" -StartupType Automatic | Out-Null
Start-Service -Name $ServiceName
Write-Host "Service $ServiceName installed and started."
