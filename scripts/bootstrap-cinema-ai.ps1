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
        [bool]$CudaAvailable,
        [bool]$ExactRuntime
    )
    if (-not $Importable) { return "INSTALL_CUDA_WHEEL" }
    if (-not $CudaBuild) { return "REPLACE_CPU_WHEEL" }
    if (-not $ExactRuntime) { return "REPLACE_RUNTIME_DRIFT" }
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
            baseVersion = $null
            torchvisionVersion = $null
            torchvisionBaseVersion = $null
            torchCudaVersion = $null
            error = "Python executable is missing: $PythonPath"
        }
    }

    $probeScript = @'
import importlib.metadata
import json
result = {
    "importable": False,
    "cudaBuild": False,
    "cudaAvailable": False,
    "version": None,
    "baseVersion": None,
    "torchvisionVersion": None,
    "torchvisionBaseVersion": None,
    "torchCudaVersion": None,
    "error": None,
}
try:
    import torch
    result["importable"] = True
    result["version"] = str(torch.__version__)
    result["baseVersion"] = str(torch.__version__).split("+", 1)[0]
    try:
        vision = importlib.metadata.version("torchvision")
    except importlib.metadata.PackageNotFoundError:
        vision = None
    result["torchvisionVersion"] = vision
    result["torchvisionBaseVersion"] = vision.split("+", 1)[0] if vision else None
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
            baseVersion = $null
            torchvisionVersion = $null
            torchvisionBaseVersion = $null
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
            baseVersion = $null
            torchvisionVersion = $null
            torchvisionBaseVersion = $null
            torchCudaVersion = $null
            error = "Torch probe returned invalid JSON: $raw"
        }
    }
}

function Test-ExactTorchRuntime {
    param(
        [object]$Probe,
        [object]$Lock
    )
    if (-not [bool]$Probe.importable) { return $false }
    return (
        [string]$Probe.baseVersion -eq [string]$Lock.torchVersion -and
        [string]$Probe.torchvisionBaseVersion -eq [string]$Lock.torchvisionVersion -and
        [string]$Probe.torchCudaVersion -eq [string]$Lock.expectedTorchCudaVersion
    )
}

if ($TorchProbeSelfTest) {
    if ((Get-TorchRepairAction -Importable $false -CudaBuild $false -CudaAvailable $false -ExactRuntime $false) -ne "INSTALL_CUDA_WHEEL") {
        throw "Missing-torch repair classification failed."
    }
    if ((Get-TorchRepairAction -Importable $true -CudaBuild $false -CudaAvailable $false -ExactRuntime $false) -ne "REPLACE_CPU_WHEEL") {
        throw "CPU-only torch repair classification failed."
    }
    if ((Get-TorchRepairAction -Importable $true -CudaBuild $true -CudaAvailable $true -ExactRuntime $false) -ne "REPLACE_RUNTIME_DRIFT") {
        throw "Torch runtime drift classification failed."
    }
    if ((Get-TorchRepairAction -Importable $true -CudaBuild $true -CudaAvailable $false -ExactRuntime $true) -ne "BLOCK_CUDA_RUNTIME") {
        throw "CUDA runtime blocker classification failed."
    }
    if ((Get-TorchRepairAction -Importable $true -CudaBuild $true -CudaAvailable $true -ExactRuntime $true) -ne "READY") {
        throw "CUDA-ready classification failed."
    }
    Write-Host "Cinema bootstrap torch probe PASS missing=install cpu-wheel=replace drift=replace cuda-runtime=block ready=keep"
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
$torchLockPath = Join-Path $repoRoot "providers\torch-runtime-lock.json"
$environmentLock = Join-Path $repoRoot "providers\diffusers_environment_lock.py"
$provider = Join-Path $repoRoot "providers\diffusers_video_provider.py"
$proofProvider = Join-Path $repoRoot "providers\modelscope_proof_provider.py"
$lowVramProvider = Join-Path $repoRoot "providers\modelscope_low_vram_provider.py"
$p0Preflight = Join-Path $repoRoot "tools\cinema_p0_preflight.py"
$service = Join-Path $repoRoot "tools\cinema_job_service.py"
$runner = Join-Path $repoRoot "tools\cinema_job_runner.py"
$ensurePython = Join-Path $repoRoot "scripts\ensure-python-on-d.ps1"
$reportPath = Join-Path $workspaceRoot "cinema-bootstrap-report.json"

foreach ($required in @($requirements, $torchLockPath, $environmentLock)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Required environment lock file not found: $required" }
}
$torchLock = Get-Content -LiteralPath $torchLockPath -Raw | ConvertFrom-Json
if ([string]$torchLock.schema -ne "echoes.torch-runtime-lock.v1") { throw "Unsupported Torch runtime lock schema." }
$lockedIndexUrl = [string]$torchLock.indexUrl
if (-not $lockedIndexUrl.StartsWith("https://download.pytorch.org/whl/")) { throw "Torch runtime lock must use the official PyTorch wheel index." }
if ($TorchIndexUrl -and $TorchIndexUrl -ne $lockedIndexUrl) {
    throw "TorchIndexUrl override does not match the pinned runtime lock. Expected: $lockedIndexUrl"
}
$TorchIndexUrl = $lockedIndexUrl

$storageDirectories = @(
    $workspaceRoot, $cacheRoot,
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
foreach ($directory in $storageDirectories) { New-Item -ItemType Directory -Path $directory -Force | Out-Null }

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
    } catch { return $false }
}

