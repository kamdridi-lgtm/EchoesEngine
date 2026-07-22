param(
    [string]$PythonExecutable = "",
    [string]$PythonLauncher = "",
    [string]$PythonVersion = "3.10",
    [string]$VenvPath = "D:\A.I\EchoesCinema\.venv-cinema",
    [string]$TorchIndexUrl = "",
    [switch]$Recreate
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venv = if ([System.IO.Path]::IsPathRooted($VenvPath)) { [System.IO.Path]::GetFullPath($VenvPath) } else { [System.IO.Path]::GetFullPath((Join-Path $repoRoot $VenvPath)) }
$venvDrive = [System.IO.Path]::GetPathRoot($venv)
if (-not $venvDrive -or $venvDrive.TrimEnd("\").ToUpperInvariant() -eq "C:") {
    throw "Cinema virtual environment must be on drive D: or another non-C: drive. Current path: $venv"
}

$workspaceRoot = Split-Path -Parent $venv
$cacheRoot = Join-Path $workspaceRoot "cache"
$tempRoot = Join-Path $workspaceRoot "temp"
$requirements = Join-Path $repoRoot "providers\requirements-diffusers.txt"
$provider = Join-Path $repoRoot "providers\diffusers_video_provider.py"
$proofProvider = Join-Path $repoRoot "providers\modelscope_proof_provider.py"
$service = Join-Path $repoRoot "tools\cinema_job_service.py"
$runner = Join-Path $repoRoot "tools\cinema_job_runner.py"
$ensurePython = Join-Path $repoRoot "scripts\ensure-python-on-d.ps1"
$reportPath = Join-Path $workspaceRoot "cinema-bootstrap-report.json"

$storageDirectories = @(
    $workspaceRoot,
    $cacheRoot,
    (Join-Path $cacheRoot "huggingface"),
    (Join-Path $cacheRoot "huggingface\hub"),
    (Join-Path $cacheRoot "huggingface\transformers"),
    (Join-Path $cacheRoot "torch"),
    (Join-Path $cacheRoot "pip"),
    (Join-Path $cacheRoot "xdg"),
    (Join-Path $cacheRoot "cuda"),
    (Join-Path $cacheRoot "numba"),
    (Join-Path $cacheRoot "python-bytecode"),
    $tempRoot
)
foreach ($directory in $storageDirectories) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}

$env:HF_HOME = Join-Path $cacheRoot "huggingface"
$env:HF_HUB_CACHE = Join-Path $cacheRoot "huggingface\hub"
$env:HUGGINGFACE_HUB_CACHE = $env:HF_HUB_CACHE
$env:TRANSFORMERS_CACHE = Join-Path $cacheRoot "huggingface\transformers"
$env:TORCH_HOME = Join-Path $cacheRoot "torch"
$env:PIP_CACHE_DIR = Join-Path $cacheRoot "pip"
$env:XDG_CACHE_HOME = Join-Path $cacheRoot "xdg"
$env:CUDA_CACHE_PATH = Join-Path $cacheRoot "cuda"
$env:NUMBA_CACHE_DIR = Join-Path $cacheRoot "numba"
$env:PYTHONPYCACHEPREFIX = Join-Path $cacheRoot "python-bytecode"
$env:TEMP = $tempRoot
$env:TMP = $tempRoot
$env:TMPDIR = $tempRoot
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = "1"
$env:PYTORCH_CUDA_ALLOC_CONF = "expandable_segments:True"

function Test-BasePython {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    try {
        & $Path -c "import struct, sys; assert sys.version_info[:2] in ((3,10),(3,11)); assert struct.calcsize('P')*8 == 64" *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Resolve-BasePython {
    if (Test-BasePython -Path $PythonExecutable) {
        return (Resolve-Path -LiteralPath $PythonExecutable).Path
    }

    if ($PythonLauncher -and $PythonLauncher -ne "py" -and (Test-BasePython -Path $PythonLauncher)) {
        return (Resolve-Path -LiteralPath $PythonLauncher).Path
    }

    if (-not (Test-Path $ensurePython -PathType Leaf)) {
        throw "Python resolver script not found: $ensurePython"
    }

    Write-Host "Resolving a real 64-bit Python 3.10/3.11 executable without trusting the stale py launcher."
    $resolvedOutput = & $ensurePython `
        -InstallRoot "D:\A.I\Python310" `
        -WorkspaceRoot $workspaceRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Python discovery/installation failed"
    }
    $resolved = [string]($resolvedOutput | Select-Object -Last 1)
    if (-not (Test-BasePython -Path $resolved)) {
        throw "Python resolver returned an unusable executable: $resolved"
    }
    return (Resolve-Path -LiteralPath $resolved).Path
}

function Test-PythonModuleImport {
    param(
        [string]$PythonPath,
        [string]$ModuleName
    )

    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "SilentlyContinue"
        & $PythonPath -c "import $ModuleName; print(getattr($ModuleName, '__version__', 'present'))" *> $null
        $exitCode = $LASTEXITCODE
    } catch {
        $exitCode = 1
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    return $exitCode -eq 0
}

if (-not (Test-Path $requirements)) {
    throw "Requirements file not found: $requirements"
}
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    throw "FFmpeg is not available in PATH"
}
if (-not (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
    throw "FFprobe is not available in PATH"
}

$basePython = Resolve-BasePython
Write-Host "Cinema workspace: $workspaceRoot"
Write-Host "Base Python: $basePython"
Write-Host "Virtual environment: $venv"
Write-Host "Package/model cache: $cacheRoot"
Write-Host "Temporary files: $tempRoot"
Write-Host "Drive C: is not selected for Cinema storage."

if ($Recreate -and (Test-Path $venv)) {
    Remove-Item $venv -Recurse -Force
}
if (-not (Test-Path $venv)) {
    Write-Host "Creating Cinema virtual environment: $venv"
    & $basePython -m venv $venv
    if ($LASTEXITCODE -ne 0) {
        throw "Python venv creation failed with exit code $LASTEXITCODE using $basePython"
    }
}

$python = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Virtual environment Python not found: $python"
}

& $python -m pip install --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) {
    throw "pip bootstrap failed"
}

