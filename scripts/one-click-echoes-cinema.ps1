param(
    [string]$RepoRoot = "D:\A.I\EchoesEngine",
    [string]$WorkspaceRoot = "D:\A.I\EchoesCinema",
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repo = [System.IO.Path]::GetFullPath($RepoRoot)
$workspace = [System.IO.Path]::GetFullPath($WorkspaceRoot)
$logs = Join-Path $workspace "logs"
$runtime = Join-Path $workspace "runtime"
$backups = Join-Path $workspace "backups\one-click"
$tempRoot = Join-Path $workspace "temp\one-click"
$proofRoot = Join-Path $workspace "proofs\one-click"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logPath = Join-Path $logs "one-click-$stamp.log"

$criticalPaths = @(
    "START_ECHOES_CINEMA.cmd",
    "STOP_ECHOES_CINEMA.cmd",
    "REFRESH_AND_START_ECHOES_CINEMA.cmd",
    "CLIQUE_ICI_ECHOES_CINEMA.cmd",
    "scripts/start-echoes-cinema-stack.ps1",
    "scripts/stop-echoes-cinema-stack.ps1",
    "scripts/echoes-cinema-stack-supervisor.ps1",
    "scripts/echoes-cinema-provider-worker.ps1",
    "scripts/bootstrap-cinema-ai.ps1",
    "scripts/ensure-python-on-d.ps1",
    "scripts/one-click-echoes-cinema.ps1",
    "scripts/one-click-echoes-cinema-monitor.ps1",
    "providers/provider_bootstrap_health_bridge.py",
    "providers/modelscope_low_vram_provider.py",
    "providers/modelscope_resilient_provider.py",
    "providers/modelscope_low_vram_provider_v2.py",
    "providers/diffusers_environment_lock.py",
    "providers/requirements-diffusers.txt",
    "tools/cinema_control_center.py",
    "tools/cinema_job_service.py",
    "tools/cinema_job_service_durable.py",
    "tools/cinema_job_scheduler.py",
    "tools/cinema_job_ledger.py",
    "tools/cinema_p0_autopilot.py",
    "tools/cinema_real_input_audio.py",
    "tools/cinema_p0_preflight.py",
    "tools/cinema_p0_evidence_bundle.py",
    "tools/cinema_job_runner.py",
    "tools/render_resume.py",
    "tools/resumable_http_render_worker.py",
    "tools/assemble_render.py",
    "tests/fixtures/first_real_clip_sections.csv"
)

function Write-Step {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message
    Write-Host $line
    if (Test-Path -LiteralPath $logs -PathType Container) {
        Add-Content -LiteralPath $logPath -Value $line -Encoding utf8
    }
}

function Invoke-Git {
    param([string[]]$Arguments)
    & git @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw ("Git failed with exit code {0}: git {1}" -f $LASTEXITCODE, ($Arguments -join ' '))
    }
}

function Test-HttpReady {
    param([string]$Url)
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 5
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Read-JsonFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    try { return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json } catch { return $null }
}

if ($SelfTest) {
    if (-not (Test-Path -LiteralPath $repo -PathType Container)) { throw "Self-test repository missing: $repo" }
    foreach ($relative in @(
        "START_ECHOES_CINEMA.cmd",
        "STOP_ECHOES_CINEMA.cmd",
        "scripts/one-click-echoes-cinema.ps1",
        "scripts/one-click-echoes-cinema-monitor.ps1",
        "providers/provider_bootstrap_health_bridge.py",
        "providers/diffusers_environment_lock.py",
        "providers/requirements-diffusers.txt",
        "tools/cinema_real_input_audio.py"
    )) {
        if (-not (Test-Path -LiteralPath (Join-Path $repo $relative) -PathType Leaf)) {
            throw "Self-test required file missing: $relative"
        }
    }
    foreach ($relative in @(
        "scripts/one-click-echoes-cinema.ps1",
        "scripts/one-click-echoes-cinema-monitor.ps1",
        "scripts/echoes-cinema-provider-worker.ps1",
        "scripts/echoes-cinema-stack-supervisor.ps1"
    )) {
        [void][scriptblock]::Create((Get-Content -LiteralPath (Join-Path $repo $relative) -Raw))
    }
    $source = Get-Content -LiteralPath $PSCommandPath -Raw
    $forbiddenCommands = @(
        ("reset " + "--hard"),
        ("clean " + "-fdx"),
        ("clean " + "-xdf")
    )
    foreach ($forbidden in $forbiddenCommands) {
        if ($source.Contains($forbidden)) { throw "Forbidden destructive command found: $forbidden" }
    }
    & python (Join-Path $repo "providers\provider_bootstrap_health_bridge.py") --self-test
    if ($LASTEXITCODE -ne 0) { throw "Provider bootstrap bridge self-test failed" }
    & python (Join-Path $repo "providers\diffusers_environment_lock.py") --self-test
    if ($LASTEXITCODE -ne 0) { throw "Pinned Diffusers environment lock self-test failed" }
    Write-Host "one-click orchestrator self-test PASS"
    exit 0
}

