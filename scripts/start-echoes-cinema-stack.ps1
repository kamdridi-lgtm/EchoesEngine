param(
    [string]$WorkspaceRoot = "D:\A.I\EchoesCinema",
    [string]$RepoRoot = "",
    [int]$StartupTimeoutSeconds = 120,
    [switch]$NoBrowser,
    [string]$ProviderMode = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Normalize-LauncherPath {
    param(
        [string]$Value,
        [string]$Name
    )

    $normalized = ([string]$Value).Trim().Trim('"')
    while ($normalized.Length -gt 3 -and ($normalized.EndsWith("\") -or $normalized.EndsWith("/"))) {
        $normalized = $normalized.Substring(0, $normalized.Length - 1)
    }
    if (-not $normalized) {
        throw "$Name is empty after path normalization."
    }
    if ($normalized.IndexOfAny([System.IO.Path]::GetInvalidPathChars()) -ge 0) {
        throw "$Name contains illegal path characters after normalization: $normalized"
    }
    return $normalized
}

if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
} else {
    $RepoRoot = Normalize-LauncherPath -Value $RepoRoot -Name "RepoRoot"
    $RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
}
$WorkspaceRoot = Normalize-LauncherPath -Value $WorkspaceRoot -Name "WorkspaceRoot"
$workspace = [System.IO.Path]::GetFullPath($WorkspaceRoot)
$runtimeRoot = Join-Path $workspace "runtime"
$logsRoot = Join-Path $workspace "logs"
$statePath = Join-Path $runtimeRoot "stack-state.json"
$supervisor = Join-Path $RepoRoot "scripts\echoes-cinema-stack-supervisor.ps1"
$stopScript = Join-Path $RepoRoot "scripts\stop-echoes-cinema-stack.ps1"

foreach ($directory in @($workspace, $runtimeRoot, $logsRoot)) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}
if (-not (Test-Path -LiteralPath $supervisor -PathType Leaf)) {
    throw "Echoes Cinema supervisor not found: $supervisor"
}

function Get-State {
    if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) { return $null }
    try { return Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json } catch { return $null }
}

function Test-VerifiedSupervisor {
    param([object]$ProcessId)
    if ($null -eq $ProcessId -or "$ProcessId" -notmatch '^\d+$') { return $false }
    $pidNumber = [int]$ProcessId
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$pidNumber" -ErrorAction SilentlyContinue
    return $null -ne $process -and ([string]$process.CommandLine -like "*echoes-cinema-stack-supervisor.ps1*")
}

function Test-Dashboard {
    param([string]$Url)
    if (-not $Url) { return $false }
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
        return $response.StatusCode -eq 200 -and $response.Content -like "*ECHOES CINEMA*"
    } catch {
        return $false
    }
}

function Test-StateReady {
    param([object]$State)
    if (-not $State) { return $false }
    if (-not $State.dashboardUrl -or -not $State.servicePid) { return $false }
    if ([string]$State.status -notin @("RUNNING", "PARTIAL")) { return $false }
    return Test-Dashboard -Url ([string]$State.dashboardUrl)
}

$existing = Get-State
if ($existing -and (Test-VerifiedSupervisor -ProcessId $existing.supervisorPid) -and (Test-StateReady -State $existing)) {
    Write-Host "Echoes Cinema is already running. Dashboard: $($existing.dashboardUrl)"
    if (-not $NoBrowser) { Start-Process ([string]$existing.dashboardUrl) }
    exit 0
}

if ($existing -and (Test-VerifiedSupervisor -ProcessId $existing.supervisorPid)) {
    Write-Host "A stale or unhealthy Echoes Cinema stack was detected. Stopping it safely before repair."
    & powershell -NoProfile -ExecutionPolicy Bypass -File $stopScript -WorkspaceRoot $workspace -GraceSeconds 10
}

Remove-Item -LiteralPath (Join-Path $runtimeRoot "stop.signal") -Force -ErrorAction SilentlyContinue
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$stdoutLog = Join-Path $logsRoot "supervisor-$stamp.log"
$stderrLog = Join-Path $logsRoot "supervisor-$stamp.error.log"
$arguments = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", $supervisor,
    "-WorkspaceRoot", $workspace,
    "-RepoRoot", $RepoRoot
)
if ($ProviderMode) {
    $arguments += @("-ProviderMode", $ProviderMode)
}

Write-Host "Starting Echoes Cinema supervisor. The browser opens only after localhost is truly reachable."
$supervisorProcess = Start-Process -FilePath "powershell.exe" -ArgumentList $arguments -WorkingDirectory $RepoRoot -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog -WindowStyle Hidden -PassThru

$deadline = (Get-Date).AddSeconds([math]::Max(20, $StartupTimeoutSeconds))
$lastState = $null
while ((Get-Date) -lt $deadline) {
    if ($supervisorProcess.HasExited) { break }
    $lastState = Get-State
    if (Test-StateReady -State $lastState) {
        Write-Host "Echoes Cinema control center is reachable: $($lastState.dashboardUrl)"
        if (-not $NoBrowser) { Start-Process ([string]$lastState.dashboardUrl) }
        exit 0
    }
    Start-Sleep -Seconds 1
}

$lastState = Get-State
$stateError = if ($lastState -and $lastState.lastError) { [string]$lastState.lastError } else { "" }
$stderrTail = if (Test-Path -LiteralPath $stderrLog) { (Get-Content -LiteralPath $stderrLog -Tail 40 -ErrorAction SilentlyContinue | Out-String).Trim() } else { "" }
$stdoutTail = if (Test-Path -LiteralPath $stdoutLog) { (Get-Content -LiteralPath $stdoutLog -Tail 40 -ErrorAction SilentlyContinue | Out-String).Trim() } else { "" }

Write-Error @"
Echoes Cinema did not open a dead localhost page. Startup failed before the dashboard became reachable and the service PID was recorded.
State error: $stateError
Supervisor stderr: $stderrTail
Supervisor stdout: $stdoutTail
Logs: $logsRoot
"@
exit 1