function Resolve-BasePython {
    if (Test-BasePython -Path $PythonExecutable) { return (Resolve-Path -LiteralPath $PythonExecutable).Path }
    if ($PythonLauncher -and $PythonLauncher -ne "py" -and (Test-BasePython -Path $PythonLauncher)) { return (Resolve-Path -LiteralPath $PythonLauncher).Path }
    if (-not (Test-Path $ensurePython -PathType Leaf)) { throw "Python resolver script not found: $ensurePython" }
    Write-Host "Resolving a real 64-bit Python 3.10/3.11 executable without trusting the stale py launcher."
    $resolvedOutput = & $ensurePython -InstallRoot "D:\A.I\Python310" -WorkspaceRoot $workspaceRoot
    if ($LASTEXITCODE -ne 0) { throw "Python discovery/installation failed" }
    $resolved = [string]($resolvedOutput | Select-Object -Last 1)
    if (-not (Test-BasePython -Path $resolved)) { throw "Python resolver returned an unusable executable: $resolved" }
    return (Resolve-Path -LiteralPath $resolved).Path
}

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { throw "FFmpeg is not available in PATH" }
if (-not (Get-Command ffprobe -ErrorAction SilentlyContinue)) { throw "FFprobe is not available in PATH" }

$basePython = Resolve-BasePython
Write-Host "Cinema workspace: $workspaceRoot"
Write-Host "Base Python: $basePython"
Write-Host "Virtual environment: $venv"
Write-Host "Package/model cache: $cacheRoot"
Write-Host "Temporary files: $tempRoot"
Write-Host "Pinned Torch runtime: torch=$($torchLock.torchVersion) torchvision=$($torchLock.torchvisionVersion) CUDA=$($torchLock.expectedTorchCudaVersion)"
Write-Host "Drive C: is not selected for Cinema storage."

if ($Recreate -and (Test-Path $venv)) { Remove-Item $venv -Recurse -Force }
if (-not (Test-Path $venv)) {
    Write-Host "Creating Cinema virtual environment: $venv"
    & $basePython -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "Python venv creation failed with exit code $LASTEXITCODE using $basePython" }
}
$python = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Virtual environment Python not found: $python" }

& $python -m pip install --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) { throw "pip bootstrap failed" }

