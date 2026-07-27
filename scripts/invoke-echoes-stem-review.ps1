[CmdletBinding()]
param(
    [string]$StemRuntimeRoot = "D:\A.I\EchoesStemRuntime",
    [string]$ControlRoot = "D:\A.I\EchoesControl",
    [string]$StemJobId,
    [string]$SourceSha256,
    [ValidateSet("approve", "reject")]
    [string]$Decision,
    [string]$Reviewer,
    [string]$Notes = "",
    [switch]$ConfirmListenedToVocals,
    [switch]$ConfirmListenedToInstrumental,
    [switch]$DeclareUserSong,
    [string]$PythonExecutable,
    [switch]$Interactive
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "Echoes stem listening review currently supports Windows only"
}

function Get-FullPath([string]$Path) {
    return [IO.Path]::GetFullPath($Path)
}

function Write-JsonAtomic([string]$Path, [object]$Value) {
    $parent = Split-Path -Parent $Path
    if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    $temporary = "$Path.tmp-$PID"
    $Value | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $temporary -Encoding utf8
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

$runtime = Get-FullPath $StemRuntimeRoot
$control = Get-FullPath $ControlRoot
$ledgerPath = Join-Path $control "stem-autopilot-ledger.json"
$runtimeManifestPath = Join-Path $runtime "stem-runtime-manifest.json"
$tool = Join-Path $runtime "tools\review_stem_acapella.py"

if ([string]::IsNullOrWhiteSpace($PythonExecutable)) {
    $PythonExecutable = Join-Path $runtime ".venv\Scripts\python.exe"
}
$python = Get-FullPath $PythonExecutable
foreach ($required in @($ledgerPath, $runtimeManifestPath, $tool, $python)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Echoes stem review required file is missing: $required"
    }
}

$ledger = Get-Content -LiteralPath $ledgerPath -Raw | ConvertFrom-Json
if ($ledger.schema -ne "echoes.stem-autopilot-ledger.v1") {
    throw "Unsupported stem ledger schema"
}
$passItems = @($ledger.items | Where-Object { $_.status -eq "PASS" })
if ($passItems.Count -eq 0) {
    throw "No PASS stem set is available for listening review"
}

if ($Interactive) {
    if ([string]::IsNullOrWhiteSpace($StemJobId) -and [string]::IsNullOrWhiteSpace($SourceSha256)) {
        if ($passItems.Count -eq 1) {
            $StemJobId = [string]$passItems[0].stemJobId
        } else {
            Write-Host "Available PASS stem jobs:"
            foreach ($entry in $passItems) {
                Write-Host ("  {0}  {1}" -f $entry.stemJobId, $entry.sourceName)
            }
            $StemJobId = Read-Host "Stem Job ID to review"
        }
    }
    if ([string]::IsNullOrWhiteSpace($Decision)) {
        $Decision = (Read-Host "Decision: approve or reject").Trim().ToLowerInvariant()
    }
    if ([string]::IsNullOrWhiteSpace($Reviewer)) {
        $Reviewer = Read-Host "Reviewer name"
    }
    if ([string]::IsNullOrWhiteSpace($Notes)) {
        $Notes = Read-Host "Review notes"
    }
    if ($Decision -eq "approve") {
        $vocalsAnswer = (Read-Host "Did you listen to vocals.wav? Type YES").Trim()
        $instrumentalAnswer = (Read-Host "Did you listen to the instrumental stems? Type YES").Trim()
        if ($vocalsAnswer -eq "YES") { $ConfirmListenedToVocals = $true }
        if ($instrumentalAnswer -eq "YES") { $ConfirmListenedToInstrumental = $true }
    }
}

if ($Decision -notin @("approve", "reject")) {
    throw "Decision must be approve or reject"
}
if ([string]::IsNullOrWhiteSpace($Reviewer)) {
    throw "Reviewer identity is required"
}

$candidates = @($passItems | Where-Object {
    (-not [string]::IsNullOrWhiteSpace($StemJobId) -and $_.stemJobId -eq $StemJobId) -or
    (-not [string]::IsNullOrWhiteSpace($SourceSha256) -and $_.sourceSha256 -eq $SourceSha256.ToLowerInvariant())
})
if ([string]::IsNullOrWhiteSpace($StemJobId) -and [string]::IsNullOrWhiteSpace($SourceSha256)) {
    if ($passItems.Count -ne 1) {
        throw "Specify StemJobId or SourceSha256 when multiple PASS stem sets exist"
    }
    $candidates = @($passItems[0])
}
if ($candidates.Count -ne 1) {
    throw "Stem review target was not resolved uniquely"
}
$item = $candidates[0]
$manifestPath = Get-FullPath ([string]$item.manifestPath)
$qualityPath = if ($null -ne $item.qualityReportPath -and -not [string]::IsNullOrWhiteSpace([string]$item.qualityReportPath)) {
    Get-FullPath ([string]$item.qualityReportPath)
} else {
    Join-Path (Split-Path -Parent $manifestPath) "stem-quality-report.json"
}
foreach ($required in @($manifestPath, $qualityPath)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Stem review evidence is missing: $required"
    }
}

