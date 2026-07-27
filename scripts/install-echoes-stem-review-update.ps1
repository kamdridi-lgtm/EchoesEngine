[CmdletBinding()]
param(
    [string]$SourceRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$StemRuntimeRoot = "D:\A.I\EchoesStemRuntime",
    [string]$ControlRoot = "D:\A.I\EchoesControl",
    [string]$PythonExecutable,
    [switch]$AllowNonDDrive
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "Echoes stem review update currently supports Windows only"
}

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Write-JsonAtomic([string]$Path, [object]$Value) {
    $temporary = "$Path.tmp-$PID"
    $Value | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $temporary -Encoding utf8
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

$source = [IO.Path]::GetFullPath($SourceRoot)
$runtime = [IO.Path]::GetFullPath($StemRuntimeRoot)
$control = [IO.Path]::GetFullPath($ControlRoot)
if (-not $AllowNonDDrive -and -not $runtime.StartsWith("D:\", [StringComparison]::OrdinalIgnoreCase)) {
    throw "Production stem review runtime must remain on D:"
}

$runtimeManifestPath = Join-Path $runtime "stem-runtime-manifest.json"
if ([string]::IsNullOrWhiteSpace($PythonExecutable)) {
    $PythonExecutable = Join-Path $runtime ".venv\Scripts\python.exe"
}
$python = [IO.Path]::GetFullPath($PythonExecutable)
foreach ($required in @($runtimeManifestPath, $python)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Stem review update prerequisite is missing: $required"
    }
}

$copyMap = [ordered]@{
    "tools\review_stem_acapella.py" = "tools\review_stem_acapella.py"
    "config\echoes-stem-listening-review.v1.json" = "config\echoes-stem-listening-review.v1.json"
    "scripts\invoke-echoes-stem-review.ps1" = "Review-Echoes-Stem.ps1"
}
foreach ($sourceRelative in $copyMap.Keys) {
    $sourcePath = Join-Path $source $sourceRelative
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
        throw "Stem review source file is missing: $sourcePath"
    }
    $destination = Join-Path $runtime $copyMap[$sourceRelative]
    $parent = Split-Path -Parent $destination
    if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    Copy-Item -LiteralPath $sourcePath -Destination $destination -Force
}

New-Item -ItemType Directory -Force -Path $control | Out-Null
$readme = @"
ECHOES STEM LISTENING REVIEW

1. Listen to vocals.wav and the instrumental stems in the selected stem job folder.
2. Run D:\A.I\EchoesStemRuntime\Review-Echoes-Stem.ps1.
3. Approve only when the vocal stem is genuinely usable; otherwise reject it.

Approval never claims that voice conversion has already happened.
The original separation manifest is never modified.
The review control ZIP never contains audio.
"@
$readme | Set-Content -LiteralPath (Join-Path $control "STEM-REVIEW-README.txt") -Encoding utf8

$runtimeManifest = Get-Content -LiteralPath $runtimeManifestPath -Raw | ConvertFrom-Json
if ($runtimeManifest.status -ne "PASS") {
    throw "Stem runtime manifest is not PASS"
}
if ($null -eq $runtimeManifest.installedFileSha256) {
    $runtimeManifest | Add-Member -NotePropertyName installedFileSha256 -NotePropertyValue ([pscustomobject]@{})
}
$hashes = [ordered]@{}
foreach ($relative in @(
    "Review-Echoes-Stem.ps1",
    "tools\review_stem_acapella.py",
    "config\echoes-stem-listening-review.v1.json"
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
$runtimeManifest | Add-Member -Force -NotePropertyName listeningReviewUpdate -NotePropertyValue ([pscustomobject]@{
    schema = "echoes.stem-listening-review-update.v1"
    status = "INSTALLED"
    capabilityStatus = "PARTIAL"
    installedAtUtc = [DateTime]::UtcNow.ToString("o")
    pythonExecutable = $python
    launcher = (Join-Path $runtime "Review-Echoes-Stem.ps1")
    reviewTool = (Join-Path $runtime "tools\review_stem_acapella.py")
    capabilityManifest = (Join-Path $runtime "config\echoes-stem-listening-review.v1.json")
    files = $hashes
})
if ($null -eq $runtimeManifest.truthBoundary) {
    $runtimeManifest | Add-Member -NotePropertyName truthBoundary -NotePropertyValue ([pscustomobject]@{})
}
$truthFields = [ordered]@{
    humanListeningReviewCompleted = $false
    kamDridiVocalStemApproved = $false
    vocalIsolationQualityProven = $false
    acapellaReady = $false
    voiceConversionInputReady = $false
    voiceConversionProven = $false
    automaticApprovalAllowed = $false
}
foreach ($entry in $truthFields.GetEnumerator()) {
    $runtimeManifest.truthBoundary | Add-Member -Force -NotePropertyName $entry.Key -NotePropertyValue $entry.Value
}
Write-JsonAtomic $runtimeManifestPath $runtimeManifest

Write-Host "EchoesStemReviewUpdate INSTALLED runtime=$runtime launcher=$($runtimeManifest.listeningReviewUpdate.launcher)"
exit 0
