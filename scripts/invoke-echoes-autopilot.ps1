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

if ($env:OS -ne "Windows_NT") {
    throw "Echoes Autopilot currently supports Windows only"
}

$runtime = [IO.Path]::GetFullPath($RuntimeRoot)
$autopilot = [IO.Path]::GetFullPath($AutopilotRoot)
$inbox = [IO.Path]::GetFullPath($InboxRoot)
$results = [IO.Path]::GetFullPath($ResultsRoot)
$control = [IO.Path]::GetFullPath($ControlRoot)
$python = Join-Path $runtime ".venv\Scripts\python.exe"
$core = Join-Path $autopilot "echoes_autopilot.py"
$policy = if ($PolicyPath) { [IO.Path]::GetFullPath($PolicyPath) } else { Join-Path $autopilot "echoes-autopilot-policy.v1.json" }

foreach ($required in @($python, $core, $policy)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Echoes Autopilot installed file is missing: $required"
    }
}

$installationManifestPath = Join-Path $control "autopilot-installation-manifest.json"
if (Test-Path -LiteralPath $installationManifestPath -PathType Leaf) {
    $installationManifest = Get-Content -LiteralPath $installationManifestPath -Raw | ConvertFrom-Json
    $ffmpegPath = [string]$installationManifest.prerequisites.ffmpegPath
    if ($ffmpegPath -and (Test-Path -LiteralPath $ffmpegPath -PathType Leaf)) {
        $ffmpegDirectory = Split-Path -Parent $ffmpegPath
        $env:PATH = $ffmpegDirectory + [IO.Path]::PathSeparator + $env:PATH
    } elseif ($installationManifest.prerequisites.ffmpegDetected -eq $true) {
        throw "Echoes Autopilot recorded FFmpeg executable is missing: $ffmpegPath"
    }
}

$latestReportPath = Join-Path $control "autopilot-report-latest.json"
$previousReportStamp = if (Test-Path -LiteralPath $latestReportPath -PathType Leaf) {
    (Get-Item -LiteralPath $latestReportPath).LastWriteTimeUtc
} else {
    [DateTime]::MinValue
}

$arguments = @(
    $core,
    "--autopilot-root", $autopilot,
    "--runtime-root", $runtime,
    "--inbox-root", $inbox,
    "--results-root", $results,
    "--control-root", $control,
    "--policy", $policy,
    "--remote-policy-url", $RemotePolicyUrl
)
if ($Interactive) { $arguments += "--interactive" }
if ($SkipRemotePolicy) { $arguments += "--skip-remote-policy" }

& $python @arguments
$exitCode = $LASTEXITCODE
if ($exitCode -notin @(0, 2)) {
    throw "Echoes Autopilot core failed with exit code $exitCode"
}

if (-not (Test-Path -LiteralPath $latestReportPath -PathType Leaf)) {
    throw "Echoes Autopilot produced no latest report; controller exit $exitCode cannot be accepted"
}
$currentReportItem = Get-Item -LiteralPath $latestReportPath
if ($currentReportItem.LastWriteTimeUtc -le $previousReportStamp) {
    throw "Echoes Autopilot latest report was not refreshed; controller exit $exitCode cannot be accepted"
}
$latestReport = Get-Content -LiteralPath $latestReportPath -Raw | ConvertFrom-Json
if ($latestReport.schema -ne "echoes.autopilot-report.v1" -or $latestReport.status -notin @("PASS", "PARTIAL")) {
    throw "Echoes Autopilot produced an invalid report"
}
if ($latestReport.status -eq "PARTIAL" -and $exitCode -eq 0) {
    $exitCode = 2
}
if ($latestReport.status -eq "PASS" -and $exitCode -eq 2) {
    throw "Echoes Autopilot exit code and report status disagree"
}

$ledgerPath = Join-Path $control "autopilot-ledger.json"
$runtimeManifestPath = Join-Path $runtime "runtime-manifest.json"
$bundlePath = Join-Path $control "Echoes-Control-Bundle-Latest.zip"
if (Test-Path -LiteralPath $ledgerPath -PathType Leaf) {
    $ledger = Get-Content -LiteralPath $ledgerPath -Raw | ConvertFrom-Json
    $staging = Join-Path $control (".bundle-staging-" + [Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $staging | Out-Null
    try {
        Copy-Item -LiteralPath $latestReportPath -Destination (Join-Path $staging "autopilot-report-latest.json")
        Copy-Item -LiteralPath $ledgerPath -Destination (Join-Path $staging "autopilot-ledger.json")
        if (Test-Path -LiteralPath $runtimeManifestPath -PathType Leaf) {
            Copy-Item -LiteralPath $runtimeManifestPath -Destination (Join-Path $staging "runtime-manifest.json")
        }
        $logsDirectory = Join-Path $control "logs"
        if (Test-Path -LiteralPath $logsDirectory -PathType Container) {
            $latestRunLog = Get-ChildItem -LiteralPath $logsDirectory -Filter "autopilot-*.log" -File -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
            if ($latestRunLog) {
                $stagingLogs = Join-Path $staging "logs"
                New-Item -ItemType Directory -Force -Path $stagingLogs | Out-Null
                Copy-Item -LiteralPath $latestRunLog.FullName -Destination (Join-Path $stagingLogs "autopilot-run.log")
            }
        }
        foreach ($item in @($ledger.items)) {
            if ($item.status -notin @("PASS", "FAILED") -or -not $item.jobId) { continue }
            $jobId = [string]$item.jobId
            $jobStage = Join-Path $staging ("jobs\" + $jobId)
            New-Item -ItemType Directory -Force -Path $jobStage | Out-Null
            foreach ($copy in @(
                @{ Source = [string]$item.analysisManifestPath; Name = "analysis-run-manifest.json" },
                @{ Source = [string]$item.analysisLogPath; Name = "autopilot-invocation.log" },
                @{ Source = (Join-Path $results "$jobId\timeline\song-activity-timeline.json"); Name = "song-activity-timeline.json" },
                @{ Source = (Join-Path $results "$jobId\timeline\song-activity-timeline.csv"); Name = "song-activity-timeline.csv" }
            )) {
                if ($copy.Source -and (Test-Path -LiteralPath $copy.Source -PathType Leaf)) {
                    Copy-Item -LiteralPath $copy.Source -Destination (Join-Path $jobStage $copy.Name)
                }
            }
        }
        $temporaryBundle = Join-Path $control ("Echoes-Control-Bundle-" + [Guid]::NewGuid().ToString("N") + ".zip")
        Compress-Archive -Path (Join-Path $staging "*") -DestinationPath $temporaryBundle -CompressionLevel Optimal -Force
        Move-Item -LiteralPath $temporaryBundle -Destination $bundlePath -Force
    } finally {
        Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
    }
}

exit $exitCode
