[CmdletBinding()]
param(
    [string]$PythonExe = "py",
    [string[]]$PythonArguments = @("-3.10"),
    [string]$VenvPath = ".venv-directml"
)

$ErrorActionPreference = "Stop"
if ($env:OS -ne "Windows_NT") {
    throw "DirectML setup must run in native Windows PowerShell, not WSL."
}

$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    if (-not (Test-Path $VenvPath)) {
        & $PythonExe @PythonArguments -m venv $VenvPath
        if ($LASTEXITCODE -ne 0) { throw "Failed to create the DirectML virtual environment." }
    }

    $venvPython = Join-Path $root "$VenvPath\Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        throw "Python executable not found at $venvPython"
    }

    & $venvPython -m pip install --upgrade pip setuptools wheel
    if ($LASTEXITCODE -ne 0) { throw "Failed to update packaging tools." }

    & $venvPython -m pip install -r requirements.directml.txt
    if ($LASTEXITCODE -ne 0) { throw "Failed to install DirectML dependencies." }

    # RapidOCR declares the CPU onnxruntime distribution. Install it without
    # dependencies so onnxruntime-directml remains the single ORT runtime.
    & $venvPython -m pip install --no-deps rapidocr-onnxruntime==1.2.3
    if ($LASTEXITCODE -ne 0) { throw "Failed to install RapidOCR without dependency replacement." }

    & $venvPython -m pip install --no-deps -e .
    if ($LASTEXITCODE -ne 0) { throw "Failed to install the label-controllers package." }

    $modelPath = Join-Path $root "models\icon_detect.pt"
    if (-not (Test-Path $modelPath)) {
        & $venvPython scripts\download_model.py --output $modelPath
        if ($LASTEXITCODE -ne 0) { throw "Failed to download the YOLO model." }
    }

    & $venvPython -c "import torch_directml; print('DirectML GPU:', torch_directml.device_name(0).rstrip(chr(0)))"
    if ($LASTEXITCODE -ne 0) { throw "DirectML validation failed." }

    Write-Host ""
    Write-Host "DirectML environment ready: $VenvPath"
    Write-Host "Start the GPU API with: .\scripts\run-windows-directml.ps1"
}
finally {
    Pop-Location
}
