param(
    [string]$WorkspaceRoot = "D:\A.I\EchoesCinema",
    [string]$RepoRoot = "",
    [int]$ProviderPort = 8081,
    [string]$TorchIndexUrl = "https://download.pytorch.org/whl/cu128",
    [string]$ProviderMode = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-AtomicJson {
    param([string]$Path, [hashtable]$Payload)
    $directory = Split-Path -Parent $Path
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $temporary = "$Path.$PID.tmp"
    $Payload | ConvertTo-Json -Depth 16 | Set-Content -LiteralPath $temporary -Encoding utf8
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

function Test-CinemaEnvironment {
    param([string]$PythonPath)
    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        return @{
            healthy = $false
            reason = "Cinema virtual-environment Python is missing: $PythonPath"
        }
    }

    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "SilentlyContinue"
        $probe = & $PythonPath -c "import torch, diffusers, transformers, accelerate, safetensors; assert torch.cuda.is_available(), 'CUDA unavailable'; print(torch.__version__)" 2>&1 | Out-String
        $exitCode = $LASTEXITCODE
    } catch {
        $probe = $_.Exception.Message
        $exitCode = 1
    } finally {
        $ErrorActionPreference = $previousPreference
    }

    $detail = ([string]$probe).Trim()
    if ($detail.Length -gt 4000) { $detail = $detail.Substring($detail.Length - 4000) }
    return @{
        healthy = ($exitCode -eq 0)
        reason = if ($exitCode -eq 0) { "CUDA/Diffusers environment is healthy" } elseif ($detail) { $detail } else { "CUDA/Diffusers import probe failed with exit code $exitCode" }
    }
}

