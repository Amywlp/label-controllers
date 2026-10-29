[CmdletBinding()]
param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8022,
    [string]$InferenceUrl = "http://127.0.0.1:8011",
    [string]$VenvPath = ".venv-directml",
    [switch]$EnableInput,
    [switch]$EnableActivation
)

$ErrorActionPreference = "Stop"
if ($env:OS -ne "Windows_NT") {
    throw "The desktop adapter must run in a native Windows PowerShell session."
}

$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    $venvPython = Join-Path $root "$VenvPath\Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        throw "Python environment not found at $venvPython. Run .\scripts\setup-windows-directml.ps1 first."
    }

    $envFile = Join-Path $root ".env"
    if (Test-Path $envFile) {
        foreach ($line in Get-Content $envFile -Encoding utf8) {
            $trimmed = $line.Trim()
            if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) { continue }
            $parts = $trimmed.Split("=", 2)
            $name = $parts[0].Trim()
            $value = $parts[1].Trim().Trim('"').Trim("'")
            if ($name -and -not [Environment]::GetEnvironmentVariable($name, "Process")) {
                [Environment]::SetEnvironmentVariable($name, $value, "Process")
            }
        }
    }

    $token = $env:LABEL_CONTROLLERS_DESKTOP_TOKEN
    if (-not $token) { $token = $env:LABEL_CONTROLLERS_API_KEY }
    if (-not $token) {
        throw "Set LABEL_CONTROLLERS_DESKTOP_TOKEN or LABEL_CONTROLLERS_API_KEY before starting the adapter."
    }

    $env:LABEL_CONTROLLERS_DESKTOP_HOST = $HostAddress
    $env:LABEL_CONTROLLERS_DESKTOP_PORT = "$Port"
    $env:LABEL_CONTROLLERS_DESKTOP_INFERENCE_URL = $InferenceUrl
    $env:LABEL_CONTROLLERS_DESKTOP_ALLOW_INPUT = if ($EnableInput) { "true" } else { "false" }
    $env:LABEL_CONTROLLERS_DESKTOP_ALLOW_ACTIVATE = if ($EnableActivation) { "true" } else { "false" }
    $env:PYTHONPATH = Join-Path $root "src"

    Write-Host "Starting Label Controllers Desktop Adapter on http://${HostAddress}:$Port"
    Write-Host "Inference API: $InferenceUrl"
    Write-Host "Mouse injection: $($env:LABEL_CONTROLLERS_DESKTOP_ALLOW_INPUT); activation: $($env:LABEL_CONTROLLERS_DESKTOP_ALLOW_ACTIVATE)"
    & $venvPython -m uvicorn label_controllers.desktop.api:app --host $HostAddress --port $Port --no-proxy-headers
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}