$torchPresent = Test-PythonModuleImport -PythonPath $python -ModuleName "torch"
if ($torchPresent) {
    Write-Host "PyTorch is already installed in the Cinema virtual environment."
} else {
    Write-Host "PyTorch is not installed yet; continuing with the selected official CUDA wheel index."
}

if (-not $torchPresent) {
    if (-not $TorchIndexUrl) {
        throw "PyTorch is not installed. Re-run with -TorchIndexUrl set to the official CUDA wheel index selected for this machine. The bootstrap refuses to guess a CUDA build."
    }
    if (-not $TorchIndexUrl.StartsWith("https://download.pytorch.org/whl/")) {
        throw "TorchIndexUrl must be an official https://download.pytorch.org/whl/ index"
    }
    Write-Host "Installing PyTorch from the selected official index into the D: virtual environment."
    & $python -m pip install torch torchvision --index-url $TorchIndexUrl
    if ($LASTEXITCODE -ne 0) {
        throw "PyTorch installation failed"
    }
}

& $python -m pip install -r $requirements
if ($LASTEXITCODE -ne 0) {
    throw "Diffusers provider dependency installation failed"
}

& $python -m py_compile $provider $proofProvider $service $runner
if ($LASTEXITCODE -ne 0) {
    throw "Cinema provider/service Python compilation failed"
}

$diagnosticScript = @'
import json
import os
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
    "storage": {
        "hfHome": os.environ.get("HF_HOME"),
        "hfHubCache": os.environ.get("HF_HUB_CACHE"),
        "torchHome": os.environ.get("TORCH_HOME"),
        "pipCache": os.environ.get("PIP_CACHE_DIR"),
        "temp": os.environ.get("TEMP"),
        "pythonBytecode": os.environ.get("PYTHONPYCACHEPREFIX"),
        "systemDriveWritesAllowed": False,
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
    storage_paths = [
        report["storage"]["hfHome"],
        report["storage"]["hfHubCache"],
        report["storage"]["torchHome"],
        report["storage"]["pipCache"],
        report["storage"]["temp"],
        report["storage"]["pythonBytecode"],
    ]
    c_drive_used = any(str(path or "").lower().startswith("c:\\") for path in storage_paths)
    report["storage"]["cDriveSelected"] = c_drive_used
    if c_drive_used:
        report["status"] = "FAILED"
        report["blocker"] = "A Cinema cache or temporary path still targets drive C:"
    elif cuda_available:
        report["status"] = "PASS"
    else:
        report["status"] = "PARTIAL"
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
Write-Host "C drive selected by Cinema caches: $($report.storage.cDriveSelected)"

if ($report.status -ne "PASS") {
    throw "Cinema AI bootstrap is not ready: $($report.blocker)"
}

Write-Host "Echoes Cinema AI environment PASS"
Write-Host "Virtual environment Python: $python"