foreach ($directory in @($workspace, $logs, $runtime, $backups, $tempRoot, $proofRoot)) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}
Set-Content -LiteralPath $logPath -Value "Echoes Cinema one-click started $(Get-Date -Format o)" -Encoding utf8

try {
    Write-Step "Validating D-drive repository and tools."
    if ([System.IO.Path]::GetPathRoot($workspace).TrimEnd("\").ToUpperInvariant() -eq "C:") {
        throw "Echoes Cinema workspace refuses heavy storage on drive C:."
    }
    if (-not (Test-Path -LiteralPath (Join-Path $repo ".git") -PathType Container)) {
        throw "EchoesEngine Git repository is missing: $repo"
    }
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "Git is not available in PATH." }

    Write-Step "Fetching canonical main without destructive reset."
    Invoke-Git -Arguments @("-C", $repo, "fetch", "origin", "main")

    $archivePath = Join-Path $tempRoot "canonical-$stamp.zip"
    $stagePath = Join-Path $tempRoot "canonical-$stamp"
    $backupPath = Join-Path $backups $stamp
    New-Item -ItemType Directory -Path $stagePath -Force | Out-Null
    New-Item -ItemType Directory -Path $backupPath -Force | Out-Null

    Write-Step "Extracting only runtime-critical files from origin/main."
    $archiveArgs = @("-C", $repo, "archive", "--format=zip", "--output=$archivePath", "origin/main", "--") + $criticalPaths
    Invoke-Git -Arguments $archiveArgs
    Expand-Archive -LiteralPath $archivePath -DestinationPath $stagePath -Force

    foreach ($relative in $criticalPaths) {
        $source = Join-Path $stagePath $relative
        $destination = Join-Path $repo $relative
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Canonical runtime file missing from archive: $relative" }
        if (Test-Path -LiteralPath $destination -PathType Leaf) {
            $backupDestination = Join-Path $backupPath $relative
            New-Item -ItemType Directory -Path (Split-Path -Parent $backupDestination) -Force | Out-Null
            Copy-Item -LiteralPath $destination -Destination $backupDestination -Force
        }
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Copy-Item -LiteralPath $source -Destination $destination -Force
    }

    Remove-Item -LiteralPath $archivePath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $stagePath -Recurse -Force -ErrorAction SilentlyContinue

    Write-Step "Parsing PowerShell launch chain before execution."
    foreach ($relative in @(
        "scripts/start-echoes-cinema-stack.ps1",
        "scripts/stop-echoes-cinema-stack.ps1",
        "scripts/echoes-cinema-stack-supervisor.ps1",
        "scripts/echoes-cinema-provider-worker.ps1",
        "scripts/bootstrap-cinema-ai.ps1",
        "scripts/one-click-echoes-cinema-monitor.ps1"
    )) {
        [void][scriptblock]::Create((Get-Content -LiteralPath (Join-Path $repo $relative) -Raw))
    }

    $pythonCandidates = @(
        (Join-Path $workspace ".venv-cinema\Scripts\python.exe"),
        "D:\A.I\Python310\python.exe"
    )
    $python = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    if ($python) {
        Write-Step "Compiling critical Python entrypoints."
        & $python -m py_compile `
            (Join-Path $repo "providers\provider_bootstrap_health_bridge.py") `
            (Join-Path $repo "providers\diffusers_environment_lock.py") `
            (Join-Path $repo "providers\modelscope_low_vram_provider.py") `
            (Join-Path $repo "providers\modelscope_resilient_provider.py") `
            (Join-Path $repo "providers\modelscope_low_vram_provider_v2.py") `
            (Join-Path $repo "tools\cinema_control_center.py") `
            (Join-Path $repo "tools\cinema_p0_autopilot.py") `
            (Join-Path $repo "tools\cinema_real_input_audio.py") `
            (Join-Path $repo "tools\cinema_p0_evidence_bundle.py")
        if ($LASTEXITCODE -ne 0) { throw "Critical Python compilation failed." }
    } else {
        Write-Step "D-drive Python is not installed yet; the canonical bootstrap will install it automatically."
    }

    Write-Step "Stopping any previous Echoes Cinema stack safely."
    $stopScript = Join-Path $repo "scripts\stop-echoes-cinema-stack.ps1"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $stopScript -WorkspaceRoot $workspace -GraceSeconds 20
    $stopExit = $LASTEXITCODE
    if ($stopExit -ne 0) {
        Write-Step "Safe stop returned $stopExit; startup will repair stale state without deleting models or jobs."
    }

    foreach ($name in @("provider.pid", "provider-worker.pid", "stop.signal")) {
        Remove-Item -LiteralPath (Join-Path $runtime $name) -Force -ErrorAction SilentlyContinue
    }

    Write-Step "Starting the verified one-click stack."
    $startScript = Join-Path $repo "scripts\start-echoes-cinema-stack.ps1"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $startScript -WorkspaceRoot $workspace -RepoRoot $repo
    $startExit = $LASTEXITCODE
    if ($startExit -ne 0) { throw "Echoes Cinema startup failed with exit code $startExit" }

    $statePath = Join-Path $runtime "stack-state.json"
    $deadline = (Get-Date).AddMinutes(3)
    $dashboardUrl = "http://127.0.0.1:8090/"
    while ((Get-Date) -lt $deadline) {
        $state = Read-JsonFile -Path $statePath
        if ($state -and $state.dashboardUrl) { $dashboardUrl = [string]$state.dashboardUrl }
        if (Test-HttpReady -Url $dashboardUrl) { break }
        Start-Sleep -Seconds 2
    }
    if (-not (Test-HttpReady -Url $dashboardUrl)) {
        throw "The control center did not become reachable. Exact log: $logPath"
    }

    Write-Step "Launching hidden automatic provider monitor."
    $monitorScript = Join-Path $repo "scripts\one-click-echoes-cinema-monitor.ps1"
    $monitorStdout = Join-Path $logs "one-click-monitor-$stamp.log"
    $monitorStderr = Join-Path $logs "one-click-monitor-$stamp.error.log"
    $monitorArguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $monitorScript,
        "-WorkspaceRoot", $workspace,
        "-MaximumMinutes", "240"
    )
    Start-Process -FilePath "powershell.exe" -ArgumentList $monitorArguments -WindowStyle Hidden -RedirectStandardOutput $monitorStdout -RedirectStandardError $monitorStderr | Out-Null

    Write-Step "Opening the live control center."
    Start-Process $dashboardUrl | Out-Null

    @{
        schema = "echoes.cinema-one-click-result.v1"
        status = "PARTIAL"
        timestampUtc = [DateTime]::UtcNow.ToString("o")
        repoRoot = $repo
        workspaceRoot = $workspace
        dashboardUrl = $dashboardUrl
        backupPath = $backupPath
        logPath = $logPath
        automaticMonitor = (Join-Path $runtime "one-click-monitor-status.json")
        message = "Control center is online. Provider recovery and P0 monitoring continue automatically."
    } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $runtime "one-click-result.json") -Encoding utf8

    Write-Step "DONE. Echoes Cinema is online and continues working automatically."
    exit 0
}
catch {
    $message = $_.Exception.Message
    Write-Step "BROKEN: $message"
    @{
        schema = "echoes.cinema-one-click-result.v1"
        status = "BROKEN"
        timestampUtc = [DateTime]::UtcNow.ToString("o")
        error = $message
        logPath = $logPath
    } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $runtime "one-click-result.json") -Encoding utf8
    exit 1
}