$torchBefore = Get-TorchRuntimeProbe -PythonPath $python
$exactBefore = Test-ExactTorchRuntime -Probe $torchBefore -Lock $torchLock
$torchAction = Get-TorchRepairAction -Importable ([bool]$torchBefore.importable) -CudaBuild ([bool]$torchBefore.cudaBuild) -CudaAvailable ([bool]$torchBefore.cudaAvailable) -ExactRuntime $exactBefore
switch ($torchAction) {
    "READY" { Write-Host "Pinned CUDA PyTorch runtime is already ready." }
    "BLOCK_CUDA_RUNTIME" { Write-Host "Pinned CUDA wheel is installed, but the NVIDIA runtime is unavailable. Package reinstall is skipped." }
    "INSTALL_CUDA_WHEEL" { Write-Host "PyTorch is missing; installing the pinned official CUDA wheel." }
    "REPLACE_CPU_WHEEL" { Write-Host "CPU-only PyTorch detected. It will be replaced with the pinned official CUDA wheel." }
    "REPLACE_RUNTIME_DRIFT" { Write-Host "Torch runtime drift detected. It will be replaced with the exact pinned CUDA wheel set." }
}

if ($torchAction -in @("INSTALL_CUDA_WHEEL", "REPLACE_CPU_WHEEL", "REPLACE_RUNTIME_DRIFT")) {
    $installArguments = @("-m", "pip", "install", "--upgrade")
    if ($torchAction -ne "INSTALL_CUDA_WHEEL") { $installArguments += "--force-reinstall" }
    $installArguments += @(
        "torch==$($torchLock.torchVersion)",
        "torchvision==$($torchLock.torchvisionVersion)",
        "--index-url", $TorchIndexUrl
    )
    & $python @installArguments
    if ($LASTEXITCODE -ne 0) { throw "Pinned PyTorch CUDA installation failed" }
    $torchAfter = Get-TorchRuntimeProbe -PythonPath $python
    if (-not (Test-ExactTorchRuntime -Probe $torchAfter -Lock $torchLock)) {
        throw "Pinned Torch installation completed but runtime versions still differ. Torch=$($torchAfter.version) TorchVision=$($torchAfter.torchvisionVersion) CUDA=$($torchAfter.torchCudaVersion)"
    }
    Write-Host "Pinned CUDA runtime installed. Torch=$($torchAfter.version) TorchVision=$($torchAfter.torchvisionVersion) CUDA=$($torchAfter.torchCudaVersion) RuntimeAvailable=$($torchAfter.cudaAvailable)"
}

& $python -m pip install -r $requirements
if ($LASTEXITCODE -ne 0) { throw "Diffusers provider dependency installation failed" }

& $python -m py_compile $environmentLock $provider $proofProvider $lowVramProvider $p0Preflight $service $runner
if ($LASTEXITCODE -ne 0) { throw "Cinema provider/preflight/service Python compilation failed" }
& $python $environmentLock --allow-unavailable-cuda-runtime
if ($LASTEXITCODE -ne 0) { throw "Pinned Python/Torch environment verification failed" }
& $python $lowVramProvider --self-test
if ($LASTEXITCODE -ne 0) { throw "Active low-VRAM provider self-test failed" }
& $python $p0Preflight --self-test
if ($LASTEXITCODE -ne 0) { throw "Cinema P0 preflight self-test failed" }

$diagnosticScript = @'
import importlib.metadata
import json
import os
import platform
import shutil
import sys
from pathlib import Path

