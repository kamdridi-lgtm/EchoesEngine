param(
    [string]$PythonExecutable = "",
    [string]$PythonLauncher = "",
    [string]$PythonVersion = "3.10",
    [string]$VenvPath = "D:\A.I\EchoesCinema\.venv-cinema",
    [string]$TorchIndexUrl = "",
    [switch]$Recreate,
    [switch]$TorchProbeSelfTest
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Get-TorchRepairAction {
    param(
        [bool]$Importable,
        [bool]$CudaBuild,
        [bool]$CudaAvailable
    )
    if (-not $Importable) { return "INSTALL_CUDA_WHEEL" }
    if (-not $CudaBuild) { return "REPLACE_CPU_WHEEL" }
    if (-not $CudaAvailable) { return "BLOCK_CUDA_RUNTIME" }
    return "READY"
}

function Get-TorchRuntimeProbe {
    param([string]$PythonPath)
    if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        return [pscustomobject]@{
            importable = $false
            cudaBuild = $false
            cudaAvailable = $false
            version = $null
            torchCudaVersion = $null
            error = "Python executable is missing: $PythonPath"
        }
    }

    $probeScript = @'
import json
result = {
    "importable": False,
    "cudaBuild": False,
    "cudaAvailable": False,
    "version": None,
    "torchCudaVersion": None,
    "error": None,
}
try:
    import torch
    result["importable"] = True
    result["version"] = str(torch.__version__)
    result["torchCudaVersion"] = str(torch.version.cuda) if torch.version.cuda else None
    result["cudaBuild"] = bool(torch.version.cuda)
    result["cudaAvailable"] = bool(torch.cuda.is_available())
except Exception as error:
    result["error"] = str(error)
print(json.dumps(result))
'@

    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "SilentlyContinue"
        $raw = $probeScript | & $PythonPath - 2>&1 | Out-String
        $exitCode = $LASTEXITCODE
    } catch {
        $raw = $_.Exception.Message
        $exitCode = 1
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($exitCode -ne 0) {
        return [pscustomobject]@{
            importable = $false
            cudaBuild = $false
            cudaAvailable = $false
            version = $null
            torchCudaVersion = $null
            error = ([string]$raw).Trim()
        }
    }
    try {
        return ([string]$raw).Trim() | ConvertFrom-Json
    } catch {
        return [pscustomobject]@{
            importable = $false
            cudaBuild = $false
            cudaAvailable = $false
            version = $null
            torchCudaVersion = $null
            error = "Torch probe returned invalid JSON: $raw"
        }
    }
}

if ($TorchProbeSelfTest) {
    if ((Get-TorchRepairAction -Importable $false -CudaBuild $false -CudaAvailable $false) -ne "INSTALL_CUDA_WHEEL") {
        throw "Missing-torch repair classification failed."
    }
    if ((Get-TorchRepairAction -Importable $true -CudaBuild $false -CudaAvailable $false) -ne "REPLACE_CPU_WHEEL") {
        throw "CPU-only torch repair classification failed."
    }
    if ((Get-TorchRepairAction -Importable $true -CudaBuild $true -CudaAvailable $false) -ne "BLOCK_CUDA_RUNTIME") {
        throw "CUDA runtime blocker classification failed."
    }
    if ((Get-TorchRepairAction -Importable $true -CudaBuild $true -CudaAvailable $true) -ne "READY") {
        throw "CUDA-ready classification failed."
    }
    Write-Host "Cinema bootstrap torch probe PASS missing=install cpu-wheel=replace cuda-runtime=block ready=keep"
    exit 0
}

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
$lowVramProvider = Join-Path $repoRoot "providers\modelscope_low_vram_provider.py"
$p0Preflight = Join-Path $repoRoot "tools\cinema_p0_preflight.py"
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

