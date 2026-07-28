[CmdletBinding()]
param(
    [string]$SourceRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$RvcRuntimeRoot = "D:\A.I\EchoesRvcRuntime",
    [string]$RecoveredModelRoot = "D:\A.I\EchoesRvcRecovered\model_2",
    [string]$RvcInputManifest,
    [string]$ComparisonOutputRoot,
    [string]$PythonExecutable,
    [string]$VoiceOwner = "Kam Dridi",
    [switch]$DeclareUserOwnedModel,
    [switch]$ConfirmOwnerConsent,
    [switch]$AuthorizeVoiceConversion,
    [switch]$ForbidThirdPartyImpersonation,
    [switch]$AllowNonDDrive,
    [switch]$NoOpen
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-Checked([string]$Executable, [string[]]$Arguments, [string]$Label) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

if ($env:OS -ne "Windows_NT") {
    throw "Echoes RVC three-model comparison preparation currently supports Windows only"
}

$runtime = [IO.Path]::GetFullPath($RvcRuntimeRoot)
$recovered = [IO.Path]::GetFullPath($RecoveredModelRoot)
if (-not $AllowNonDDrive) {
    if (-not $runtime.StartsWith("D:\", [StringComparison]::OrdinalIgnoreCase)) {
        throw "Production RVC runtime must remain on D:"
    }
    if (-not $recovered.StartsWith("D:\", [StringComparison]::OrdinalIgnoreCase)) {
        throw "Recovered RVC models must remain on D:"
    }
}

foreach ($requiredFlag in @(
    @{ Name = "DeclareUserOwnedModel"; Value = [bool]$DeclareUserOwnedModel },
    @{ Name = "ConfirmOwnerConsent"; Value = [bool]$ConfirmOwnerConsent },
    @{ Name = "AuthorizeVoiceConversion"; Value = [bool]$AuthorizeVoiceConversion },
    @{ Name = "ForbidThirdPartyImpersonation"; Value = [bool]$ForbidThirdPartyImpersonation }
)) {
    if (-not $requiredFlag.Value) {
        throw "Three-model registration requires -$($requiredFlag.Name)"
    }
}

$runtimeManifest = Join-Path $runtime "rvc-runtime-manifest.json"
$assetsManifest = Join-Path $runtime "control\rvc-core-assets-manifest.json"
if ([string]::IsNullOrWhiteSpace($PythonExecutable)) {
    $python = Join-Path $runtime ".venv\Scripts\python.exe"
} else {
    $python = [IO.Path]::GetFullPath($PythonExecutable)
}
if ([string]::IsNullOrWhiteSpace($RvcInputManifest)) {
    $inputManifest = Join-Path $runtime "control\rvc-input-manifest.json"
} else {
    $inputManifest = [IO.Path]::GetFullPath($RvcInputManifest)
}
if ([string]::IsNullOrWhiteSpace($ComparisonOutputRoot)) {
    $comparisonRoot = Join-Path $runtime "control\rvc-model-comparison"
} else {
    $comparisonRoot = [IO.Path]::GetFullPath($ComparisonOutputRoot)
}

$registrar = Join-Path $SourceRoot "tools\register_rvc_voice_model.py"
$planner = Join-Path $SourceRoot "tools\plan_rvc_model_comparison.py"
$indexFile = Join-Path $recovered "model_2.index"
$modelSpecs = @(
    @{ Label = "700"; File = (Join-Path $recovered "model_2_700e_63700s.pth"); Name = "Kam-Dridi-Voice-700E" },
    @{ Label = "1000"; File = (Join-Path $recovered "model_2_1000e_91000s.pth"); Name = "Kam-Dridi-Voice-1000E" },
    @{ Label = "1500"; File = (Join-Path $recovered "model_2_1500e_136500s.pth"); Name = "Kam-Dridi-Voice-1500E" }
)

foreach ($required in @($runtimeManifest, $assetsManifest, $python, $inputManifest, $registrar, $planner, $indexFile)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required RVC comparison file is missing: $required"
    }
}
foreach ($spec in $modelSpecs) {
    if (-not (Test-Path -LiteralPath $spec.File -PathType Leaf)) {
        throw "Recovered checkpoint is missing for $($spec.Label): $($spec.File)"
    }
}

$indexHash = Get-Sha256 $indexFile
$modelHashes = @($modelSpecs | ForEach-Object { Get-Sha256 $_.File })
if (($modelHashes | Select-Object -Unique).Count -ne 3) {
    throw "The 700, 1000 and 1500 checkpoints must have distinct SHA-256 values"
}

New-Item -ItemType Directory -Force -Path $comparisonRoot | Out-Null
$manifestRoot = Join-Path $comparisonRoot "models"
$outputAudioRoot = Join-Path $comparisonRoot "planned-output"
New-Item -ItemType Directory -Force -Path $manifestRoot, $outputAudioRoot | Out-Null

$registered = @{}
foreach ($spec in $modelSpecs) {
    $manifestPath = Join-Path $manifestRoot ("rvc-voice-model-" + $spec.Label + ".json")
    $arguments = @(
        $registrar,
        "--runtime-root", $runtime,
        "--runtime-manifest", $runtimeManifest,
        "--core-assets-manifest", $assetsManifest,
        "--model-file", $spec.File,
        "--index-file", $indexFile,
        "--voice-owner", $VoiceOwner,
        "--model-name", $spec.Name,
        "--output", $manifestPath,
        "--declare-user-owned-model",
        "--confirm-owner-consent",
        "--authorize-voice-conversion",
        "--forbid-third-party-impersonation"
    )
    Invoke-Checked $python $arguments ("RVC voice-model registration " + $spec.Label)
    $registered[$spec.Label] = $manifestPath
}

$planPath = Join-Path $comparisonRoot "rvc-model-comparison-plan.json"
$planArguments = @(
    $planner,
    "--input-manifest", $inputManifest,
    "--model", ("700=" + $registered["700"]),
    "--model", ("1000=" + $registered["1000"]),
    "--model", ("1500=" + $registered["1500"]),
    "--output-directory", $outputAudioRoot,
    "--output", $planPath,
    "--f0-method", "rmvpe",
    "--pitch-shift", "0"
)
Invoke-Checked $python $planArguments "RVC three-model comparison planning"

$plan = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
if ($plan.status -ne "READY") {
    throw "RVC comparison plan did not reach READY status"
}
if (($plan.comparison.labels -join ",") -ne "700,1000,1500") {
    throw "RVC comparison model order drifted"
}
if ($plan.truthBoundary.threeDistinctModelsVerified -ne $true) {
    throw "RVC comparison did not prove three distinct checkpoints"
}
if ($plan.truthBoundary.sharedIndexVerified -ne $true) {
    throw "RVC comparison did not prove a shared index"
}
if ($plan.truthBoundary.sharedInputVerified -ne $true) {
    throw "RVC comparison did not prove a shared input"
}
foreach ($field in @("modelDeserializationAttempted", "voiceModelLoadProven", "indexLoadProven", "rvcInferenceProven", "voiceConversionProven", "convertedAudioGenerated", "audioUploaded", "executionAuthorized")) {
    if ($plan.truthBoundary.$field -ne $false) {
        throw "RVC comparison plan falsely promoted capability: $field"
    }
}

$summaryPath = Join-Path $comparisonRoot "rvc-model-comparison-summary.json"
$summary = [ordered]@{
    schema = "echoes.rvc-three-model-comparison-preparation.v1"
    status = "READY"
    preparedAtUtc = [DateTime]::UtcNow.ToString("o")
    voiceOwner = $VoiceOwner
    runtimeRoot = $runtime
    recoveredModelRoot = $recovered
    inputManifest = $inputManifest
    sharedRecoveredIndex = [ordered]@{
        path = $indexFile
        sha256 = $indexHash
    }
    registeredModelManifests = [ordered]@{
        "700" = $registered["700"]
        "1000" = $registered["1000"]
        "1500" = $registered["1500"]
    }
    comparisonPlan = $planPath
    plannedOutputRoot = $outputAudioRoot
    truthBoundary = [ordered]@{
        recoveredModelsCopiedIntoManagedRuntime = $true
        threeModelComparisonPlanReady = $true
        modelDeserializationAttempted = $false
        voiceModelLoadProven = $false
        rvcInferenceProven = $false
        voiceConversionProven = $false
        convertedAudioGenerated = $false
        executionAuthorized = $false
    }
}
$summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $summaryPath -Encoding UTF8

Write-Host "Echoes RVC three-model comparison preparation READY"
Write-Host "Recovered models: $recovered"
Write-Host "Shared index SHA-256: $indexHash"
Write-Host "Comparison plan: $planPath"
Write-Host "Planned outputs: $outputAudioRoot"
Write-Host "Inference: NOT EXECUTED"
Write-Host "Conversion: NOT EXECUTED"

if (-not $NoOpen) {
    Start-Process explorer.exe -ArgumentList $comparisonRoot
}
exit 0
