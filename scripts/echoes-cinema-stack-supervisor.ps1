param(
    [string]$WorkspaceRoot = "D:\A.I\EchoesCinema",
    [string]$RepoRoot = "",
    [int]$PreferredServicePort = 8090,
    [int]$PreferredProviderPort = 8081,
    [int]$MaxWorkers = 1,
    [double]$StorageReserveGiB = 20,
    [double]$DefaultJobGiB = 8,
    [double]$MaxJobGiB = 200,
    [string]$ProviderMode = "",
    [int]$DashboardFailureThreshold = 3,
    [switch]$DashboardRecoverySelfTest
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

function New-RandomToken {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return -join ($bytes | ForEach-Object { $_.ToString("x2") })
}

function Test-PortAvailable {
    param([int]$Port)
    $listener = $null
    try {
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
        $listener.Start()
        return $true
    } catch {
        return $false
    } finally {
        if ($listener) { $listener.Stop() }
    }
}

function Find-FreePort {
    param([int]$Preferred, [int]$Span = 20)
    for ($port = $Preferred; $port -lt ($Preferred + $Span); $port++) {
        if (Test-PortAvailable -Port $port) { return $port }
    }
    throw "No free localhost port was found between $Preferred and $($Preferred + $Span - 1)."
}

function Test-DashboardReady {
    param([string]$Url)
    if (-not $Url) { return $false }
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
        return $response.StatusCode -eq 200 -and $response.Content -like "*ECHOES CINEMA*"
    } catch {
        return $false
    }
}

function Get-DashboardRecoveryAction {
    param(
        [bool]$ProcessAlive,
        [bool]$DashboardHealthy,
        [int]$ConsecutiveFailures,
        [int]$FailureThreshold
    )
    $threshold = [math]::Max(1, $FailureThreshold)
    if (-not $ProcessAlive) { return "RESTART_EXITED" }
    if ($DashboardHealthy) { return "HEALTHY" }
    if ($ConsecutiveFailures -ge $threshold) { return "RESTART_UNRESPONSIVE" }
    return "WAIT_UNRESPONSIVE"
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

if ($DashboardRecoverySelfTest) {
    if ((Get-DashboardRecoveryAction -ProcessAlive $false -DashboardHealthy $false -ConsecutiveFailures 0 -FailureThreshold 3) -ne "RESTART_EXITED") {
        throw "Exited process recovery contract failed."
    }
    if ((Get-DashboardRecoveryAction -ProcessAlive $true -DashboardHealthy $true -ConsecutiveFailures 9 -FailureThreshold 3) -ne "HEALTHY") {
        throw "Healthy dashboard recovery contract failed."
    }
    if ((Get-DashboardRecoveryAction -ProcessAlive $true -DashboardHealthy $false -ConsecutiveFailures 2 -FailureThreshold 3) -ne "WAIT_UNRESPONSIVE") {
        throw "Unresponsive grace-window contract failed."
    }
    if ((Get-DashboardRecoveryAction -ProcessAlive $true -DashboardHealthy $false -ConsecutiveFailures 3 -FailureThreshold 3) -ne "RESTART_UNRESPONSIVE") {
        throw "Unresponsive restart threshold contract failed."
    }
    Write-Host "Echoes Cinema dashboard recovery PASS exited=restart healthy=keep unresponsive=threshold-restart"
    exit 0
}

if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
} else {
    $RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
}
$workspace = [System.IO.Path]::GetFullPath($WorkspaceRoot)
$workspaceDrive = [System.IO.Path]::GetPathRoot($workspace)
if (-not $workspaceDrive -or $workspaceDrive.TrimEnd("\").ToUpperInvariant() -eq "C:") {
    throw "Echoes Cinema stack refuses workspace storage on drive C:. Current: $workspace"
}
if ($MaxWorkers -le 0) { throw "MaxWorkers must be positive." }
if ($DashboardFailureThreshold -le 0) { throw "DashboardFailureThreshold must be positive." }

$resolvedProviderMode = if ($ProviderMode) { $ProviderMode } elseif ($env:ECHOES_CINEMA_PROVIDER_MODE) { $env:ECHOES_CINEMA_PROVIDER_MODE } else { "real" }
$resolvedProviderMode = $resolvedProviderMode.Trim().ToLowerInvariant()
if ($resolvedProviderMode -notin @("real", "mock-contract")) {
    throw "ProviderMode must be real or mock-contract. Current value: $resolvedProviderMode"
}

$runtimeRoot = Join-Path $workspace "runtime"
$logsRoot = Join-Path $workspace "logs"
$sectionsRoot = Join-Path $workspace "sections"
$audioRoot = Join-Path $workspace "input"
$jobsRoot = Join-Path $workspace "jobs"
$tempRoot = Join-Path $workspace "temp"
$statePath = Join-Path $runtimeRoot "stack-state.json"
$lockPath = Join-Path $runtimeRoot "supervisor.lock"
$supervisorPidPath = Join-Path $runtimeRoot "supervisor.pid"
$servicePidPath = Join-Path $runtimeRoot "service.pid"
$providerWorkerPidPath = Join-Path $runtimeRoot "provider-worker.pid"
$providerPidPath = Join-Path $runtimeRoot "provider.pid"
$stopSignalPath = Join-Path $runtimeRoot "stop.signal"
$providerWorkerStatusPath = Join-Path $runtimeRoot "provider-worker-status.json"

foreach ($directory in @($workspace, $runtimeRoot, $logsRoot, $sectionsRoot, $audioRoot, $jobsRoot, $tempRoot)) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}

