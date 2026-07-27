[CmdletBinding()]
param(
    [string]$SourceRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$StemRuntimeRoot = "D:\A.I\EchoesStemRuntime",
    [string]$ControlRoot = "D:\A.I\EchoesControl"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "Echoes Stem QC update currently supports Windows only"
}

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

$source = [IO.Path]::GetFullPath($SourceRoot)
$runtime = [IO.Path]::GetFullPath($StemRuntimeRoot)
$control = [IO.Path]::GetFullPath($ControlRoot)
$python = Join-Path $runtime ".venv\Scripts\python.exe"
$runtimeManifestPath = Join-Path $runtime "stem-runtime-manifest.json"
$ledgerPath = Join-Path $control "stem-autopilot-ledger.json"
$reportPath = Join-Path $control "stem-autopilot-report-latest.json"

foreach ($required in @($python, $runtimeManifestPath, $ledgerPath, $reportPath)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Stem QC update prerequisite is missing: $required"
    }
}

$copyMap = [ordered]@{
    "tools\analyze_stem_quality.py" = "tools\analyze_stem_quality.py"
    "tools\normalize_stem_truth_and_qc.py" = "tools\normalize_stem_truth_and_qc.py"
}
foreach ($sourceRelative in $copyMap.Keys) {
    $sourcePath = Join-Path $source $sourceRelative
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
        throw "Stem QC source file is missing: $sourcePath"
    }
    $destination = Join-Path $runtime $copyMap[$sourceRelative]
    $parent = Split-Path -Parent $destination
    if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    Copy-Item -LiteralPath $sourcePath -Destination $destination -Force
}

$launcherPath = Join-Path $runtime "Run-Echoes-Stems.ps1"
$launcher = @'
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
if ($env:OS -ne "Windows_NT") { throw "Echoes Stem Autopilot currently supports Windows only" }
$runtime = [IO.Path]::GetFullPath($StemRuntimeRoot)
$results = [IO.Path]::GetFullPath($ResultsRoot)
$control = [IO.Path]::GetFullPath($ControlRoot)
$analysisLedger = [IO.Path]::GetFullPath($AnalysisLedgerPath)
$python = Join-Path $runtime ".venv\Scripts\python.exe"
$core = Join-Path $runtime "tools\echoes_stem_autopilot.py"
$normalizer = Join-Path $runtime "tools\normalize_stem_truth_and_qc.py"
foreach ($required in @($python, $core, $normalizer, $analysisLedger)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Echoes Stem Autopilot QC required file is missing: $required" }
}
$coreArguments = @(
    $core,
    "--stem-runtime-root", $runtime,
    "--results-root", $results,
    "--control-root", $control,
    "--analysis-ledger", $analysisLedger,
    "--max-files", [string]$MaxFiles
)
if ($DeclareUserSong) { $coreArguments += "--declare-user-song" }
if ($Interactive) { $coreArguments += "--interactive" }
& $python @coreArguments
$coreExit = $LASTEXITCODE
if ($coreExit -notin @(0, 2)) { throw "Echoes Stem Autopilot core failed with exit code $coreExit" }
& $python $normalizer --stem-runtime-root $runtime --control-root $control
$qualityExit = $LASTEXITCODE
if ($qualityExit -notin @(0, 2)) { throw "Echoes Stem technical QC failed with exit code $qualityExit" }
if ($coreExit -eq 2 -or $qualityExit -eq 2) { exit 2 }
exit 0
'@
$launcher | Set-Content -LiteralPath $launcherPath -Encoding utf8

$runtimeManifest = Get-Content -LiteralPath $runtimeManifestPath -Raw | ConvertFrom-Json
if ($null -eq $runtimeManifest.installedFileSha256) {
    $runtimeManifest | Add-Member -NotePropertyName installedFileSha256 -NotePropertyValue ([pscustomobject]@{})
}
$hashes = [ordered]@{}
foreach ($relative in @(
    "Run-Echoes-Stems.ps1",
    "tools\analyze_stem_quality.py",
    "tools\normalize_stem_truth_and_qc.py"
)) {
    $hashes[$relative] = Get-Sha256 (Join-Path $runtime $relative)
}
foreach ($key in $hashes.Keys) {
    $existing = $runtimeManifest.installedFileSha256.PSObject.Properties[$key]
    if ($null -eq $existing) {
        $runtimeManifest.installedFileSha256 | Add-Member -NotePropertyName $key -NotePropertyValue $hashes[$key]
    } else {
        $existing.Value = $hashes[$key]
    }
}
$runtimeManifest | Add-Member -Force -NotePropertyName qcUpdate -NotePropertyValue ([pscustomobject]@{
    schema = "echoes.stem-qc-update.v1"
    status = "INSTALLED"
    installedAtUtc = [DateTime]::UtcNow.ToString("o")
    launcher = $launcherPath
    files = $hashes
})
$runtimeManifest | ConvertTo-Json -Depth 14 | Set-Content -LiteralPath $runtimeManifestPath -Encoding utf8

& $python (Join-Path $runtime "tools\normalize_stem_truth_and_qc.py") --stem-runtime-root $runtime --control-root $control
$exitCode = $LASTEXITCODE
if ($exitCode -notin @(0, 2)) { throw "Initial Stem QC normalization failed with exit code $exitCode" }
Write-Host "EchoesStemQcUpdate installed runtime=$runtime status=$exitCode"
exit $exitCode