$jobDirectory = Split-Path -Parent $manifestPath
$reviewPath = Join-Path $jobDirectory "stem-listening-review.json"
$reviewedManifestPath = Join-Path $jobDirectory "stem-separation-manifest-reviewed.json"
$arguments = @(
    $tool,
    "--separation-manifest", $manifestPath,
    "--quality-report", $qualityPath,
    "--expected-source-sha256", ([string]$item.sourceSha256),
    "--decision", $Decision,
    "--reviewer", $Reviewer,
    "--notes", $Notes,
    "--output", $reviewPath,
    "--reviewed-manifest-output", $reviewedManifestPath
)
if ($ConfirmListenedToVocals) { $arguments += "--confirm-listened-to-vocals" }
if ($ConfirmListenedToInstrumental) { $arguments += "--confirm-listened-to-instrumental" }
if ($DeclareUserSong) { $arguments += "--declare-user-song" }

& $python @arguments
$reviewExit = $LASTEXITCODE
if ($reviewExit -notin @(0, 2)) {
    throw "Echoes stem listening review tool failed with exit code $reviewExit"
}
if (-not (Test-Path -LiteralPath $reviewPath -PathType Leaf)) {
    throw "Stem listening review record was not produced"
}
$review = Get-Content -LiteralPath $reviewPath -Raw | ConvertFrom-Json

foreach ($ledgerItem in $ledger.items) {
    if ($ledgerItem.stemJobId -ne $item.stemJobId) { continue }
    $ledgerItem | Add-Member -Force -NotePropertyName listeningReviewPath -NotePropertyValue $reviewPath
    $ledgerItem | Add-Member -Force -NotePropertyName listeningReviewStatus -NotePropertyValue ([string]$review.status)
    $ledgerItem | Add-Member -Force -NotePropertyName listeningReviewDecision -NotePropertyValue ([string]$review.decision)
    $ledgerItem | Add-Member -Force -NotePropertyName listeningReviewReviewer -NotePropertyValue ([string]$review.reviewer)
    $ledgerItem | Add-Member -Force -NotePropertyName humanListeningReviewCompleted -NotePropertyValue ([bool]$review.truthBoundary.humanListeningReviewCompleted)
    $ledgerItem | Add-Member -Force -NotePropertyName acapellaReady -NotePropertyValue ([bool]$review.truthBoundary.acapellaReady)
    $ledgerItem | Add-Member -Force -NotePropertyName voiceConversionInputReady -NotePropertyValue ([bool]$review.truthBoundary.voiceConversionInputReady)
    $ledgerItem | Add-Member -Force -NotePropertyName voiceConversionProven -NotePropertyValue $false
}
$ledger | Add-Member -Force -NotePropertyName listeningReviewUpdatedAtUtc -NotePropertyValue ([DateTime]::UtcNow.ToString("o"))
Write-JsonAtomic $ledgerPath $ledger

$bundle = Join-Path $control "Echoes-Stem-Review-Control-Latest.zip"
$stage = Join-Path $env:TEMP "Echoes-Stem-Review-$PID"
if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path (Join-Path $stage "jobs\$($item.stemJobId)") | Out-Null
Copy-Item -LiteralPath $ledgerPath -Destination (Join-Path $stage "stem-autopilot-ledger.json") -Force
Copy-Item -LiteralPath $runtimeManifestPath -Destination (Join-Path $stage "stem-runtime-manifest.json") -Force
Copy-Item -LiteralPath $reviewPath -Destination (Join-Path $stage "jobs\$($item.stemJobId)\stem-listening-review.json") -Force
Copy-Item -LiteralPath $qualityPath -Destination (Join-Path $stage "jobs\$($item.stemJobId)\stem-quality-report.json") -Force
if (Test-Path -LiteralPath $reviewedManifestPath -PathType Leaf) {
    Copy-Item -LiteralPath $reviewedManifestPath -Destination (Join-Path $stage "jobs\$($item.stemJobId)\stem-separation-manifest-reviewed.json") -Force
}
if (Test-Path -LiteralPath $bundle) { Remove-Item -LiteralPath $bundle -Force }
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $bundle -Force
Remove-Item -LiteralPath $stage -Recurse -Force

Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [IO.Compression.ZipFile]::OpenRead($bundle)
try {
    $audioExtensions = @(".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg")
    foreach ($entry in $archive.Entries) {
        if ($audioExtensions -contains [IO.Path]::GetExtension($entry.FullName).ToLowerInvariant()) {
            throw "Stem review control bundle contains audio: $($entry.FullName)"
        }
    }
} finally {
    $archive.Dispose()
}

Write-Host ("EchoesStemReview {0} job={1} decision={2} acapellaReady={3} bundle={4}" -f `
    $review.status, $item.stemJobId, $Decision, $review.truthBoundary.acapellaReady, $bundle)
exit $reviewExit
