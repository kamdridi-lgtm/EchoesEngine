param(
    [string]$WorkspaceRoot = "D:\A.I\EchoesCinema",
    [string]$RepoRoot = "",
    [int]$StartupTimeoutSeconds = 120,
    [switch]$NoBrowser,
    [string]$ProviderMode = "",
    [switch]$PathNormalizationSelfTest
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
    if (-not $normalized) { throw "$Name is empty after path normalization." }
    if ($normalized.IndexOfAny([System.IO.Path]::GetInvalidPathChars()) -ge 0) {
        throw "$Name contains illegal path characters after normalization: $normalized"
    }
    return $normalized
}

if ($PathNormalizationSelfTest) {
    $cases = @(
        @{ value = 'D:\A.I\EchoesEngine"'; expected = 'D:\A.I\EchoesEngine' },
        @{ value = ' D:\A.I\EchoesEngine\ '; expected = 'D:\A.I\EchoesEngine' },
        @{ value = 'D:\A.I\EchoesCinema/'; expected = 'D:\A.I\EchoesCinema' }
    )
    foreach ($case in $cases) {
        $actual = Normalize-LauncherPath -Value $case.value -Name "SelfTestPath"
        if ($actual -ne $case.expected) {
            throw "Path normalization self-test failed. Input=$($case.value) Expected=$($case.expected) Actual=$actual"
        }
    }
    Write-Host "Echoes Cinema path normalization PASS trailing-quote=removed trailing-separator=removed"
    exit 0
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
$ffmpegWorker = Join-Path $RepoRoot "scripts\echoes-cinema-ffmpeg-worker.ps1"
$ffmpegBin = Join-Path $workspace "tools\ffmpeg\bin"
$runtimePackagePaths = @(
    "scripts\echoes-cinema-ffmpeg-worker.ps1",
    "scripts\ensure-ffmpeg-on-d.ps1",
    "providers\ffmpeg-runtime-lock.json",
    "tools\assemble_render.py"
)

foreach ($directory in @($workspace, $runtimeRoot, $logsRoot)) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}

