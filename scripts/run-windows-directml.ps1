[CmdletBinding()]
param(
    [string]$HostAddress = "0.0.0.0",
    [int]$Port = 8000,
    [string]$VenvPath = ".venv-directml"
)

$ErrorActionPreference = "Stop"
if ($env:OS -ne "Windows_NT") {
    throw "DirectML serving must run in native Windows PowerShell, not WSL."
}

$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    $venvPython = Join-Path $root "$VenvPath\Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        throw "DirectML environment not found. Run .\scripts\setup-windows-directml.ps1 first."
    }

    $envFile = Join-Path $root ".env"
    if (Test-Path $envFile) {
        foreach ($line in Get-Content $envFile) {
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

    $env:LABEL_CONTROLLERS_DEVICE = "directml"
    $env:LABEL_CONTROLLERS_OCR_DEVICE = "directml"
    $env:LABEL_CONTROLLERS_MODEL_PATH = Join-Path $root "models\icon_detect.pt"
    $env:YOLO_CONFIG_DIR = Join-Path $env:TEMP "label-controllers-yolo"
    $env:MPLCONFIGDIR = Join-Path $env:TEMP "label-controllers-matplotlib"
    New-Item -ItemType Directory -Force $env:YOLO_CONFIG_DIR, $env:MPLCONFIGDIR | Out-Null

    Write-Host "Starting DirectML API on http://${HostAddress}:$Port"
    & $venvPython -m uvicorn label_controllers.api:app --host $HostAddress --port $Port --proxy-headers
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
