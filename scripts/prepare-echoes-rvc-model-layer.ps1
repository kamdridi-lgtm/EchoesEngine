[CmdletBinding()]
param(
    [string]$SourceRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$RvcRuntimeRoot = "D:\A.I\EchoesRvcRuntime",
    [string]$ModelFile,
    [string]$IndexFile,
    [string]$VoiceOwner = "Kam Dridi",
    [string]$ModelName = "Kam-Dridi-Voice",
    [switch]$IncludeWindowsSupport,
    [switch]$DeclareUserOwnedModel,
    [switch]$ConfirmOwnerConsent,
    [switch]$AuthorizeVoiceConversion,
    [switch]$ForbidThirdPartyImpersonation,
    [switch]$ReuseVerifiedFiles,
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

if ($env:OS -ne "Windows_NT") {
    throw "Echoes RVC model-layer preparation currently supports Windows only"
}

$runtime = [IO.Path]::GetFullPath($RvcRuntimeRoot)
if (-not $AllowNonDDrive -and -not $runtime.StartsWith("D:\", [StringComparison]::OrdinalIgnoreCase)) {
    throw "Production RVC runtime must remain on D:"
}

$runtimeManifest = Join-Path $runtime "rvc-runtime-manifest.json"
$python = Join-Path $runtime ".venv\Scripts\python.exe"
$control = Join-Path $runtime "control"
$assetsManifest = Join-Path $control "rvc-core-assets-manifest.json"
$modelManifest = Join-Path $control "rvc-voice-model-manifest.json"
$provisioner = Join-Path $SourceRoot "tools\provision_rvc_core_assets.py"
$registrar = Join-Path $SourceRoot "tools\register_rvc_voice_model.py"

foreach ($required in @($runtimeManifest, $python, $provisioner, $registrar)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required RVC model-layer file is missing: $required"
    }
}
New-Item -ItemType Directory -Force -Path $control | Out-Null

$assetArguments = @(
    $provisioner,
    "--runtime-root", $runtime,
    "--runtime-manifest", $runtimeManifest,
    "--output", $assetsManifest
)
if ($IncludeWindowsSupport) { $assetArguments += "--include-windows-support" }
if ($ReuseVerifiedFiles) { $assetArguments += "--reuse-verified-files" }
Invoke-Checked $python $assetArguments "Pinned RVC core asset provisioning"

if (-not [string]::IsNullOrWhiteSpace($ModelFile)) {
    foreach ($requiredFlag in @(
        @{ Name = "DeclareUserOwnedModel"; Value = [bool]$DeclareUserOwnedModel },
        @{ Name = "ConfirmOwnerConsent"; Value = [bool]$ConfirmOwnerConsent },
        @{ Name = "AuthorizeVoiceConversion"; Value = [bool]$AuthorizeVoiceConversion },
        @{ Name = "ForbidThirdPartyImpersonation"; Value = [bool]$ForbidThirdPartyImpersonation }
    )) {
        if (-not $requiredFlag.Value) {
            throw "Model registration requires -$($requiredFlag.Name)"
        }
    }

    $modelPath = [IO.Path]::GetFullPath($ModelFile)
    $modelArguments = @(
        $registrar,
        "--runtime-root", $runtime,
        "--runtime-manifest", $runtimeManifest,
        "--core-assets-manifest", $assetsManifest,
        "--model-file", $modelPath,
        "--voice-owner", $VoiceOwner,
        "--model-name", $ModelName,
        "--output", $modelManifest,
        "--declare-user-owned-model",
        "--confirm-owner-consent",
        "--authorize-voice-conversion",
        "--forbid-third-party-impersonation"
    )
    if (-not [string]::IsNullOrWhiteSpace($IndexFile)) {
        $modelArguments += @("--index-file", [IO.Path]::GetFullPath($IndexFile))
    }
    Invoke-Checked $python $modelArguments "RVC voice-model registration"
}

$assets = Get-Content -LiteralPath $assetsManifest -Raw | ConvertFrom-Json
if ($assets.status -ne "VERIFIED") {
    throw "RVC core assets did not reach VERIFIED status"
}
Write-Host "Echoes RVC model layer prepared"
Write-Host "Runtime: $runtime"
Write-Host "Assets: $assetsManifest"
if (Test-Path -LiteralPath $modelManifest -PathType Leaf) {
    Write-Host "Voice model: $modelManifest"
} else {
    Write-Host "Voice model: NOT REGISTERED"
}
Write-Host "Inference: NOT EXECUTED"
Write-Host "Conversion: NOT EXECUTED"

if (-not $NoOpen) {
    Start-Process explorer.exe -ArgumentList $control
}
exit 0
