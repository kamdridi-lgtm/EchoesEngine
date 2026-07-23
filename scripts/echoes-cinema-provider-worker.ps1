param(
    [string]$WorkspaceRoot = "D:\A.I\EchoesCinema",
    [string]$RepoRoot = "",
    [int]$ProviderPort = 8081,
    [string]$TorchIndexUrl = "https://download.pytorch.org/whl/cu128"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-AtomicJson {
    param([string]$Path, [hashtable]$Payload)
    $directory = Split-Path -Parent $Path
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $temporary = "$Path.$PID.tmp"
    $Payload | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $temporary -Encoding utf8
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
} else {
    $RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
}
$workspace = [System.IO.Path]::GetFullPath($WorkspaceRoot)
if ([System.IO.Path]::GetPathRoot($workspace).TrimEnd("\").ToUpperInvariant() -eq "C:") {
    throw "Echoes Cinema provider storage must not use drive C:."
}
if (-not $env:ECHOES_RENDER_TOKEN) {
    throw "ECHOES_RENDER_TOKEN is missing from the provider worker environment."
}
if ($ProviderPort -le 0 -or $ProviderPort -gt 65535) {
    throw "ProviderPort must be between 1 and 65535."
}

$runtimeRoot = Join-Path $workspace "runtime"
$logsRoot = Join-Path $workspace "logs"
$cacheRoot = Join-Path $workspace "cache"
$tempRoot = Join-Path $workspace "temp"
$venvRoot = Join-Path $workspace ".venv-cinema"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
$statusPath = Join-Path $runtimeRoot "provider-worker-status.json"
$pidPath = Join-Path $runtimeRoot "provider.pid"
$provider = Join-Path $RepoRoot "providers\modelscope_low_vram_provider.py"
$bootstrap = Join-Path $RepoRoot "scripts\bootstrap-cinema-ai.ps1"

foreach ($directory in @(
    $workspace,
    $runtimeRoot,
    $logsRoot,
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
)) {
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

$providerProcess = $null
try {
    Write-AtomicJson -Path $statusPath -Payload @{
        schema = "echoes.cinema-provider-worker.v1"
        status = "PREPARING"
        timestampUtc = [DateTime]::UtcNow.ToString("o")
        providerPort = $ProviderPort
        workspace = $workspace
        systemDriveWritesAllowed = $false
    }

    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        if (-not (Test-Path -LiteralPath $bootstrap -PathType Leaf)) {
            throw "Cinema bootstrap not found: $bootstrap"
        }
        Write-AtomicJson -Path $statusPath -Payload @{
            schema = "echoes.cinema-provider-worker.v1"
            status = "BOOTSTRAPPING"
            timestampUtc = [DateTime]::UtcNow.ToString("o")
            providerPort = $ProviderPort
            workspace = $workspace
            systemDriveWritesAllowed = $false
        }
        & powershell -NoProfile -ExecutionPolicy Bypass -File $bootstrap -VenvPath $venvRoot -TorchIndexUrl $TorchIndexUrl
        if ($LASTEXITCODE -ne 0) {
            throw "Cinema bootstrap failed with exit code $LASTEXITCODE"
        }
    }

    & $venvPython -c "import torch, diffusers, transformers, accelerate; assert torch.cuda.is_available(), 'CUDA unavailable'"
    if ($LASTEXITCODE -ne 0) {
        throw "Cinema Python exists but CUDA/Diffusers imports are not healthy."
    }
    if (-not (Test-Path -LiteralPath $provider -PathType Leaf)) {
        throw "Low-VRAM provider not found: $provider"
    }

    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $stdoutLog = Join-Path $logsRoot "provider-$stamp.log"
    $stderrLog = Join-Path $logsRoot "provider-$stamp.error.log"
    $arguments = @(
        $provider,
        "--host", "127.0.0.1",
        "--port", "$ProviderPort",
        "--device", "cuda",
        "--width", "384",
        "--height", "216",
        "--fps", "4",
        "--inference-steps", "15",
        "--max-frames", "16"
    )

    Write-AtomicJson -Path $statusPath -Payload @{
        schema = "echoes.cinema-provider-worker.v1"
        status = "MODEL_LOADING"
        timestampUtc = [DateTime]::UtcNow.ToString("o")
        providerPort = $ProviderPort
        stdoutLog = $stdoutLog
        stderrLog = $stderrLog
        workspace = $workspace
        systemDriveWritesAllowed = $false
    }

    $providerProcess = Start-Process -FilePath $venvPython -ArgumentList $arguments -WorkingDirectory $workspace -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog -PassThru
    Set-Content -LiteralPath $pidPath -Value $providerProcess.Id -Encoding ascii
    Write-AtomicJson -Path $statusPath -Payload @{
        schema = "echoes.cinema-provider-worker.v1"
        status = "RUNNING"
        timestampUtc = [DateTime]::UtcNow.ToString("o")
        providerPort = $ProviderPort
        providerPid = $providerProcess.Id
        stdoutLog = $stdoutLog
        stderrLog = $stderrLog
        workspace = $workspace
        systemDriveWritesAllowed = $false
    }

    Wait-Process -Id $providerProcess.Id
    $providerProcess.Refresh()
    $exitCode = $providerProcess.ExitCode
    Write-AtomicJson -Path $statusPath -Payload @{
        schema = "echoes.cinema-provider-worker.v1"
        status = if ($exitCode -eq 0) { "STOPPED" } else { "BROKEN" }
        timestampUtc = [DateTime]::UtcNow.ToString("o")
        providerPort = $ProviderPort
        providerPid = $providerProcess.Id
        exitCode = $exitCode
        stdoutLog = $stdoutLog
        stderrLog = $stderrLog
        workspace = $workspace
        systemDriveWritesAllowed = $false
    }
    exit $exitCode
}
catch {
    Write-AtomicJson -Path $statusPath -Payload @{
        schema = "echoes.cinema-provider-worker.v1"
        status = "BROKEN"
        timestampUtc = [DateTime]::UtcNow.ToString("o")
        providerPort = $ProviderPort
        error = $_.Exception.Message
        workspace = $workspace
        systemDriveWritesAllowed = $false
    }
    Write-Error $_
    exit 1
}
finally {
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
}
