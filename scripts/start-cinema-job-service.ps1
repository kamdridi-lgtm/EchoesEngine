param(
    [Parameter(Mandatory = $true)]
    [string]$ServiceToken,

    [Parameter(Mandatory = $true)]
    [string]$ProviderToken,

    [Parameter(Mandatory = $true)]
    [string]$ManifestCli,

    [Parameter(Mandatory = $true)]
    [string]$SectionsRoot,

    [Parameter(Mandatory = $true)]
    [string]$OutputRoot,

    [string]$AudioRoot = "",
    [string]$ProviderEndpoint = "http://127.0.0.1:8081/v1/render",
    [string]$ProviderHealthUrl = "http://127.0.0.1:8081/health",
    [string]$ProviderAllowlist = "127.0.0.1,localhost",
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8090,
    [int]$MaxWorkers = 1,
    [double]$ProviderTimeout = 180
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python is not available in PATH"
}
if (-not (Test-Path $ManifestCli)) {
    throw "RenderManifestCli not found: $ManifestCli"
}
if (-not (Test-Path $SectionsRoot -PathType Container)) {
    throw "Sections root not found: $SectionsRoot"
}
if ($AudioRoot -and -not (Test-Path $AudioRoot -PathType Container)) {
    throw "Audio root not found: $AudioRoot"
}
if ($Port -le 0 -or $Port -gt 65535) {
    throw "Port must be between 1 and 65535"
}
if ($MaxWorkers -le 0) {
    throw "MaxWorkers must be positive"
}

$service = Join-Path $PSScriptRoot "..\tools\cinema_job_service.py"
if (-not (Test-Path $service)) {
    throw "Cinema job service not found: $service"
}

New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null

$env:ECHOES_CINEMA_SERVICE_TOKEN = $ServiceToken
$env:ECHOES_RENDER_TOKEN = $ProviderToken
$env:ECHOES_RENDER_ENDPOINT = $ProviderEndpoint
$env:ECHOES_RENDER_HEALTH_URL = $ProviderHealthUrl
$env:ECHOES_RENDER_HOST_ALLOWLIST = $ProviderAllowlist

$arguments = @(
    $service,
    "--host", $HostAddress,
    "--port", "$Port",
    "--token", $ServiceToken,
    "--manifest-cli", (Resolve-Path $ManifestCli).Path,
    "--sections-root", (Resolve-Path $SectionsRoot).Path,
    "--output-root", (Resolve-Path $OutputRoot).Path,
    "--provider-timeout", "$ProviderTimeout",
    "--max-workers", "$MaxWorkers"
)
if ($AudioRoot) {
    $arguments += @("--audio-root", (Resolve-Path $AudioRoot).Path)
}

Write-Host "Starting Echoes Cinema job service on http://${HostAddress}:$Port"
Write-Host "Provider endpoint: $ProviderEndpoint"
Write-Host "Sections root: $SectionsRoot"
Write-Host "Output root: $OutputRoot"
Write-Host "Tokens are stored only in this process environment and are not printed."

& python @arguments
exit $LASTEXITCODE
