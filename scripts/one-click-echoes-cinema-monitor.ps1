param(
    [string]$WorkspaceRoot = "D:\A.I\EchoesCinema",
    [int]$MaximumMinutes = 240,
    [switch]$ProgressPolicySelfTest
)

$ErrorActionPreference = "Continue"
Set-StrictMode -Version Latest

function Get-ObjectProperty {
    param(
        [object]$Object,
        [string]$Name,
        [object]$DefaultValue = $null
    )
    if ($null -eq $Object) { return $DefaultValue }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $DefaultValue }
    return $property.Value
}

function Get-ProviderProgressSignature {
    param([object]$Health)
    if ($null -eq $Health) { return $null }
    $signature = [ordered]@{
        loadState = [string](Get-ObjectProperty -Object $Health -Name "loadState" -DefaultValue "CONNECTING")
        realModelLoaded = [bool](Get-ObjectProperty -Object $Health -Name "realModelLoaded" -DefaultValue $false)
        recoveryCount = [int](Get-ObjectProperty -Object $Health -Name "recoveryCount" -DefaultValue 0)
        consecutiveSameFailure = [int](Get-ObjectProperty -Object $Health -Name "consecutiveSameFailure" -DefaultValue 0)
        lastAttemptUtc = Get-ObjectProperty -Object $Health -Name "lastAttemptUtc" -DefaultValue $null
        nextRetryUtc = Get-ObjectProperty -Object $Health -Name "nextRetryUtc" -DefaultValue $null
        modelCacheBytes = Get-ObjectProperty -Object $Health -Name "modelCacheBytes" -DefaultValue $null
        modelCacheGiB = Get-ObjectProperty -Object $Health -Name "modelCacheGiB" -DefaultValue $null
        workerStatus = Get-ObjectProperty -Object $Health -Name "workerStatus" -DefaultValue $null
    }
    return ($signature | ConvertTo-Json -Compress -Depth 4)
}

function Get-MonitorDecision {
    param(
        [bool]$RealLoaded,
        [string]$LoadState,
        [DateTime]$NowUtc,
        [DateTime]$InactivityDeadlineUtc
    )
    if ($RealLoaded) { return "REAL" }
    if ($LoadState -eq "BLOCKED") { return "BLOCKED" }
    if ($NowUtc -ge $InactivityDeadlineUtc) { return "INACTIVE" }
    return "CONTINUE"
}

if ($ProgressPolicySelfTest) {
    $health = [pscustomobject]@{
        loadState = "LOADING"
        realModelLoaded = $false
        recoveryCount = 1
        consecutiveSameFailure = 0
        lastAttemptUtc = "2026-07-25T00:00:00Z"
        nextRetryUtc = $null
        modelCacheBytes = 100
        modelCacheGiB = 0.1
        workspaceFreeGiB = 50.0
        workerStatus = "MODEL_LOADING"
    }
    $first = Get-ProviderProgressSignature -Health $health
    $health.workspaceFreeGiB = 49.5
    if ((Get-ProviderProgressSignature -Health $health) -ne $first) {
        throw "Unrelated free-space changes must not count as model progress."
    }
    $health.modelCacheBytes = 101
    if ((Get-ProviderProgressSignature -Health $health) -eq $first) {
        throw "Model-cache growth must count as provider progress."
    }
    $now = [DateTime]::UtcNow
    if ((Get-MonitorDecision -RealLoaded $true -LoadState "READY" -NowUtc $now -InactivityDeadlineUtc $now.AddMinutes(-1)) -ne "REAL") {
        throw "REAL decision contract failed."
    }
    if ((Get-MonitorDecision -RealLoaded $false -LoadState "BLOCKED" -NowUtc $now -InactivityDeadlineUtc $now.AddMinutes(1)) -ne "BLOCKED") {
        throw "BLOCKED decision contract failed."
    }
    if ((Get-MonitorDecision -RealLoaded $false -LoadState "LOADING" -NowUtc $now -InactivityDeadlineUtc $now.AddSeconds(-1)) -ne "INACTIVE") {
        throw "Inactivity decision contract failed."
    }
    if ((Get-MonitorDecision -RealLoaded $false -LoadState "RETRY_WAIT" -NowUtc $now -InactivityDeadlineUtc $now.AddMinutes(1)) -ne "CONTINUE") {
        throw "Progress wait decision contract failed."
    }
    Write-Host "Echoes Cinema one-click monitor PASS policy=progress-aware inactivity-only free-space=ignored cache-growth=progress"
    exit 0
}

$workspace = [System.IO.Path]::GetFullPath($WorkspaceRoot)
$runtime = Join-Path $workspace "runtime"
$logs = Join-Path $workspace "logs"
$proofRoot = Join-Path $workspace "proofs\automatic-monitor"
$statePath = Join-Path $runtime "stack-state.json"
$monitorPath = Join-Path $runtime "one-click-monitor-status.json"
$startedUtc = [DateTime]::UtcNow
$inactivityMinutes = [math]::Max(1, $MaximumMinutes)
$inactivityDeadlineUtc = $startedUtc.AddMinutes($inactivityMinutes)
$lastProgressUtc = $startedUtc
$lastProgressSignature = $null

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
$finalReason = ""
$evidenceZip = $null

