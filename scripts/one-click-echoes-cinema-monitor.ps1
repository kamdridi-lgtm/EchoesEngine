param(
    [string]$WorkspaceRoot = "D:\A.I\EchoesCinema",
    [int]$MaximumMinutes = 240
)

$ErrorActionPreference = "Continue"
Set-StrictMode -Version Latest

$workspace = [System.IO.Path]::GetFullPath($WorkspaceRoot)
$runtime = Join-Path $workspace "runtime"
$logs = Join-Path $workspace "logs"
$proofRoot = Join-Path $workspace "proofs\automatic-monitor"
$statePath = Join-Path $runtime "stack-state.json"
$monitorPath = Join-Path $runtime "one-click-monitor-status.json"
$startedUtc = [DateTime]::UtcNow
$deadline = (Get-Date).AddMinutes([math]::Max(1, $MaximumMinutes))

foreach ($directory in @($runtime, $logs, $proofRoot)) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}

function Write-AtomicJson {
    param([string]$Path, [hashtable]$Payload)
    $temporary = "$Path.$PID.tmp"
    $Payload | ConvertTo-Json -Depth 16 | Set-Content -LiteralPath $temporary -Encoding utf8
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

function Read-JsonFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    try { return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json } catch { return $null }
}

function Save-Evidence {
    param([string]$Reason)
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $stage = Join-Path $proofRoot "stage-$stamp"
    $zip = Join-Path $proofRoot "automatic-monitor-$stamp.zip"
    New-Item -ItemType Directory -Path $stage -Force | Out-Null
    foreach ($path in @(
        (Join-Path $runtime "stack-state.json"),
        (Join-Path $runtime "provider-worker-status.json"),
        (Join-Path $runtime "provider-health.json"),
        (Join-Path $runtime "p0-autopilot-status.json"),
        (Join-Path $runtime "one-click-monitor-status.json")
    )) {
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            Copy-Item -LiteralPath $path -Destination $stage -Force -ErrorAction SilentlyContinue
        }
    }
    Set-Content -LiteralPath (Join-Path $stage "reason.txt") -Value $Reason -Encoding utf8
    if (Test-Path -LiteralPath $logs -PathType Container) {
        Get-ChildItem -LiteralPath $logs -File -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 30 |
            Copy-Item -Destination $stage -Force -ErrorAction SilentlyContinue
    }
    try {
        Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $zip -Force
    } finally {
        Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
    }
    return $zip
}

$lastHealth = $null
$lastError = ""
$finalStatus = "PARTIAL"
$evidenceZip = $null

while ((Get-Date) -lt $deadline) {
    $stack = Read-JsonFile -Path $statePath
    $dashboardUrl = if ($stack -and $stack.dashboardUrl) { [string]$stack.dashboardUrl } else { "http://127.0.0.1:8090/" }
    $providerPort = if ($stack -and $stack.providerPort) { [int]$stack.providerPort } else { 8081 }
    $healthUrl = "http://127.0.0.1:$providerPort/health"

    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $healthUrl -TimeoutSec 5
        $lastHealth = $response.Content | ConvertFrom-Json
        $lastError = ""
    } catch {
        $lastHealth = $null
        $lastError = $_.Exception.Message
    }

    $realLoaded = $lastHealth -and $lastHealth.realModelLoaded -eq $true
    $loadState = if ($lastHealth -and $lastHealth.loadState) { [string]$lastHealth.loadState } else { "CONNECTING" }
    $failureClass = if ($lastHealth -and $lastHealth.failureClass) { [string]$lastHealth.failureClass } else { $null }
    $operatorAction = if ($lastHealth -and $lastHealth.operatorAction) { [string]$lastHealth.operatorAction } else { "Automatic recovery is still running." }

    Write-AtomicJson -Path $monitorPath -Payload @{
        schema = "echoes.cinema-one-click-monitor.v1"
        status = if ($realLoaded) { "REAL" } elseif ($loadState -eq "BLOCKED") { "BROKEN" } else { "PARTIAL" }
        timestampUtc = [DateTime]::UtcNow.ToString("o")
        startedUtc = $startedUtc.ToString("o")
        dashboardUrl = $dashboardUrl
        providerHealthUrl = $healthUrl
        realModelLoaded = [bool]$realLoaded
        loadState = $loadState
        failureClass = $failureClass
        operatorAction = $operatorAction
        lastConnectionError = if ($lastError) { $lastError } else { $null }
        evidenceZip = $evidenceZip
    }

    if ($realLoaded) {
        $finalStatus = "REAL"
        $evidenceZip = Save-Evidence -Reason "The real model reported realModelLoaded=true. P0 can continue automatically."
        break
    }
    if ($loadState -eq "BLOCKED") {
        $finalStatus = "BROKEN"
        $evidenceZip = Save-Evidence -Reason "Provider recovery entered BLOCKED state: $failureClass. $operatorAction"
        break
    }
    Start-Sleep -Seconds 10
}

if (-not $evidenceZip) {
    $evidenceZip = Save-Evidence -Reason "Automatic monitor reached its time limit while provider recovery remained incomplete."
}

Write-AtomicJson -Path $monitorPath -Payload @{
    schema = "echoes.cinema-one-click-monitor.v1"
    status = $finalStatus
    timestampUtc = [DateTime]::UtcNow.ToString("o")
    startedUtc = $startedUtc.ToString("o")
    completedUtc = [DateTime]::UtcNow.ToString("o")
    realModelLoaded = ($finalStatus -eq "REAL")
    lastConnectionError = if ($lastError) { $lastError } else { $null }
    evidenceZip = $evidenceZip
}
