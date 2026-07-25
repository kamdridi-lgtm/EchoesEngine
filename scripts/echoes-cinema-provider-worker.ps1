param(
    [string]$WorkspaceRoot = "D:\A.I\EchoesCinema",
    [string]$RepoRoot = "",
    [int]$ProviderPort = 8081,
    [string]$TorchIndexUrl = "https://download.pytorch.org/whl/cu128",
    [string]$ProviderMode = "",
    [switch]$EnvironmentProbeSelfTest
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

function Get-FreeGiB {
    param([string]$Path)
    try {
        $root = [System.IO.Path]::GetPathRoot([System.IO.Path]::GetFullPath($Path))
        $drive = New-Object System.IO.DriveInfo($root)
        return [math]::Round($drive.AvailableFreeSpace / 1GB, 2)
    } catch {
        return $null
    }
}

function Get-CinemaEnvironmentAction {
    param(
        [bool]$DependenciesReady,
        [bool]$CudaBuild,
        [bool]$CudaAvailable
    )
    if (-not $DependenciesReady) { return "REPAIR_DEPENDENCIES" }
    if (-not $CudaBuild) { return "REPLACE_CPU_TORCH" }
    if (-not $CudaAvailable) { return "BLOCK_CUDA_RUNTIME" }
    return "READY"
}

function Test-CinemaEnvironment {
    param([string]$PythonPath)
    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        return [pscustomobject]@{
            healthy = $false
            retryable = $true
            failureClass = "PYTHON_ENVIRONMENT_MISSING"
            operatorRestartRequired = $false
            reason = "Cinema virtual-environment Python is missing: $PythonPath"
            torchVersion = $null
            torchCudaVersion = $null
        }
    }

    $probeScript = @'
import json
result = {
    "dependenciesReady": False,
    "cudaBuild": False,
    "cudaAvailable": False,
    "torchVersion": None,
    "torchCudaVersion": None,
    "deviceName": None,
    "error": None,
}
try:
    import torch
    import diffusers
    import transformers
    import accelerate
    import safetensors
    result["dependenciesReady"] = True
    result["torchVersion"] = str(torch.__version__)
    result["torchCudaVersion"] = str(torch.version.cuda) if torch.version.cuda else None
    result["cudaBuild"] = bool(torch.version.cuda)
    result["cudaAvailable"] = bool(torch.cuda.is_available())
    if result["cudaAvailable"]:
        result["deviceName"] = torch.cuda.get_device_name(0)
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
            healthy = $false
            retryable = $true
            failureClass = "DEPENDENCY_PROBE_FAILED"
            operatorRestartRequired = $false
            reason = ([string]$raw).Trim()
            torchVersion = $null
            torchCudaVersion = $null
        }
    }

    try {
        $probe = ([string]$raw).Trim() | ConvertFrom-Json
    } catch {
        return [pscustomobject]@{
            healthy = $false
            retryable = $true
            failureClass = "DEPENDENCY_PROBE_INVALID_JSON"
            operatorRestartRequired = $false
            reason = "Cinema dependency probe returned invalid JSON: $raw"
            torchVersion = $null
            torchCudaVersion = $null
        }
    }

    $action = Get-CinemaEnvironmentAction `
        -DependenciesReady ([bool]$probe.dependenciesReady) `
        -CudaBuild ([bool]$probe.cudaBuild) `
        -CudaAvailable ([bool]$probe.cudaAvailable)

    switch ($action) {
        "READY" {
            return [pscustomobject]@{
                healthy = $true
                retryable = $false
                failureClass = $null
                operatorRestartRequired = $false
                reason = "CUDA/Diffusers environment is healthy on $($probe.deviceName)"
                torchVersion = $probe.torchVersion
                torchCudaVersion = $probe.torchCudaVersion
            }
        }
        "BLOCK_CUDA_RUNTIME" {
            return [pscustomobject]@{
                healthy = $false
                retryable = $false
                failureClass = "CUDA_RUNTIME_UNAVAILABLE"
                operatorRestartRequired = $true
                reason = "CUDA-enabled PyTorch $($probe.torchVersion) (CUDA $($probe.torchCudaVersion)) is installed, but torch.cuda.is_available() is false. The NVIDIA driver/runtime or a pending restart is blocking the GPU."
                torchVersion = $probe.torchVersion
                torchCudaVersion = $probe.torchCudaVersion
            }
        }
        "REPLACE_CPU_TORCH" {
            return [pscustomobject]@{
                healthy = $false
                retryable = $true
                failureClass = "CPU_ONLY_TORCH"
                operatorRestartRequired = $false
                reason = "CPU-only PyTorch $($probe.torchVersion) is installed and must be replaced by the official CUDA wheel."
                torchVersion = $probe.torchVersion
                torchCudaVersion = $null
            }
        }
        default {
            return [pscustomobject]@{
                healthy = $false
                retryable = $true
                failureClass = "DEPENDENCY_MISSING"
                operatorRestartRequired = $false
                reason = if ($probe.error) { [string]$probe.error } else { "Torch/Diffusers dependencies are incomplete." }
                torchVersion = $probe.torchVersion
                torchCudaVersion = $probe.torchCudaVersion
            }
        }
    }
}

if ($EnvironmentProbeSelfTest) {
    if ((Get-CinemaEnvironmentAction -DependenciesReady $false -CudaBuild $false -CudaAvailable $false) -ne "REPAIR_DEPENDENCIES") {
        throw "Dependency repair classification failed."
    }
    if ((Get-CinemaEnvironmentAction -DependenciesReady $true -CudaBuild $false -CudaAvailable $false) -ne "REPLACE_CPU_TORCH") {
        throw "CPU torch replacement classification failed."
    }
    if ((Get-CinemaEnvironmentAction -DependenciesReady $true -CudaBuild $true -CudaAvailable $false) -ne "BLOCK_CUDA_RUNTIME") {
        throw "CUDA runtime blocker classification failed."
    }
    if ((Get-CinemaEnvironmentAction -DependenciesReady $true -CudaBuild $true -CudaAvailable $true) -ne "READY") {
        throw "CUDA ready classification failed."
    }
    Write-Host "Echoes Cinema environment probe PASS dependencies=repair cpu-torch=replace cuda-runtime=block ready=run"
    exit 0
}

function Stop-ChildProcess {
    param([System.Diagnostics.Process]$Process)
    if (-not $Process) { return }
    try {
        if (-not $Process.HasExited) {
            Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
            Wait-Process -Id $Process.Id -Timeout 10 -ErrorAction SilentlyContinue
        }
    } catch { }
}

function Wait-PortReleased {
    param([int]$Port, [int]$TimeoutSeconds = 15)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $listener = $null
        try {
            $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
            $listener.Start()
            return $true
        } catch {
            Start-Sleep -Milliseconds 300
        } finally {
            if ($listener) { $listener.Stop() }
        }
    }
    return $false
}

function Wait-ProviderProcess {
    param(
        [System.Diagnostics.Process]$Process,
        [string]$StatusPath,
        [string]$Mode,
        [int]$Port,
        [string]$StdoutLog,
        [string]$StderrLog,
        [string]$Workspace,
        [string]$PidPath
    )
    Set-Content -LiteralPath $PidPath -Value $Process.Id -Encoding ascii
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
if (-not $env:ECHOES_RENDER_TOKEN) { throw "ECHOES_RENDER_TOKEN is missing from the provider worker environment." }
if ($ProviderPort -le 0 -or $ProviderPort -gt 65535) { throw "ProviderPort must be between 1 and 65535." }

$resolvedMode = if ($ProviderMode) { $ProviderMode } elseif ($env:ECHOES_CINEMA_PROVIDER_MODE) { $env:ECHOES_CINEMA_PROVIDER_MODE } else { "real" }
$resolvedMode = $resolvedMode.Trim().ToLowerInvariant()
if ($resolvedMode -notin @("real", "mock-contract")) { throw "ProviderMode must be real or mock-contract. Current value: $resolvedMode" }

$runtimeRoot = Join-Path $workspace "runtime"
$logsRoot = Join-Path $workspace "logs"
$cacheRoot = Join-Path $workspace "cache"
$tempRoot = Join-Path $workspace "temp"
$venvRoot = Join-Path $workspace ".venv-cinema"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
$fallbackPython = "D:\A.I\Python310\python.exe"
$statusPath = Join-Path $runtimeRoot "provider-worker-status.json"
$pidPath = Join-Path $runtimeRoot "provider.pid"
$stopSignalPath = Join-Path $runtimeRoot "stop.signal"
$provider = Join-Path $RepoRoot "providers\modelscope_low_vram_provider.py"
$bridge = Join-Path $RepoRoot "providers\provider_bootstrap_health_bridge.py"
$mockProvider = Join-Path $RepoRoot "tests\mock_health_provider.py"
$bootstrap = Join-Path $RepoRoot "scripts\bootstrap-cinema-ai.ps1"

foreach ($directory in @(
    $workspace, $runtimeRoot, $logsRoot, $cacheRoot,
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
)) { New-Item -ItemType Directory -Path $directory -Force | Out-Null }

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
$bridgeProcess = $null
$bridgeStdout = ""
$bridgeStderr = ""
try {
    Write-AtomicJson -Path $statusPath -Payload @{
        schema = "echoes.cinema-provider-worker.v1"
        status = "PREPARING"
        timestampUtc = [DateTime]::UtcNow.ToString("o")
        providerMode = $resolvedMode
        providerPort = $ProviderPort
        workspace = $workspace
        workspaceFreeGiB = Get-FreeGiB -Path $workspace
        minimumFreeGiB = 20
        recoveryCount = 0
        systemDriveWritesAllowed = $false
    }

    if ($resolvedMode -eq "mock-contract") {
        $mockPython = if (Test-Path -LiteralPath $venvPython -PathType Leaf) { $venvPython } elseif (Test-Path -LiteralPath $fallbackPython -PathType Leaf) { $fallbackPython } else { "python" }
        if (-not (Test-Path -LiteralPath $mockProvider -PathType Leaf)) { throw "Mock contract provider not found: $mockProvider" }
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $stdoutLog = Join-Path $logsRoot "provider-mock-$stamp.log"
        $stderrLog = Join-Path $logsRoot "provider-mock-$stamp.error.log"
        $arguments = @($mockProvider, "--host", "127.0.0.1", "--port", "$ProviderPort")
        $providerProcess = Start-Process -FilePath $mockPython -ArgumentList $arguments -WorkingDirectory $workspace -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog -WindowStyle Hidden -PassThru
        exit (Wait-ProviderProcess -Process $providerProcess -StatusPath $statusPath -Mode $resolvedMode -Port $ProviderPort -StdoutLog $stdoutLog -StderrLog $stderrLog -Workspace $workspace -PidPath $pidPath)
    }

    if (-not (Test-Path -LiteralPath $bridge -PathType Leaf)) { throw "Provider bootstrap health bridge not found: $bridge" }
    $bridgePython = if (Test-Path -LiteralPath $fallbackPython -PathType Leaf) { $fallbackPython } elseif (Test-Path -LiteralPath $venvPython -PathType Leaf) { $venvPython } else { throw "No D-drive Python is available for the bootstrap health bridge." }
    $bridgeStamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $bridgeStdout = Join-Path $logsRoot "provider-bootstrap-bridge-$bridgeStamp.log"
    $bridgeStderr = Join-Path $logsRoot "provider-bootstrap-bridge-$bridgeStamp.error.log"
    $bridgeArguments = @($bridge, "--host", "127.0.0.1", "--port", "$ProviderPort", "--status-file", $statusPath)
    $bridgeProcess = Start-Process -FilePath $bridgePython -ArgumentList $bridgeArguments -WorkingDirectory $workspace -RedirectStandardOutput $bridgeStdout -RedirectStandardError $bridgeStderr -WindowStyle Hidden -PassThru
    Set-Content -LiteralPath $pidPath -Value $bridgeProcess.Id -Encoding ascii
    Start-Sleep -Milliseconds 800
    if ($bridgeProcess.HasExited) {
        $tail = if (Test-Path -LiteralPath $bridgeStderr) { Get-Content -LiteralPath $bridgeStderr -Tail 30 | Out-String } else { "no bridge error log" }
        throw "Provider bootstrap health bridge failed to start. $tail"
    }

    if (-not (Test-Path -LiteralPath $bootstrap -PathType Leaf)) { throw "Cinema bootstrap not found: $bootstrap" }
    $recoveryCount = 0
    $retrySeconds = 15
    while ($true) {
        $environment = Test-CinemaEnvironment -PythonPath $venvPython
        if ($environment.healthy) { break }
        if (-not [bool]$environment.retryable) {
            Write-AtomicJson -Path $statusPath -Payload @{
                schema = "echoes.cinema-provider-worker.v1"
                status = "BLOCKED"
                timestampUtc = [DateTime]::UtcNow.ToString("o")
                providerMode = $resolvedMode
                providerPort = $ProviderPort
                error = $environment.reason
                failureClass = $environment.failureClass
                retryable = $false
                automaticRetry = $false
                operatorRestartRequired = [bool]$environment.operatorRestartRequired
                operatorAction = "A CUDA wheel is installed, but the NVIDIA runtime is unavailable. Update/restart the NVIDIA driver or reboot Windows; models, caches, jobs, and proofs are preserved."
                torchVersion = $environment.torchVersion
                torchCudaVersion = $environment.torchCudaVersion
                workspace = $workspace
                workspaceFreeGiB = Get-FreeGiB -Path $workspace
                minimumFreeGiB = 20
                recoveryCount = $recoveryCount
                stdoutLog = $bridgeStdout
                stderrLog = $bridgeStderr
                systemDriveWritesAllowed = $false
            }
            while (-not (Test-Path -LiteralPath $stopSignalPath)) {
                if ($bridgeProcess.HasExited) { throw "Provider bridge exited while reporting a permanent CUDA runtime blocker." }
                Start-Sleep -Seconds 5
            }
            exit 0
        }

        $recoveryCount++
        $attemptUtc = [DateTime]::UtcNow
        Write-AtomicJson -Path $statusPath -Payload @{
            schema = "echoes.cinema-provider-worker.v1"
            status = "BOOTSTRAPPING"
            timestampUtc = $attemptUtc.ToString("o")
            providerMode = $resolvedMode
            providerPort = $ProviderPort
            bootstrapReason = $environment.reason
            failureClass = $environment.failureClass
            workspace = $workspace
            workspaceFreeGiB = Get-FreeGiB -Path $workspace
            minimumFreeGiB = 20
            recoveryCount = $recoveryCount
            stdoutLog = $bridgeStdout
            stderrLog = $bridgeStderr
            systemDriveWritesAllowed = $false
        }
        $bootstrapError = ""
        try {
            $bootstrapArguments = @(
                "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", $bootstrap,
                "-VenvPath", $venvRoot,
                "-TorchIndexUrl", $TorchIndexUrl
            )
            $bootstrapProcess = Start-Process -FilePath "powershell.exe" -ArgumentList $bootstrapArguments -WindowStyle Hidden -Wait -PassThru
            $bootstrapExit = $bootstrapProcess.ExitCode
        } catch {
            $bootstrapExit = 1
            $bootstrapError = $_.Exception.Message
        }

        $environment = Test-CinemaEnvironment -PythonPath $venvPython
        if ($environment.healthy) { break }
        if (-not [bool]$environment.retryable) {
            continue
        }

        $reason = if ($bootstrapError) { $bootstrapError } elseif (-not $environment.healthy) { $environment.reason } else { "Cinema bootstrap failed with exit code $bootstrapExit" }
        $nextRetry = [DateTime]::UtcNow.AddSeconds($retrySeconds)
        Write-AtomicJson -Path $statusPath -Payload @{
            schema = "echoes.cinema-provider-worker.v1"
            status = "RETRY_WAIT"
            timestampUtc = [DateTime]::UtcNow.ToString("o")
            providerMode = $resolvedMode
            providerPort = $ProviderPort
            error = $reason
            failureClass = $environment.failureClass
            retryable = $true
            automaticRetry = $true
            operatorRestartRequired = $false
            operatorAction = "No action is required. Echoes Cinema will retry the D-drive AI bootstrap automatically."
            nextRetryUtc = $nextRetry.ToString("o")
            workspace = $workspace
            workspaceFreeGiB = Get-FreeGiB -Path $workspace
            minimumFreeGiB = 20
            recoveryCount = $recoveryCount
            stdoutLog = $bridgeStdout
            stderrLog = $bridgeStderr
            systemDriveWritesAllowed = $false
        }
        Start-Sleep -Seconds $retrySeconds
        $retrySeconds = [math]::Min(120, [math]::Max(15, $retrySeconds * 2))
    }

    if (-not (Test-Path -LiteralPath $provider -PathType Leaf)) { throw "Low-VRAM provider not found: $provider" }
    Write-AtomicJson -Path $statusPath -Payload @{
        schema = "echoes.cinema-provider-worker.v1"
        status = "MODEL_LOADING"
        timestampUtc = [DateTime]::UtcNow.ToString("o")
        providerMode = $resolvedMode
        providerPort = $ProviderPort
        workspace = $workspace
        workspaceFreeGiB = Get-FreeGiB -Path $workspace
        minimumFreeGiB = 20
        recoveryCount = $recoveryCount
        systemDriveWritesAllowed = $false
    }

    Stop-ChildProcess -Process $bridgeProcess
    $bridgeProcess = $null
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
    if (-not (Wait-PortReleased -Port $ProviderPort -TimeoutSeconds 15)) { throw "Provider port $ProviderPort did not become available after bootstrap bridge shutdown." }

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
    $providerProcess = Start-Process -FilePath $venvPython -ArgumentList $arguments -WorkingDirectory $workspace -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog -WindowStyle Hidden -PassThru
    exit (Wait-ProviderProcess -Process $providerProcess -StatusPath $statusPath -Mode $resolvedMode -Port $ProviderPort -StdoutLog $stdoutLog -StderrLog $stderrLog -Workspace $workspace -PidPath $pidPath)
}
catch {
    Write-AtomicJson -Path $statusPath -Payload @{
        schema = "echoes.cinema-provider-worker.v1"
        status = "BROKEN"
        timestampUtc = [DateTime]::UtcNow.ToString("o")
        providerMode = $resolvedMode
        providerPort = $ProviderPort
        error = $_.Exception.Message
        failureClass = "PYTHON_RUNTIME_BLOCKER"
        retryable = $false
        automaticRetry = $false
        operatorRestartRequired = $false
        operatorAction = "Inspect the provider-worker error log. The supervisor will preserve the control center."
        workspace = $workspace
        workspaceFreeGiB = Get-FreeGiB -Path $workspace
        minimumFreeGiB = 20
        stdoutLog = $bridgeStdout
        stderrLog = $bridgeStderr
        systemDriveWritesAllowed = $false
    }
    Write-Error $_
    exit 1
}
finally {
    Stop-ChildProcess -Process $bridgeProcess
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
}