function Wait-ProviderProcess {
    param(
        [System.Diagnostics.Process]$Process,
        [string]$StatusPath,
        [string]$Mode,
        [int]$Port,
        [string]$StdoutLog,
        [string]$StderrLog,
        [string]$Workspace
    )

    Set-Content -LiteralPath $pidPath -Value $Process.Id -Encoding ascii
    Write-AtomicJson -Path $StatusPath -Payload @{
        schema = "echoes.cinema-provider-worker.v1"
        status = "RUNNING"
        timestampUtc = [DateTime]::UtcNow.ToString("o")
        providerMode = $Mode
        providerPort = $Port
        providerPid = $Process.Id
        stdoutLog = $StdoutLog
        stderrLog = $StderrLog
        workspace = $Workspace
        systemDriveWritesAllowed = $false
    }

    Wait-Process -Id $Process.Id
    $Process.Refresh()
    $exitCode = $Process.ExitCode
    Write-AtomicJson -Path $StatusPath -Payload @{
        schema = "echoes.cinema-provider-worker.v1"
        status = if ($exitCode -eq 0) { "STOPPED" } else { "BROKEN" }
        timestampUtc = [DateTime]::UtcNow.ToString("o")
        providerMode = $Mode
        providerPort = $Port
        providerPid = $Process.Id
        exitCode = $exitCode
        stdoutLog = $StdoutLog
        stderrLog = $StderrLog
        workspace = $Workspace
        systemDriveWritesAllowed = $false
    }
    return $exitCode
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

$resolvedMode = if ($ProviderMode) { $ProviderMode } elseif ($env:ECHOES_CINEMA_PROVIDER_MODE) { $env:ECHOES_CINEMA_PROVIDER_MODE } else { "real" }
$resolvedMode = $resolvedMode.Trim().ToLowerInvariant()
if ($resolvedMode -notin @("real", "mock-contract")) {
    throw "ProviderMode must be real or mock-contract. Current value: $resolvedMode"
}

$runtimeRoot = Join-Path $workspace "runtime"
$logsRoot = Join-Path $workspace "logs"
$cacheRoot = Join-Path $workspace "cache"
$tempRoot = Join-Path $workspace "temp"
$venvRoot = Join-Path $workspace ".venv-cinema"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
$fallbackPython = "D:\A.I\Python310\python.exe"
$statusPath = Join-Path $runtimeRoot "provider-worker-status.json"
$pidPath = Join-Path $runtimeRoot "provider.pid"
$provider = Join-Path $RepoRoot "providers\modelscope_low_vram_provider.py"
$mockProvider = Join-Path $RepoRoot "tests\mock_render_provider.py"
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
        providerMode = $resolvedMode
        providerPort = $ProviderPort
        workspace = $workspace
        systemDriveWritesAllowed = $false
    }

    if ($resolvedMode -eq "mock-contract") {
        $mockPython = if (Test-Path -LiteralPath $venvPython -PathType Leaf) { $venvPython } elseif (Test-Path -LiteralPath $fallbackPython -PathType Leaf) { $fallbackPython } else { "python" }
        if (-not (Test-Path -LiteralPath $mockProvider -PathType Leaf)) {
            throw "Mock contract provider not found: $mockProvider"
        }
        if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
            throw "FFmpeg is required by the mock contract provider"
        }

        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $stdoutLog = Join-Path $logsRoot "provider-mock-$stamp.log"
        $stderrLog = Join-Path $logsRoot "provider-mock-$stamp.error.log"
        $arguments = @(
            $mockProvider,
            "--host", "127.0.0.1",
            "--port", "$ProviderPort",
            "--width", "320",
            "--height", "180",
            "--fps", "12"
        )
        $providerProcess = Start-Process -FilePath $mockPython -ArgumentList $arguments -WorkingDirectory $workspace -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog -PassThru
        $exitCode = Wait-ProviderProcess -Process $providerProcess -StatusPath $statusPath -Mode $resolvedMode -Port $ProviderPort -StdoutLog $stdoutLog -StderrLog $stderrLog -Workspace $workspace
        exit $exitCode
    }

    if (-not (Test-Path -LiteralPath $bootstrap -PathType Leaf)) {
        throw "Cinema bootstrap not found: $bootstrap"
    }
    $environment = Test-CinemaEnvironment -PythonPath $venvPython
    $bootstrapAttempted = $false
    if (-not $environment.healthy) {
        $bootstrapAttempted = $true
        Write-AtomicJson -Path $statusPath -Payload @{
            schema = "echoes.cinema-provider-worker.v1"
            status = "BOOTSTRAPPING"
            timestampUtc = [DateTime]::UtcNow.ToString("o")
            providerMode = $resolvedMode
            providerPort = $ProviderPort
            bootstrapReason = $environment.reason
            workspace = $workspace
            systemDriveWritesAllowed = $false
        }
        & powershell -NoProfile -ExecutionPolicy Bypass -File $bootstrap -VenvPath $venvRoot -TorchIndexUrl $TorchIndexUrl
        if ($LASTEXITCODE -ne 0) {
            throw "Cinema bootstrap failed with exit code $LASTEXITCODE. Initial environment blocker: $($environment.reason)"
        }
        $environment = Test-CinemaEnvironment -PythonPath $venvPython
    }
    if (-not $environment.healthy) {
        throw "Cinema environment is still unhealthy after bootstrap. $($environment.reason)"
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
        providerMode = $resolvedMode
        providerPort = $ProviderPort
        bootstrapAttempted = $bootstrapAttempted
        stdoutLog = $stdoutLog
        stderrLog = $stderrLog
        workspace = $workspace
        systemDriveWritesAllowed = $false
    }

    $providerProcess = Start-Process -FilePath $venvPython -ArgumentList $arguments -WorkingDirectory $workspace -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog -PassThru
    $exitCode = Wait-ProviderProcess -Process $providerProcess -StatusPath $statusPath -Mode $resolvedMode -Port $ProviderPort -StdoutLog $stdoutLog -StderrLog $stderrLog -Workspace $workspace
    exit $exitCode
}
catch {
    Write-AtomicJson -Path $statusPath -Payload @{
        schema = "echoes.cinema-provider-worker.v1"
        status = "BROKEN"
        timestampUtc = [DateTime]::UtcNow.ToString("o")
        providerMode = $resolvedMode
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
