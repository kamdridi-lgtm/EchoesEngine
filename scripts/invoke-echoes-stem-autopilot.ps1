[CmdletBinding()]
param(
    [string]$StemRuntimeRoot = "D:\A.I\EchoesStemRuntime",
    [string]$ResultsRoot = "D:\A.I\EchoesResults",
    [string]$ControlRoot = "D:\A.I\EchoesControl",
    [string]$AnalysisLedgerPath = "D:\A.I\EchoesControl\autopilot-ledger.json",
    [int]$MaxFiles = 2,
    [switch]$DeclareUserSong,
    [switch]$Interactive
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "Echoes Stem Autopilot currently supports Windows only"
}

$runtime = [IO.Path]::GetFullPath($StemRuntimeRoot)
$results = [IO.Path]::GetFullPath($ResultsRoot)
$control = [IO.Path]::GetFullPath($ControlRoot)
$analysisLedger = [IO.Path]::GetFullPath($AnalysisLedgerPath)
$python = Join-Path $runtime ".venv\Scripts\python.exe"
$core = Join-Path $runtime "tools\echoes_stem_autopilot.py"
$runtimeManifestPath = Join-Path $runtime "stem-runtime-manifest.json"
foreach ($required in @($python, $core, $analysisLedger, $runtimeManifestPath)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Echoes Stem Autopilot required file is missing: $required"
    }
}

$runtimeManifest = Get-Content -LiteralPath $runtimeManifestPath -Raw | ConvertFrom-Json
if ($runtimeManifest.status -ne "PASS") {
    throw "Echoes Stem runtime manifest is not PASS"
}
$ffprobe = [string]$runtimeManifest.ffmpeg.ffprobe
if (-not $ffprobe -or -not (Test-Path -LiteralPath $ffprobe -PathType Leaf)) {
    throw "Echoes Stem recorded ffprobe executable is missing: $ffprobe"
}
$ffprobeDirectory = Split-Path -Parent $ffprobe
$env:PATH = $ffprobeDirectory + [IO.Path]::PathSeparator + $env:PATH

$arguments = @(
    $core,
    "--stem-runtime-root", $runtime,
    "--results-root", $results,
    "--control-root", $control,
    "--analysis-ledger", $analysisLedger,
    "--max-files", [string]$MaxFiles
)
if ($DeclareUserSong) { $arguments += "--declare-user-song" }
if ($Interactive) { $arguments += "--interactive" }

& $python @arguments
$exitCode = $LASTEXITCODE
if ($exitCode -notin @(0, 2)) {
    throw "Echoes Stem Autopilot core failed with exit code $exitCode"
}

$latestReportPath = Join-Path $control "stem-autopilot-report-latest.json"
if (Test-Path -LiteralPath $latestReportPath -PathType Leaf) {
    $latestReport = Get-Content -LiteralPath $latestReportPath -Raw | ConvertFrom-Json
    $missingOrChangedSources = [int]$latestReport.summary.missingOrChangedSources
    if ($missingOrChangedSources -gt 0 -and $exitCode -eq 0) {
        Write-Error "Echoes Stem Autopilot found $missingOrChangedSources missing or changed approved source(s); success is blocked."
        $exitCode = 2
    }
}

exit $exitCode
