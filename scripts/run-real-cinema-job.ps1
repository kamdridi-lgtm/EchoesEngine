param(
    [Parameter(Mandatory = $true)]
    [string]$SectionsCsv,

    [Parameter(Mandatory = $true)]
    [string]$OutputRoot,

    [Parameter(Mandatory = $true)]
    [string]$ManifestCli,

    [Parameter(Mandatory = $true)]
    [string]$JobId,

    [Parameter(Mandatory = $true)]
    [string]$Token,

    [string]$Endpoint = "http://127.0.0.1:8081/v1/render",
    [string]$HealthUrl = "http://127.0.0.1:8081/health",
    [string]$AllowedHosts = "127.0.0.1,localhost",
    [uint32]$Seed = 1337,
    [string]$AudioFile = ""
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python is not available in PATH"
}
if (-not (Test-Path $SectionsCsv)) {
    throw "Sections CSV not found: $SectionsCsv"
}
if (-not (Test-Path $ManifestCli)) {
    throw "RenderManifestCli not found: $ManifestCli"
}
if ($AudioFile -and -not (Test-Path $AudioFile)) {
    throw "Audio file not found: $AudioFile"
}

$runner = Join-Path $PSScriptRoot "..\tools\cinema_job_runner.py"
if (-not (Test-Path $runner)) {
    throw "Cinema job runner not found: $runner"
}

$env:ECHOES_RENDER_TOKEN = $Token
$env:ECHOES_RENDER_ENDPOINT = $Endpoint
$env:ECHOES_RENDER_HEALTH_URL = $HealthUrl
$env:ECHOES_RENDER_HOST_ALLOWLIST = $AllowedHosts

$arguments = @(
    $runner,
    (Resolve-Path $SectionsCsv).Path,
    $OutputRoot,
    "--manifest-cli", (Resolve-Path $ManifestCli).Path,
    "--job-id", $JobId,
    "--seed", "$Seed",
    "--backend", "http"
)
if ($AudioFile) {
    $arguments += @("--audio", (Resolve-Path $AudioFile).Path)
}

Write-Host "Submitting REAL Echoes Cinema job: $JobId"
Write-Host "Provider: $Endpoint"
Write-Host "The job runner requires provider health realModelLoaded=true."

& python @arguments
exit $LASTEXITCODE