function Repair-MissingRuntimePackage {
    $missing = @($runtimePackagePaths | Where-Object {
        -not (Test-Path -LiteralPath (Join-Path $RepoRoot $_) -PathType Leaf)
    })
    if ($missing.Count -eq 0) { return }

    $git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $git) {
        throw "Echoes Cinema runtime package is incomplete and git is unavailable. Missing: $($missing -join ', ')"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot ".git") -PathType Container)) {
        throw "Echoes Cinema runtime package is incomplete and RepoRoot is not a Git repository: $RepoRoot"
    }

    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $migrationRoot = Join-Path $workspace "temp\runtime-package-migration-$stamp"
    $archivePath = Join-Path $migrationRoot "runtime-package.zip"
    $stagePath = Join-Path $migrationRoot "stage"
    New-Item -ItemType Directory -Path $stagePath -Force | Out-Null

    try {
        Write-Host "Incomplete one-click package detected. Synchronizing missing runtime files: $($missing -join ', ')"
        & $git.Source -C $RepoRoot fetch origin main
        if ($LASTEXITCODE -ne 0) { throw "Unable to fetch origin/main for automatic runtime migration." }

        $archiveArguments = @("-C", $RepoRoot, "archive", "--format=zip", "--output=$archivePath", "origin/main", "--") + $missing
        & $git.Source @archiveArguments
        if ($LASTEXITCODE -ne 0) { throw "Unable to extract missing runtime files from origin/main." }

        Expand-Archive -LiteralPath $archivePath -DestinationPath $stagePath -Force
        foreach ($relative in $missing) {
            $source = Join-Path $stagePath $relative
            $destination = Join-Path $RepoRoot $relative
            if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
                throw "Canonical runtime migration archive is missing: $relative"
            }
            New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
            Copy-Item -LiteralPath $source -Destination $destination -Force
        }
        Write-Host "Echoes Cinema runtime migration PASS files=$($missing.Count) source=origin/main"
    } finally {
        Remove-Item -LiteralPath $migrationRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Repair-MissingRuntimePackage
foreach ($required in @($supervisor, $stopScript, $ffmpegWorker)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Echoes Cinema runtime file not found: $required" }
}
foreach ($relative in $runtimePackagePaths) {
    $required = Join-Path $RepoRoot $relative
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Echoes Cinema migrated runtime file not found: $required" }
}

$env:PATH = "$ffmpegBin;$env:PATH"
$env:ECHOES_CINEMA_MEDIA_TOOL_WAIT_SECONDS = "1800"

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

function Test-VerifiedFfmpegWorker {
    $pidPath = Join-Path $runtimeRoot "ffmpeg-worker.pid"
    if (-not (Test-Path -LiteralPath $pidPath -PathType Leaf)) { return $false }
    $raw = (Get-Content -LiteralPath $pidPath -Raw -ErrorAction SilentlyContinue).Trim()
    if ($raw -notmatch '^\d+$') { return $false }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$raw" -ErrorAction SilentlyContinue
    return $null -ne $process -and ([string]$process.CommandLine -like "*echoes-cinema-ffmpeg-worker.ps1*")
}

function Start-FfmpegWorker {
    if (Test-VerifiedFfmpegWorker) { return }
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $stdout = Join-Path $logsRoot "ffmpeg-worker-$stamp.log"
    $stderr = Join-Path $logsRoot "ffmpeg-worker-$stamp.error.log"
    $arguments = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", $ffmpegWorker,
        "-WorkspaceRoot", $workspace,
        "-RepoRoot", $RepoRoot
    )
    Start-Process -FilePath "powershell.exe" -ArgumentList $arguments -WorkingDirectory $RepoRoot -RedirectStandardOutput $stdout -RedirectStandardError $stderr -WindowStyle Hidden | Out-Null
    Write-Host "Pinned FFmpeg provisioning is running in the background. Dashboard startup will not be blocked."
}

function Test-Dashboard {
    param([string]$Url)
    if (-not $Url) { return $false }
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
        return $response.StatusCode -eq 200 -and $response.Content -like "*ECHOES CINEMA*"
    } catch { return $false }
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
    Start-FfmpegWorker
    Write-Host "Echoes Cinema is already running. Dashboard: $($existing.dashboardUrl)"
    if (-not $NoBrowser) { Start-Process ([string]$existing.dashboardUrl) }
    exit 0
}

if ($existing -and (Test-VerifiedSupervisor -ProcessId $existing.supervisorPid)) {
    Write-Host "A stale or unhealthy Echoes Cinema stack was detected. Stopping it safely before repair."
    & powershell -NoProfile -ExecutionPolicy Bypass -File $stopScript -WorkspaceRoot $workspace -GraceSeconds 10
}

Remove-Item -LiteralPath (Join-Path $runtimeRoot "stop.signal") -Force -ErrorAction SilentlyContinue
Start-FfmpegWorker

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
if ($ProviderMode) { $arguments += @("-ProviderMode", $ProviderMode) }

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
$ffmpegStatusPath = Join-Path $runtimeRoot "ffmpeg-worker-status.json"
$ffmpegStatus = if (Test-Path -LiteralPath $ffmpegStatusPath) { (Get-Content -LiteralPath $ffmpegStatusPath -Raw -ErrorAction SilentlyContinue | Out-String).Trim() } else { "not written yet" }

Write-Error @"
Echoes Cinema did not open a dead localhost page. Startup failed before the dashboard became reachable and the service PID was recorded.
State error: $stateError
Supervisor stderr: $stderrTail
Supervisor stdout: $stdoutTail
FFmpeg worker: $ffmpegStatus
Logs: $logsRoot
"@
exit 1