while ($true) {
    $stack = Read-JsonFile -Path $statePath
    $dashboardUrl = [string](Get-ObjectProperty -Object $stack -Name "dashboardUrl" -DefaultValue "http://127.0.0.1:8090/")
    $providerPort = [int](Get-ObjectProperty -Object $stack -Name "providerPort" -DefaultValue 8081)
    $stackStatus = [string](Get-ObjectProperty -Object $stack -Name "status" -DefaultValue "PARTIAL")
    $healthUrl = "http://127.0.0.1:$providerPort/health"

    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $healthUrl -TimeoutSec 5
        $lastHealth = $response.Content | ConvertFrom-Json
        $lastError = ""
    } catch {
        $lastHealth = $null
        $lastError = $_.Exception.Message
    }

    $nowUtc = [DateTime]::UtcNow
    $progressSignature = Get-ProviderProgressSignature -Health $lastHealth
    if ($progressSignature -and $progressSignature -ne $lastProgressSignature) {
        $lastProgressSignature = $progressSignature
        $lastProgressUtc = $nowUtc
        $inactivityDeadlineUtc = $nowUtc.AddMinutes($inactivityMinutes)
    }

    $realLoaded = [bool](Get-ObjectProperty -Object $lastHealth -Name "realModelLoaded" -DefaultValue $false)
    $loadState = [string](Get-ObjectProperty -Object $lastHealth -Name "loadState" -DefaultValue "CONNECTING")
    $failureClass = Get-ObjectProperty -Object $lastHealth -Name "failureClass" -DefaultValue $null
    $operatorAction = [string](Get-ObjectProperty -Object $lastHealth -Name "operatorAction" -DefaultValue "Automatic recovery is still running.")
    $decision = Get-MonitorDecision -RealLoaded $realLoaded -LoadState $loadState -NowUtc $nowUtc -InactivityDeadlineUtc $inactivityDeadlineUtc

    $currentStatus = "PARTIAL"
    if ($decision -eq "REAL") {
        $currentStatus = "REAL"
    } elseif ($decision -eq "BLOCKED") {
        $currentStatus = "BROKEN"
    }
    $connectionError = if ($lastError) { $lastError } else { $null }
    $remainingSeconds = [math]::Max(0, [int][math]::Floor(($inactivityDeadlineUtc - $nowUtc).TotalSeconds))

    Write-AtomicJson -Path $monitorPath -Payload @{
        schema = "echoes.cinema-one-click-monitor.v2"
        status = $currentStatus
        timestampUtc = $nowUtc.ToString("o")
        startedUtc = $startedUtc.ToString("o")
        dashboardUrl = $dashboardUrl
        providerHealthUrl = $healthUrl
        realModelLoaded = $realLoaded
        loadState = $loadState
        failureClass = $failureClass
        operatorAction = $operatorAction
        lastConnectionError = $connectionError
        stackStatus = $stackStatus
        monitorPolicy = "PROGRESS_AWARE_INACTIVITY_TIMEOUT"
        inactivityTimeoutMinutes = $inactivityMinutes
        inactivityRemainingSeconds = $remainingSeconds
        lastProgressUtc = $lastProgressUtc.ToString("o")
        evidenceZip = $evidenceZip
    }

    if ($decision -eq "REAL") {
        $finalStatus = "REAL"
        $finalReason = "The real model reported realModelLoaded=true. P0 can continue automatically."
        $evidenceZip = Save-Evidence -Reason $finalReason
        break
    }
    if ($decision -eq "BLOCKED") {
        $finalStatus = "BROKEN"
        $finalReason = "Provider recovery entered BLOCKED state: $failureClass. $operatorAction"
        $evidenceZip = Save-Evidence -Reason $finalReason
        break
    }
    if ($decision -eq "INACTIVE") {
        $finalStatus = "PARTIAL"
        $finalReason = "No provider recovery progress was observed for $inactivityMinutes minutes. The control center and P0 autopilot remain independent; evidence was preserved. Last connection error: $lastError"
        $evidenceZip = Save-Evidence -Reason $finalReason
        break
    }
    if ($stackStatus -eq "STOPPED") {
        $finalStatus = "PARTIAL"
        $finalReason = "The Echoes Cinema stack stopped before the provider became ready. Evidence was preserved."
        $evidenceZip = Save-Evidence -Reason $finalReason
        break
    }
    Start-Sleep -Seconds 10
}

$finalConnectionError = if ($lastError) { $lastError } else { $null }
Write-AtomicJson -Path $monitorPath -Payload @{
    schema = "echoes.cinema-one-click-monitor.v2"
    status = $finalStatus
    timestampUtc = [DateTime]::UtcNow.ToString("o")
    startedUtc = $startedUtc.ToString("o")
    completedUtc = [DateTime]::UtcNow.ToString("o")
    realModelLoaded = ($finalStatus -eq "REAL")
    monitorPolicy = "PROGRESS_AWARE_INACTIVITY_TIMEOUT"
    inactivityTimeoutMinutes = $inactivityMinutes
    lastProgressUtc = $lastProgressUtc.ToString("o")
    lastConnectionError = $finalConnectionError
    finalReason = $finalReason
    evidenceZip = $evidenceZip
}
