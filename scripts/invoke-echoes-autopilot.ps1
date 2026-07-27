[CmdletBinding()]
param(
    [string]$AutopilotRoot = "D:\A.I\EchoesAutopilot",
    [string]$RuntimeRoot = "D:\A.I\EchoesEngineRuntime",
    [string]$InboxRoot = "D:\A.I\EchoesInbox",
    [string]$ResultsRoot = "D:\A.I\EchoesResults",
    [string]$ControlRoot = "D:\A.I\EchoesControl",
    [string]$PolicyPath = "",
    [string]$RemotePolicyUrl = "https://raw.githubusercontent.com/kamdridi-lgtm/EchoesEngine/main/config/echoes-autopilot-policy.v1.json",
    [switch]$Interactive,
    [switch]$SkipRemotePolicy
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$LedgerSchema = "echoes.autopilot-ledger.v1"
$ReportSchema = "echoes.autopilot-report.v1"
$AllowedExtensionCeiling = @(".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg")

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Write-JsonAtomic([object]$Value, [string]$Path, [int]$Depth = 16) {
    $parent = Split-Path -Parent $Path
    if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    $temporary = "$Path.tmp-$PID"
    $Value | ConvertTo-Json -Depth $Depth | Set-Content -LiteralPath $temporary -Encoding utf8
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

function Read-JsonObject([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label is missing: $Path"
    }
    try {
        return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    } catch {
        throw "$Label is invalid JSON: $Path"
    }
}

function Assert-SafePolicy([object]$Policy) {
    if ($Policy.schema -ne "echoes.autopilot-policy.v1") { throw "Unsupported autopilot policy schema" }
    if ([int]$Policy.scanIntervalMinutes -lt 1 -or [int]$Policy.scanIntervalMinutes -gt 60) { throw "Policy scan interval is outside 1-60 minutes" }
    if ([int]$Policy.maxFilesPerRun -lt 1 -or [int]$Policy.maxFilesPerRun -gt 100) { throw "Policy maxFilesPerRun is outside 1-100" }
    $extensions = @($Policy.allowedExtensions | ForEach-Object { ([string]$_).ToLowerInvariant() })
    foreach ($extension in $extensions) {
        if ($AllowedExtensionCeiling -notcontains $extension) { throw "Policy requested unsupported extension: $extension" }
    }
    if ($Policy.safety.allowAudioUpload -ne $false) { throw "Policy may not authorize audio upload" }
    if ($Policy.safety.allowSourceDeletion -ne $false) { throw "Policy may not authorize source deletion" }
    if ($Policy.safety.allowArbitraryCommands -ne $false) { throw "Policy may not authorize arbitrary commands" }
    if ($Policy.safety.requireHashLedger -ne $true) { throw "Policy must require the hash ledger" }
    if ($Policy.safety.requireOperatorApprovalForExecution -ne $true) { throw "Policy must preserve operator approval" }
    return $extensions
}

function New-EmptyLedger {
    return [ordered]@{
        schema = $LedgerSchema
        version = 1
        updatedAtUtc = [DateTime]::UtcNow.ToString("o")
        items = @()
    }
}

function Save-Ledger([object]$Ledger, [string]$Path) {
    $Ledger.updatedAtUtc = [DateTime]::UtcNow.ToString("o")
    Write-JsonAtomic $Ledger $Path 20
}

function Safe-BaseName([string]$Path) {
    $name = [regex]::Replace([IO.Path]::GetFileNameWithoutExtension($Path), "[^A-Za-z0-9_-]+", "-").Trim("-")
    if (-not $name) { $name = "audio" }
    if ($name.Length -gt 48) { $name = $name.Substring(0, 48).TrimEnd("-") }
    return $name
}

function Add-BundleFile([string]$Source, [string]$DestinationRoot, [string]$RelativeName) {
    if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) { return }
    $destination = Join-Path $DestinationRoot $RelativeName
    $parent = Split-Path -Parent $destination
    if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    Copy-Item -LiteralPath $Source -Destination $destination -Force
}

if ($env:OS -ne "Windows_NT") {
    throw "Echoes Autopilot currently supports Windows only"
}

$autopilot = [IO.Path]::GetFullPath($AutopilotRoot)
$runtime = [IO.Path]::GetFullPath($RuntimeRoot)
$inbox = [IO.Path]::GetFullPath($InboxRoot)
$results = [IO.Path]::GetFullPath($ResultsRoot)
$control = [IO.Path]::GetFullPath($ControlRoot)
foreach ($directory in @($autopilot, $inbox, $results, $control, (Join-Path $control "logs"))) {
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
}

$lockPath = Join-Path $control "autopilot.lock"
$lockStream = $null
try {
    $lockStream = [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
} catch {
    Write-Host "EchoesAutopilot SKIP reason=another-controller-is-running"
    exit 0
}

try {
    $localPolicyPath = if ($PolicyPath) { [IO.Path]::GetFullPath($PolicyPath) } else { Join-Path $autopilot "echoes-autopilot-policy.v1.json" }
    $policy = Read-JsonObject $localPolicyPath "Local autopilot policy"
    $policySource = "local"
    $remotePolicyApplied = $false
    $remotePolicyError = $null
    [void](Assert-SafePolicy $policy)

    if (-not $SkipRemotePolicy -and $RemotePolicyUrl) {
        try {
            $remoteText = (Invoke-WebRequest -UseBasicParsing -Uri $RemotePolicyUrl -TimeoutSec 20).Content
            $remoteCandidate = $remoteText | ConvertFrom-Json
            [void](Assert-SafePolicy $remoteCandidate)
            $policy = $remoteCandidate
            $policySource = "github-approved"
            $remotePolicyApplied = $true
            $cachePath = Join-Path $control "remote-policy-cache.json"
            Write-JsonAtomic $remoteCandidate $cachePath 12
        } catch {
            $remotePolicyError = $_.Exception.Message
        }
    }

    $allowedExtensions = @(Assert-SafePolicy $policy)
    $runtimeManifestPath = Join-Path $runtime "runtime-manifest.json"
    $analyzerPath = Join-Path $runtime "Analyze-EchoesSong.ps1"
    foreach ($required in @($runtimeManifestPath, $analyzerPath)) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
            throw "Installed runtime is incomplete: $required"
        }
    }
    $runtimeManifest = Read-JsonObject $runtimeManifestPath "Runtime manifest"
    if ($runtimeManifest.schema -ne "echoes.local-song-activity-runtime-installation.v1" -or $runtimeManifest.status -ne "PASS") {
        throw "Installed runtime manifest is not PASS"
    }

    $ledgerPath = Join-Path $control "autopilot-ledger.json"
    $ledger = if (Test-Path -LiteralPath $ledgerPath -PathType Leaf) { Read-JsonObject $ledgerPath "Autopilot ledger" } else { New-EmptyLedger }
    if ($ledger.schema -ne $LedgerSchema) { throw "Unsupported autopilot ledger schema" }
    $ledgerItems = @($ledger.items)

    $startedAt = [DateTime]::UtcNow
    $runId = $startedAt.ToString("yyyyMMddTHHmmssfffZ")
    $runLogPath = Join-Path (Join-Path $control "logs") "autopilot-$runId.log"
    $discovered = @(
        Get-ChildItem -LiteralPath $inbox -File -ErrorAction SilentlyContinue |
            Where-Object { $allowedExtensions -contains $_.Extension.ToLowerInvariant() } |
            Sort-Object LastWriteTimeUtc, FullName
    )

    $reportItems = @()
    $processedCount = 0
    $successCount = 0
    $failureCount = 0
    $duplicateCount = 0
    $seenThisRun = @{}

    if ($policy.enabled -eq $true) {
        foreach ($file in $discovered) {
            $sha = Get-Sha256 $file.FullName
            if ($seenThisRun.ContainsKey($sha)) {
                $duplicateCount++
                $reportItems += [ordered]@{ path = $file.FullName; sha256 = $sha; status = "SKIPPED_DUPLICATE_HASH"; canonicalPath = $seenThisRun[$sha] }
                continue
            }
            $seenThisRun[$sha] = $file.FullName

            $existing = @($ledgerItems | Where-Object { $_.sha256 -eq $sha } | Select-Object -First 1)
            if ($existing.Count -gt 0 -and $existing[0].status -eq "PASS") {
                $reportItems += [ordered]@{ path = $file.FullName; sha256 = $sha; status = "ALREADY_PROCESSED"; jobId = $existing[0].jobId }
                continue
            }
            if ($processedCount -ge [int]$policy.maxFilesPerRun) {
                $reportItems += [ordered]@{ path = $file.FullName; sha256 = $sha; status = "DEFERRED_MAX_FILES" }
                continue
            }

            $processedCount++
            $priorAttempts = 0
            if ($existing.Count -gt 0) { $priorAttempts = [int]$existing[0].attempts }
            $attempt = $priorAttempts + 1
            $jobId = "$(Safe-BaseName $file.FullName)-$($sha.Substring(0, 12))-a$attempt"
            $jobDirectory = Join-Path $results $jobId
            $analysisManifestPath = Join-Path $jobDirectory "analysis-run-manifest.json"
            $analysisLogPath = Join-Path $jobDirectory "autopilot-invocation.log"
            New-Item -ItemType Directory -Force -Path $jobDirectory | Out-Null

            $arguments = @(
                "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $analyzerPath,
                "-InputPath", $file.FullName,
                "-RuntimeRoot", $runtime,
                "-OutputRoot", $results,
                "-JobId", $jobId,
                "-ExpectedInputSha256", $sha
            )
            if ($policy.declareUserSong -eq $true) { $arguments += "-DeclareUserSong" }

            $output = & powershell.exe @arguments 2>&1
            $exitCode = $LASTEXITCODE
            $output | Set-Content -LiteralPath $analysisLogPath -Encoding utf8
            $status = "FAILED"
            $errorText = $null
            $canonicalSha = $null
            if ($exitCode -eq 0 -and (Test-Path -LiteralPath $analysisManifestPath -PathType Leaf)) {
                try {
                    $analysisManifest = Read-JsonObject $analysisManifestPath "Analysis manifest"
                    if ($analysisManifest.status -eq "PASS" -and $analysisManifest.source.sha256 -eq $sha) {
                        $status = "PASS"
                        $canonicalSha = $analysisManifest.timeline.canonicalSha256
                    } else {
                        $errorText = "Analysis manifest status or input SHA mismatch"
                    }
                } catch {
                    $errorText = $_.Exception.Message
                }
            } else {
                $errorText = "Analyzer exit code $exitCode"
            }

            $ledgerItems = @($ledgerItems | Where-Object { $_.sha256 -ne $sha })
            $ledgerRecord = [ordered]@{
                sha256 = $sha
                sourcePath = $file.FullName
                sourceName = $file.Name
                sizeBytes = $file.Length
                status = $status
                attempts = $attempt
                jobId = $jobId
                analysisManifestPath = $analysisManifestPath
                analysisLogPath = $analysisLogPath
                canonicalTimelineSha256 = $canonicalSha
                lastAttemptAtUtc = [DateTime]::UtcNow.ToString("o")
                error = $errorText
            }
            $ledgerItems += [pscustomobject]$ledgerRecord
            $ledger.items = $ledgerItems
            Save-Ledger $ledger $ledgerPath

            if ($status -eq "PASS") { $successCount++ } else { $failureCount++ }
            $reportItems += $ledgerRecord
            "$([DateTime]::UtcNow.ToString('o')) $status sha=$sha job=$jobId path=$($file.FullName)" | Add-Content -LiteralPath $runLogPath -Encoding utf8
        }
    }

    $finishedAt = [DateTime]::UtcNow
    $report = [ordered]@{
        schema = $ReportSchema
        status = if ($failureCount -gt 0) { "PARTIAL" } else { "PASS" }
        runId = $runId
        startedAtUtc = $startedAt.ToString("o")
        finishedAtUtc = $finishedAt.ToString("o")
        durationSeconds = [math]::Round(($finishedAt - $startedAt).TotalSeconds, 3)
        policy = [ordered]@{
            source = $policySource
            version = $policy.version
            enabled = [bool]$policy.enabled
            maxFilesPerRun = [int]$policy.maxFilesPerRun
            remotePolicyApplied = $remotePolicyApplied
            remotePolicyError = $remotePolicyError
        }
        paths = [ordered]@{
            autopilotRoot = $autopilot
            runtimeRoot = $runtime
            inboxRoot = $inbox
            resultsRoot = $results
            controlRoot = $control
        }
        summary = [ordered]@{
            discoveredFiles = $discovered.Count
            attemptedFiles = $processedCount
            successfulFiles = $successCount
            failedFiles = $failureCount
            duplicateHashFiles = $duplicateCount
            ledgerItems = @($ledgerItems).Count
        }
        items = $reportItems
        truthBoundary = [ordered]@{
            currentHostControllerExecuted = $true
            scheduledExecutionObserved = (-not $Interactive)
            remotePolicyControlObserved = $remotePolicyApplied
            userSongAnalyzed = ($successCount -gt 0 -and $policy.declareUserSong -eq $true)
            hpOmenExecutionProven = $false
            sourceAudioDeleted = $false
            sourceAudioUploaded = $false
            instrumentalClassificationProven = $false
            vocalIsolationProven = $false
            stemSeparationProven = $false
            voiceConversionProven = $false
            gpuInferenceProven = $false
            tensorRtInferenceProven = $false
        }
    }

    $latestReportPath = Join-Path $control "autopilot-report-latest.json"
    $timestampedReportPath = Join-Path $control "autopilot-report-$runId.json"
    Write-JsonAtomic $report $latestReportPath 24
    Write-JsonAtomic $report $timestampedReportPath 24
    Save-Ledger $ledger $ledgerPath

    if ($policy.createControlBundle -eq $true) {
        $staging = Join-Path $control "bundle-staging-$runId"
        if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
        New-Item -ItemType Directory -Force -Path $staging | Out-Null
        Add-BundleFile $latestReportPath $staging "autopilot-report-latest.json"
        Add-BundleFile $ledgerPath $staging "autopilot-ledger.json"
        Add-BundleFile $runtimeManifestPath $staging "runtime-manifest.json"
        Add-BundleFile $runLogPath $staging "logs\autopilot-run.log"
        foreach ($item in $reportItems) {
            if ($item.status -eq "PASS" -or $item.status -eq "FAILED") {
                $safeJob = [string]$item.jobId
                Add-BundleFile ([string]$item.analysisManifestPath) $staging "jobs\$safeJob\analysis-run-manifest.json"
                Add-BundleFile ([string]$item.analysisLogPath) $staging "jobs\$safeJob\autopilot-invocation.log"
                $timelineJson = Join-Path (Join-Path $results $safeJob) "timeline\song-activity-timeline.json"
                $timelineCsv = Join-Path (Join-Path $results $safeJob) "timeline\song-activity-timeline.csv"
                Add-BundleFile $timelineJson $staging "jobs\$safeJob\song-activity-timeline.json"
                Add-BundleFile $timelineCsv $staging "jobs\$safeJob\song-activity-timeline.csv"
            }
        }
        $bundlePath = Join-Path $control "Echoes-Control-Bundle-Latest.zip"
        if (Test-Path -LiteralPath $bundlePath) { Remove-Item -LiteralPath $bundlePath -Force }
        Compress-Archive -Path (Join-Path $staging "*") -DestinationPath $bundlePath -CompressionLevel Optimal
        Remove-Item -LiteralPath $staging -Recurse -Force
    }

    $statusText = @(
        "ECHOES AUTOPILOT",
        "Status: $($report.status)",
        "Run: $runId",
        "Discovered: $($report.summary.discoveredFiles)",
        "Attempted: $($report.summary.attemptedFiles)",
        "Successful: $($report.summary.successfulFiles)",
        "Failed: $($report.summary.failedFiles)",
        "Inbox: $inbox",
        "Results: $results",
        "Control bundle: $(Join-Path $control 'Echoes-Control-Bundle-Latest.zip')"
    ) -join [Environment]::NewLine
    $statusText | Set-Content -LiteralPath (Join-Path $control "STATUS.txt") -Encoding utf8

    Write-Host "EchoesAutopilot $($report.status) discovered=$($report.summary.discoveredFiles) attempted=$processedCount success=$successCount failed=$failureCount policy=$policySource audio-upload=false source-delete=false"
    Write-Host "Inbox:   $inbox"
    Write-Host "Results: $results"
    Write-Host "Control: $(Join-Path $control 'Echoes-Control-Bundle-Latest.zip')"

    if ($Interactive -and $policy.openResultsOnInteractiveRun -eq $true) {
        if ($discovered.Count -eq 0) { Start-Process explorer.exe $inbox } else { Start-Process explorer.exe $results }
    }

    if ($failureCount -gt 0) { exit 2 }
    exit 0
} finally {
    if ($null -ne $lockStream) { $lockStream.Dispose() }
}