$lockStream = $null
try {
    $lockStream = [System.IO.File]::Open($lockPath, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
} catch {
    throw "Another Echoes Cinema supervisor is already active. Re-run START_ECHOES_CINEMA.cmd to reopen it."
}

$serviceProcess = $null
$providerWorkerProcess = $null
$serviceRestarts = 0
$providerRestarts = 0
$serviceBackoffSeconds = 2
$providerBackoffSeconds = 5
$lastServiceRestart = [DateTime]::MinValue
$lastProviderRestart = [DateTime]::MinValue
$serviceStdout = ""
$serviceStderr = ""
$providerWorkerStdout = ""
$providerWorkerStderr = ""
$lastError = ""
$dashboardUrl = ""
$consecutiveDashboardFailures = 0

try {
    Remove-Item -LiteralPath $stopSignalPath -Force -ErrorAction SilentlyContinue
    Set-Content -LiteralPath $supervisorPidPath -Value $PID -Encoding ascii

    $pythonCandidates = @(
        (Join-Path $workspace ".venv-cinema\Scripts\python.exe"),
        "D:\A.I\Python310\python.exe"
    )
    $servicePython = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    if (-not $servicePython) {
        $ensurePython = Join-Path $RepoRoot "scripts\ensure-python-on-d.ps1"
        if (-not (Test-Path -LiteralPath $ensurePython -PathType Leaf)) {
            throw "No D-drive Python is available and ensure-python-on-d.ps1 is missing."
        }
        $resolved = & $ensurePython -InstallRoot "D:\A.I\Python310" -WorkspaceRoot $workspace
        if ($LASTEXITCODE -ne 0) { throw "D-drive Python installation failed." }
        $servicePython = [string]($resolved | Select-Object -Last 1)
    }

    $controlCenter = Join-Path $RepoRoot "tools\cinema_control_center.py"
    $providerWorker = Join-Path $RepoRoot "scripts\echoes-cinema-provider-worker.ps1"
    $fixture = Join-Path $RepoRoot "tests\fixtures\first_real_clip_sections.csv"
    foreach ($required in @($controlCenter, $providerWorker, $fixture)) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Required stack file not found: $required" }
    }
    Copy-Item -LiteralPath $fixture -Destination (Join-Path $sectionsRoot "first_real_clip_sections.csv") -Force

    $servicePort = Find-FreePort -Preferred $PreferredServicePort
    $providerPort = Find-FreePort -Preferred $PreferredProviderPort
    if ($servicePort -eq $providerPort) { $providerPort = Find-FreePort -Preferred ($providerPort + 1) }
    $dashboardUrl = "http://localhost:$servicePort/"
    $providerEndpoint = "http://127.0.0.1:$providerPort/v1/render"
    $providerHealthUrl = "http://127.0.0.1:$providerPort/health"

    # Secrets are inherited through the child environment only. They are never
    # written to state/log files or placed in a process command line.
    $env:ECHOES_CINEMA_SERVICE_TOKEN = New-RandomToken
    $env:ECHOES_RENDER_TOKEN = New-RandomToken
    $env:ECHOES_RENDER_ENDPOINT = $providerEndpoint
    $env:ECHOES_RENDER_HEALTH_URL = $providerHealthUrl
    $env:ECHOES_RENDER_HOST_ALLOWLIST = "127.0.0.1,localhost"
    $env:ECHOES_CINEMA_STORAGE_RESERVE_GIB = "$StorageReserveGiB"
    $env:ECHOES_CINEMA_DEFAULT_JOB_GIB = "$DefaultJobGiB"
    $env:ECHOES_CINEMA_MAX_JOB_GIB = "$MaxJobGiB"
    $env:ECHOES_CINEMA_RUNTIME_ROOT = $runtimeRoot
    $env:ECHOES_CINEMA_PROVIDER_MODE = $resolvedProviderMode
    $env:TEMP = $tempRoot
    $env:TMP = $tempRoot
    $env:TMPDIR = $tempRoot

    function Start-ControlCenterProcess {
        $script:serviceRestarts++
        $script:consecutiveDashboardFailures = 0
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $script:serviceStdout = Join-Path $logsRoot "control-center-$stamp.log"
        $script:serviceStderr = Join-Path $logsRoot "control-center-$stamp.error.log"
        $arguments = @(
            $controlCenter,
            "--host", "127.0.0.1",
            "--port", "$servicePort",
            "--sections-root", $sectionsRoot,
            "--audio-root", $audioRoot,
            "--output-root", $jobsRoot,
            "--provider-timeout", "180",
            "--max-workers", "$MaxWorkers"
        )
        $script:serviceProcess = Start-Process -FilePath $servicePython -ArgumentList $arguments -WorkingDirectory $workspace -RedirectStandardOutput $serviceStdout -RedirectStandardError $serviceStderr -WindowStyle Hidden -PassThru
        Set-Content -LiteralPath $servicePidPath -Value $serviceProcess.Id -Encoding ascii
        $script:lastServiceRestart = Get-Date
    }

    function Start-ProviderWorkerProcess {
        $script:providerRestarts++
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $script:providerWorkerStdout = Join-Path $logsRoot "provider-worker-$stamp.log"
        $script:providerWorkerStderr = Join-Path $logsRoot "provider-worker-$stamp.error.log"
        $arguments = @(
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", $providerWorker,
            "-WorkspaceRoot", $workspace,
            "-RepoRoot", $RepoRoot,
            "-ProviderPort", "$providerPort",
            "-ProviderMode", $resolvedProviderMode
        )
        $script:providerWorkerProcess = Start-Process -FilePath "powershell.exe" -ArgumentList $arguments -WorkingDirectory $RepoRoot -RedirectStandardOutput $providerWorkerStdout -RedirectStandardError $providerWorkerStderr -WindowStyle Hidden -PassThru
        Set-Content -LiteralPath $providerWorkerPidPath -Value $providerWorkerProcess.Id -Encoding ascii
        $script:lastProviderRestart = Get-Date
    }

    function Publish-State {
        param([string]$Status)
        $providerPid = $null
        if (Test-Path -LiteralPath $providerPidPath) {
            $rawProviderPid = (Get-Content -LiteralPath $providerPidPath -Raw -ErrorAction SilentlyContinue).Trim()
            if ($rawProviderPid -match '^\d+$') { $providerPid = [int]$rawProviderPid }
        }
        $workerStatus = $null
        if (Test-Path -LiteralPath $providerWorkerStatusPath) {
            try { $workerStatus = Get-Content -LiteralPath $providerWorkerStatusPath -Raw | ConvertFrom-Json } catch { }
        }
        Write-AtomicJson -Path $statePath -Payload @{
            schema = "echoes.cinema-stack-state.v1"
            status = $Status
            timestampUtc = [DateTime]::UtcNow.ToString("o")
            dashboardUrl = $dashboardUrl
            workspace = $workspace
            repoRoot = $RepoRoot
            servicePort = $servicePort
            providerPort = $providerPort
            providerMode = $resolvedProviderMode
            supervisorPid = $PID
            servicePid = if ($serviceProcess -and -not $serviceProcess.HasExited) { $serviceProcess.Id } else { $null }
            providerWorkerPid = if ($providerWorkerProcess -and -not $providerWorkerProcess.HasExited) { $providerWorkerProcess.Id } else { $null }
            providerPid = $providerPid
            serviceRestarts = $serviceRestarts
            providerRestarts = $providerRestarts
            consecutiveDashboardFailures = $consecutiveDashboardFailures
            dashboardFailureThreshold = $DashboardFailureThreshold
            serviceStdoutLog = $serviceStdout
            serviceStderrLog = $serviceStderr
            providerWorkerStdoutLog = $providerWorkerStdout
            providerWorkerStderrLog = $providerWorkerStderr
            providerWorkerStatus = $workerStatus
            lastError = if ($lastError) { $lastError } else { $null }
            secretsPersisted = $false
            systemDriveWritesAllowed = $false
        }
    }

    Publish-State -Status "STARTING"
    Start-ControlCenterProcess

    $readyDeadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $readyDeadline) {
        if ($serviceProcess.HasExited) {
            $tail = if (Test-Path -LiteralPath $serviceStderr) { Get-Content -LiteralPath $serviceStderr -Tail 30 | Out-String } else { "no service error log" }
            throw "Control center exited during startup. $tail"
        }
        if (Test-DashboardReady -Url $dashboardUrl) { break }
        Start-Sleep -Seconds 1
    }
    if (-not (Test-DashboardReady -Url $dashboardUrl)) {
        throw "Control center did not become reachable at $dashboardUrl within 60 seconds."
    }

    Start-ProviderWorkerProcess
    Publish-State -Status "RUNNING"

    while (-not (Test-Path -LiteralPath $stopSignalPath)) {
        $now = Get-Date
        $serviceAlive = [bool]($serviceProcess -and -not $serviceProcess.HasExited)
        $dashboardHealthy = $false
        if ($serviceAlive) { $dashboardHealthy = Test-DashboardReady -Url $dashboardUrl }

        if ($serviceAlive -and -not $dashboardHealthy) {
            $consecutiveDashboardFailures++
        } elseif ($dashboardHealthy) {
            $consecutiveDashboardFailures = 0
        } else {
            $consecutiveDashboardFailures = 0
        }

        $dashboardAction = Get-DashboardRecoveryAction `
            -ProcessAlive $serviceAlive `
            -DashboardHealthy $dashboardHealthy `
            -ConsecutiveFailures $consecutiveDashboardFailures `
            -FailureThreshold $DashboardFailureThreshold

        switch ($dashboardAction) {
            "RESTART_EXITED" {
                $lastError = "Control center stopped and is being restarted automatically."
                if (($now - $lastServiceRestart).TotalSeconds -ge $serviceBackoffSeconds) {
                    Start-ControlCenterProcess
                    $serviceBackoffSeconds = [math]::Min(60, [math]::Max(2, $serviceBackoffSeconds * 2))
                }
            }
            "RESTART_UNRESPONSIVE" {
                $lastError = "Control center process stayed alive but failed $consecutiveDashboardFailures consecutive HTTP health checks. It is being replaced automatically."
                if (($now - $lastServiceRestart).TotalSeconds -ge $serviceBackoffSeconds) {
                    Stop-ChildProcess -Process $serviceProcess
                    Remove-Item -LiteralPath $servicePidPath -Force -ErrorAction SilentlyContinue
                    Start-ControlCenterProcess
                    $serviceBackoffSeconds = [math]::Min(60, [math]::Max(2, $serviceBackoffSeconds * 2))
                }
            }
            "WAIT_UNRESPONSIVE" {
                $lastError = "Control center HTTP health check failed $consecutiveDashboardFailures of $DashboardFailureThreshold times; supervisor is verifying before replacement."
            }
            "HEALTHY" {
                $serviceBackoffSeconds = 2
                $lastError = ""
            }
        }

        if (-not $providerWorkerProcess -or $providerWorkerProcess.HasExited) {
            $lastError = "AI provider worker stopped and is being restarted automatically."
            if (($now - $lastProviderRestart).TotalSeconds -ge $providerBackoffSeconds) {
                Start-ProviderWorkerProcess
                $providerBackoffSeconds = [math]::Min(120, [math]::Max(5, $providerBackoffSeconds * 2))
            }
        } else {
            $providerBackoffSeconds = 5
        }

        $stackStatus = if ($dashboardHealthy) { "RUNNING" } else { "PARTIAL" }
        Publish-State -Status $stackStatus
        Start-Sleep -Seconds 5
    }
}
catch {
    $lastError = $_.Exception.Message
    try {
        Write-AtomicJson -Path $statePath -Payload @{
            schema = "echoes.cinema-stack-state.v1"
            status = "BROKEN"
            timestampUtc = [DateTime]::UtcNow.ToString("o")
            dashboardUrl = $dashboardUrl
            workspace = $workspace
            providerMode = $resolvedProviderMode
            supervisorPid = $PID
            lastError = $lastError
            secretsPersisted = $false
            systemDriveWritesAllowed = $false
        }
    } catch { }
    Write-Error $_
    exit 1
}
finally {
    Stop-ChildProcess -Process $providerWorkerProcess
    if (Test-Path -LiteralPath $providerPidPath) {
        $raw = (Get-Content -LiteralPath $providerPidPath -Raw -ErrorAction SilentlyContinue).Trim()
        if ($raw -match '^\d+$') { Stop-Process -Id ([int]$raw) -Force -ErrorAction SilentlyContinue }
    }
    Stop-ChildProcess -Process $serviceProcess
    Remove-Item -LiteralPath $servicePidPath, $providerWorkerPidPath, $providerPidPath, $supervisorPidPath, $stopSignalPath -Force -ErrorAction SilentlyContinue
    try {
        Write-AtomicJson -Path $statePath -Payload @{
            schema = "echoes.cinema-stack-state.v1"
            status = "STOPPED"
            timestampUtc = [DateTime]::UtcNow.ToString("o")
            dashboardUrl = $dashboardUrl
            workspace = $workspace
            providerMode = $resolvedProviderMode
            supervisorPid = $PID
            lastError = if ($lastError) { $lastError } else { $null }
            secretsPersisted = $false
            systemDriveWritesAllowed = $false
        }
    } catch { }
    Remove-Item Env:ECHOES_CINEMA_SERVICE_TOKEN, Env:ECHOES_RENDER_TOKEN, Env:ECHOES_CINEMA_PROVIDER_MODE -ErrorAction SilentlyContinue
    if ($lockStream) { $lockStream.Dispose() }
}
