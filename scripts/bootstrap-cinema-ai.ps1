param(
    [string]$PythonLauncher = "py",
    [string]$PythonVersion = "3.10",
    [string]$VenvPath = ".venv-cinema",
    [string]$TorchIndexUrl = "",
    [switch]$Recreate
)

$ErrorActionPreference = "Stop"

function Invoke-PythonLauncher {
    param([string[]]$Arguments)
    if ($PythonLauncher -eq "py") {
        & py "-$PythonVersion" @Arguments
    } else {
        & $PythonLauncher @Arguments
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed with exit code $LASTEXITCODE"
    }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venv = if ([System.IO.Path]::IsPathRooted($VenvPath)) { $VenvPath } else { Join-Path $repoRoot $VenvPath }
$requirements = Join-Path $repoRoot "providers\requirements-diffusers.txt"
$provider = Join-Path $repoRoot "providers\diffusers_video_provider.py"
$service = Join-Path $repoRoot "tools\cinema_job_service.py"
$runner = Join-Path $repoRoot "tools\cinema_job_runner.py"
$reportPath = Join-Path $repoRoot "cinema-bootstrap-report.json"

if (-not (Test-Path $requirements)) {
    throw "Requirements file not found: $requirements"
}
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    throw "FFmpeg is not available in PATH"
}
if (-not (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
    throw "FFprobe is not available in PATH"
}

if ($Recreate -and (Test-Path $venv)) {
    Remove-Item $venv -Recurse -Force
}
if (-not (Test-Path $venv)) {
    Write-Host "Creating Cinema virtual environment: $venv"
    Invoke-PythonLauncher -Arguments @("-m", "venv", $venv)
}

$python = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Virtual environment Python not found: $python"
}

& $python -m pip install --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) {
    throw "pip bootstrap failed"
}

$torchPresent = $false
& $python -c "import torch; print(torch.__version__)" 2>$null
if ($LASTEXITCODE -eq 0) {
    $torchPresent = $true
}

if (-not $torchPresent) {
    if (-not $TorchIndexUrl) {
        throw "PyTorch is not installed. Re-run with -TorchIndexUrl set to the official CUDA wheel index selected for this machine. The bootstrap refuses to guess a CUDA build."
    }
    if (-not $TorchIndexUrl.StartsWith("https://download.pytorch.org/whl/")) {
        throw "TorchIndexUrl must be an official https://download.pytorch.org/whl/ index"
    }
    Write-Host "Installing PyTorch from the selected official index."
    & $python -m pip install torch torchvision --index-url $TorchIndexUrl
    if ($LASTEXITCODE -ne 0) {
        throw "PyTorch installation failed"
    }
}

& $python -m pip install -r $requirements
if ($LASTEXITCODE -ne 0) {
    throw "Diffusers provider dependency installation failed"
}

& $python -m py_compile $provider $service $runner
if ($LASTEXITCODE -ne 0) {
    throw "Cinema provider/service Python compilation failed"
}

$diagnosticScript = @'
import json
import platform
import shutil
import sys

report = {
    "schema": "echoes.cinema-bootstrap-report.v1",
    "status": "RUNNING",
    "python": {
        "version": sys.version,
        "executable": sys.executable,
        "platform": platform.platform(),
    },
    "tools": {
        "ffmpeg": shutil.which("ffmpeg"),
        "ffprobe": shutil.which("ffprobe"),
    },
}
try:
    import torch
    import diffusers
    import transformers
    import accelerate
    import safetensors

    cuda_available = bool(torch.cuda.is_available())
    report["packages"] = {
        "torch": torch.__version__,
        "diffusers": diffusers.__version__,
        "transformers": transformers.__version__,
        "accelerate": accelerate.__version__,
        "safetensors": safetensors.__version__,
    }
    report["cuda"] = {
        "available": cuda_available,
        "deviceCount": torch.cuda.device_count() if cuda_available else 0,
        "deviceName": torch.cuda.get_device_name(0) if cuda_available else None,
        "torchCudaVersion": torch.version.cuda,
    }
    report["status"] = "PASS" if cuda_available else "PARTIAL"
    if not cuda_available:
        report["blocker"] = "PyTorch is installed but CUDA is not available"
except Exception as error:
    report["status"] = "FAILED"
    report["blocker"] = str(error)

print(json.dumps(report, indent=2))
'@

$diagnosticOutput = $diagnosticScript | & $python -
if ($LASTEXITCODE -ne 0) {
    throw "Cinema diagnostic failed"
}
$diagnosticOutput | Set-Content -Path $reportPath -Encoding utf8
$report = $diagnosticOutput | ConvertFrom-Json

Write-Host "Cinema bootstrap report: $reportPath"
Write-Host "Status: $($report.status)"
Write-Host "Python: $($report.python.version)"
Write-Host "CUDA available: $($report.cuda.available)"
Write-Host "GPU: $($report.cuda.deviceName)"

if ($report.status -ne "PASS") {
    throw "Cinema AI bootstrap is not ready: $($report.blocker)"
}

Write-Host "Echoes Cinema AI environment PASS"
Write-Host "Virtual environment Python: $python"
