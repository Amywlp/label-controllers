#Requires -RunAsAdministrator
<#
.SYNOPSIS
Expose the Docker service running inside WSL2 to the Windows LAN.

Requires Docker Compose to be running in the selected WSL distribution.
Run this from an elevated PowerShell after the WSL IP changes.
#>
param(
    [string]$Distro = "Ubuntu-22.04",
    [int]$ListenPort = 8000,
    [string]$RuleName = "Label Controllers API"
)

$ErrorActionPreference = "Stop"

$wslOutput = (& wsl.exe -d $Distro -- hostname -I 2>$null)
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($wslOutput)) {
    throw "Could not determine the IPv4 address of WSL distro '$Distro'."
}
$wslIp = (($wslOutput.Trim() -split "\s+") | Where-Object { $_ -match "^\d+\.\d+\.\d+\.\d+$" } | Select-Object -First 1)
if (-not $wslIp) {
    throw "No WSL IPv4 address was returned."
}

& netsh.exe interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=$ListenPort | Out-Null
& netsh.exe interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=$ListenPort connectaddress=$wslIp connectport=$ListenPort | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Failed to create the portproxy rule."
}

Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule `
    -DisplayName $RuleName `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort $ListenPort `
    -Profile Any | Out-Null

$lanAddresses = Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
    Select-Object -ExpandProperty IPAddress

Write-Output "Label Controllers is exposed on port $ListenPort."
Write-Output "WSL target: ${wslIp}:$ListenPort"
Write-Output "LAN addresses:"
$lanAddresses | ForEach-Object { Write-Output ("  http://{0}:{1}" -f $_, $ListenPort) }