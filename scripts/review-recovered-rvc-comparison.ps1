[CmdletBinding()]
param(
    [string]$ComparisonReport = "D:\A.I\EchoesRvcRecovered\comparison_output\control\RECOVERED-RVC-COMPARISON-REPORT.json",
    [string]$ToolPath,
    [ValidateSet("700", "1000", "1500")]
    [string]$SelectedLabel,
    [string]$Reviewer = "Kam Dridi",
    [string]$Notes = "",
    [switch]$ConfirmAllListened,
    [switch]$NoOpen
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-FullPath([string]$PathValue) {
    if ([string]::IsNullOrWhiteSpace($PathValue)) {
        return $null
    }
    return [IO.Path]::GetFullPath($PathValue)
}

if ($env:OS -ne "Windows_NT") {
    throw "Recovered RVC listening review currently supports Windows only"
}

$reportPath = Resolve-FullPath $ComparisonReport
if (-not (Test-Path -LiteralPath $reportPath -PathType Leaf)) {
    throw "Comparison report is missing: $reportPath"
}

$report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
if ($report.schema -ne "echoes.recovered-rvc-comparison-run.v1" -or $report.status -ne "PASS") {
    throw "Comparison report is not a verified PASS report"
}

if ([string]::IsNullOrWhiteSpace($ToolPath)) {
    $packageRoot = Split-Path -Parent $PSScriptRoot
    $ToolPath = Join-Path $packageRoot "tools\record_rvc_comparison_listening_review.py"
}
$reviewTool = Resolve-FullPath $ToolPath
if (-not (Test-Path -LiteralPath $reviewTool -PathType Leaf)) {
    throw "Listening review tool is missing: $reviewTool"
}

$playlistPath = Resolve-FullPath ([string]$report.playlistPath)
if (-not $NoOpen -and $playlistPath -and (Test-Path -LiteralPath $playlistPath -PathType Leaf)) {
    Start-Process -FilePath $playlistPath
}

Write-Host "" 
Write-Host "ECHOES RVC - LISTENING REVIEW" -ForegroundColor Cyan
Write-Host "700 : $($report.runs[0].outputPath)"
Write-Host "1000: $($report.runs[1].outputPath)"
Write-Host "1500: $($report.runs[2].outputPath)"
Write-Host ""

if ([string]::IsNullOrWhiteSpace($SelectedLabel)) {
    do {
        $SelectedLabel = (Read-Host "After listening to all three, type 700, 1000 or 1500").Trim()
    } while ($SelectedLabel -notin @("700", "1000", "1500"))
}

if (-not $ConfirmAllListened) {
    $confirmation = (Read-Host "Type OUI to confirm you listened to 700, 1000 and 1500").Trim().ToUpperInvariant()
    if ($confirmation -ne "OUI") {
        throw "All three listening confirmations are required"
    }
    $ConfirmAllListened = $true
}

$resolvedReviewer = $Reviewer.Trim()
if ([string]::IsNullOrWhiteSpace($resolvedReviewer)) {
    throw "Reviewer identity is required"
}

$runtimePython = $null
if ($report.runtime -and $report.runtime.python) {
    $candidate = Resolve-FullPath ([string]$report.runtime.python)
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
        $runtimePython = $candidate
    }
}
if (-not $runtimePython) {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        $runtimePython = $pythonCommand.Source
    }
}
if (-not $runtimePython) {
    throw "No Python runtime is available for the listening review"
}

$controlDirectory = Split-Path -Parent $reportPath
$reviewPath = Join-Path $controlDirectory "RVC-COMPARISON-LISTENING-REVIEW.json"
$summaryPath = Join-Path $controlDirectory "SELECTED-RVC-MODEL.txt"

$arguments = @(
    $reviewTool,
    "--comparison-report", $reportPath,
    "--selected-label", $SelectedLabel,
    "--reviewer", $resolvedReviewer,
    "--notes", $Notes,
    "--output", $reviewPath,
    "--confirm-listened-700",
    "--confirm-listened-1000",
    "--confirm-listened-1500",
    "--confirm-manual-selection"
)

& $runtimePython @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Listening review failed with exit code $LASTEXITCODE"
}
if (-not (Test-Path -LiteralPath $reviewPath -PathType Leaf)) {
    throw "Listening review did not create its decision file"
}

$review = Get-Content -LiteralPath $reviewPath -Raw | ConvertFrom-Json
if ($review.status -ne "APPROVED" -or $review.decision.selectedLabel -ne $SelectedLabel) {
    throw "Listening review output did not preserve the selected checkpoint"
}

@(
    "ECHOES RVC MANUAL LISTENING DECISION",
    "",
    "SELECTED CHECKPOINT: $SelectedLabel EPOCHS",
    "REVIEWER: $resolvedReviewer",
    "MODEL: $($review.decision.selectedModelPath)",
    "MODEL SHA-256: $($review.decision.selectedModelSha256)",
    "LISTENING OUTPUT: $($review.decision.selectedOutputPath)",
    "OUTPUT SHA-256: $($review.decision.selectedOutputSha256)",
    "REVIEW JSON: $reviewPath",
    "",
    "NO MODEL WAS COPIED, MODIFIED OR PROMOTED AUTOMATICALLY."
) | Set-Content -LiteralPath $summaryPath -Encoding utf8

Write-Host "" 
Write-Host "RVC LISTENING DECISION RECORDED" -ForegroundColor Green
Write-Host "Selected: $SelectedLabel"
Write-Host "Review: $reviewPath"
Write-Host "Summary: $summaryPath"

if (-not $NoOpen) {
    Start-Process explorer.exe -ArgumentList $controlDirectory
}
exit 0