$torchBefore = Get-TorchRuntimeProbe -PythonPath $python
$torchAction = Get-TorchRepairAction `
    -Importable ([bool]$torchBefore.importable) `
    -CudaBuild ([bool]$torchBefore.cudaBuild) `
    -CudaAvailable ([bool]$torchBefore.cudaAvailable)

switch ($torchAction) {
    "READY" {
        Write-Host "CUDA-enabled PyTorch is already installed and the GPU runtime is available. Version: $($torchBefore.version) CUDA: $($torchBefore.torchCudaVersion)"
    }
    "BLOCK_CUDA_RUNTIME" {
        Write-Host "A CUDA-enabled PyTorch wheel is installed, but the NVIDIA CUDA runtime is unavailable. Package reinstall is intentionally skipped."
    }
    "INSTALL_CUDA_WHEEL" {
        Write-Host "PyTorch is missing; installing the selected official CUDA wheel."
    }
    "REPLACE_CPU_WHEEL" {
        Write-Host "CPU-only PyTorch detected. It will be replaced automatically with the selected official CUDA wheel."
    }
}

if ($torchAction -in @("INSTALL_CUDA_WHEEL", "REPLACE_CPU_WHEEL")) {
    if (-not $TorchIndexUrl) {
        throw "A CUDA PyTorch wheel is required. Re-run with -TorchIndexUrl set to the official CUDA wheel index selected for this machine."
    }
    if (-not $TorchIndexUrl.StartsWith("https://download.pytorch.org/whl/")) {
        throw "TorchIndexUrl must be an official https://download.pytorch.org/whl/ index"
    }
    $installArguments = @("-m", "pip", "install", "--upgrade")
    if ($torchAction -eq "REPLACE_CPU_WHEEL") {
        $installArguments += "--force-reinstall"
    }
    $installArguments += @("torch", "torchvision", "--index-url", $TorchIndexUrl)
    & $python @installArguments
    if ($LASTEXITCODE -ne 0) {
        throw "PyTorch CUDA installation failed"
    }
    $torchAfter = Get-TorchRuntimeProbe -PythonPath $python
    if (-not [bool]$torchAfter.importable -or -not [bool]$torchAfter.cudaBuild) {
        throw "The official CUDA wheel installation completed but PyTorch still has no CUDA build. Version=$($torchAfter.version) Error=$($torchAfter.error)"
    }
    Write-Host "CUDA PyTorch wheel installed. Version: $($torchAfter.version) CUDA build: $($torchAfter.torchCudaVersion) Runtime available: $($torchAfter.cudaAvailable)"
}

& $python -m pip install -r $requirements
if ($LASTEXITCODE -ne 0) {
    throw "Diffusers provider dependency installation failed"
}

& $python -m py_compile $provider $proofProvider $lowVramProvider $p0Preflight $service $runner
if ($LASTEXITCODE -ne 0) {
    throw "Cinema provider/preflight/service Python compilation failed"
}

& $python $lowVramProvider --self-test
if ($LASTEXITCODE -ne 0) {
    throw "Active low-VRAM provider self-test failed"
}
& $python $p0Preflight --self-test
if ($LASTEXITCODE -ne 0) {
    throw "Cinema P0 preflight self-test failed"
}

$diagnosticScript = @'
import json
import os
import platform
import shutil
import sys

report = {
    "schema": "echoes.cinema-bootstrap-report.v2",
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

    cuda_build = bool(torch.version.cuda)
    cuda_available = bool(torch.cuda.is_available())
    report["packages"] = {
        "torch": torch.__version__,
        "diffusers": diffusers.__version__,
        "transformers": transformers.__version__,
        "accelerate": accelerate.__version__,
        "safetensors": safetensors.__version__,
    }
    report["cuda"] = {
        "buildPresent": cuda_build,
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
        report["failureClass"] = "SYSTEM_DRIVE_STORAGE"
        report["blocker"] = "A Cinema cache or temporary path still targets drive C:"
    elif not cuda_build:
        report["status"] = "FAILED"
        report["failureClass"] = "CUDA_WHEEL_MISSING"
        report["blocker"] = "PyTorch is importable but it is not a CUDA-enabled build"
    elif not cuda_available:
        report["status"] = "BLOCKED"
        report["failureClass"] = "CUDA_RUNTIME_UNAVAILABLE"
        report["operatorRestartRequired"] = True
        report["blocker"] = "A CUDA-enabled PyTorch wheel is installed, but the NVIDIA driver/runtime is unavailable"
    else:
        report["status"] = "PASS"
except Exception as error:
    report["status"] = "FAILED"
    report["failureClass"] = "DEPENDENCY_IMPORT_FAILED"
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
Write-Host "CUDA build present: $($report.cuda.buildPresent)"
Write-Host "CUDA available: $($report.cuda.available)"
Write-Host "GPU: $($report.cuda.deviceName)"
Write-Host "C drive selected by Cinema caches: $($report.storage.cDriveSelected)"

if ($report.status -ne "PASS") {
    throw "Cinema AI bootstrap is not ready [$($report.failureClass)]: $($report.blocker)"
}

Write-Host "Echoes Cinema AI environment PASS"
Write-Host "Virtual environment Python: $python"