torch_lock = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
report = {
    "schema": "echoes.cinema-bootstrap-report.v3",
    "status": "RUNNING",
    "python": {"version": sys.version, "executable": sys.executable, "platform": platform.platform()},
    "tools": {"ffmpeg": shutil.which("ffmpeg"), "ffprobe": shutil.which("ffprobe")},
    "storage": {
        "hfHome": os.environ.get("HF_HOME"), "hfHubCache": os.environ.get("HF_HUB_CACHE"),
        "torchHome": os.environ.get("TORCH_HOME"), "pipCache": os.environ.get("PIP_CACHE_DIR"),
        "temp": os.environ.get("TEMP"), "pythonBytecode": os.environ.get("PYTHONPYCACHEPREFIX"),
        "systemDriveWritesAllowed": False,
    },
    "expectedTorchRuntime": torch_lock,
}
try:
    import torch
    import diffusers
    import transformers
    import accelerate
    import safetensors
    vision = importlib.metadata.version("torchvision")
    torch_base = str(torch.__version__).split("+", 1)[0]
    vision_base = str(vision).split("+", 1)[0]
    cuda_build = bool(torch.version.cuda)
    cuda_available = bool(torch.cuda.is_available())
    exact_runtime = (
        torch_base == str(torch_lock["torchVersion"])
        and vision_base == str(torch_lock["torchvisionVersion"])
        and str(torch.version.cuda or "") == str(torch_lock["expectedTorchCudaVersion"])
    )
    report["packages"] = {
        "torch": torch.__version__, "torchvision": vision, "diffusers": diffusers.__version__,
        "transformers": transformers.__version__, "accelerate": accelerate.__version__,
        "safetensors": safetensors.__version__,
    }
    report["cuda"] = {
        "buildPresent": cuda_build, "available": cuda_available,
        "deviceCount": torch.cuda.device_count() if cuda_available else 0,
        "deviceName": torch.cuda.get_device_name(0) if cuda_available else None,
        "torchCudaVersion": torch.version.cuda, "exactPinnedRuntime": exact_runtime,
    }
    storage_paths = [report["storage"][key] for key in ("hfHome", "hfHubCache", "torchHome", "pipCache", "temp", "pythonBytecode")]
    c_drive_used = any(str(path or "").lower().startswith("c:\\") for path in storage_paths)
    report["storage"]["cDriveSelected"] = c_drive_used
    if c_drive_used:
        report.update(status="FAILED", failureClass="SYSTEM_DRIVE_STORAGE", blocker="A Cinema cache or temporary path still targets drive C:")
    elif not exact_runtime:
        report.update(status="FAILED", failureClass="TORCH_RUNTIME_VERSION_DRIFT", blocker="Installed Torch runtime differs from the pinned lock")
    elif not cuda_build:
        report.update(status="FAILED", failureClass="CUDA_WHEEL_MISSING", blocker="PyTorch is not a CUDA-enabled build")
    elif not cuda_available:
        report.update(status="BLOCKED", failureClass="CUDA_RUNTIME_UNAVAILABLE", operatorRestartRequired=True, blocker="Pinned CUDA wheel is installed, but the NVIDIA driver/runtime is unavailable")
    else:
        report["status"] = "PASS"
except Exception as error:
    report.update(status="FAILED", failureClass="DEPENDENCY_IMPORT_FAILED", blocker=str(error))
print(json.dumps(report, indent=2))
'@

$diagnosticOutput = $diagnosticScript | & $python - $torchLockPath
if ($LASTEXITCODE -ne 0) { throw "Cinema diagnostic failed" }
$diagnosticOutput | Set-Content -Path $reportPath -Encoding utf8
$report = $diagnosticOutput | ConvertFrom-Json
Write-Host "Cinema bootstrap report: $reportPath"
Write-Host "Status: $($report.status)"
Write-Host "Python: $($report.python.version)"
Write-Host "Exact pinned Torch runtime: $($report.cuda.exactPinnedRuntime)"
Write-Host "CUDA available: $($report.cuda.available)"
Write-Host "GPU: $($report.cuda.deviceName)"
Write-Host "C drive selected by Cinema caches: $($report.storage.cDriveSelected)"
if ($report.status -ne "PASS") { throw "Cinema AI bootstrap is not ready [$($report.failureClass)]: $($report.blocker)" }
Write-Host "Echoes Cinema AI environment PASS"
Write-Host "Virtual environment Python: $python"
