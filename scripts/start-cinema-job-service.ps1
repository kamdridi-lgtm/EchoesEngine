param(
    [Parameter(Mandatory = $true)]
    [string]$ServiceToken,

    [Parameter(Mandatory = $true)]
    [string]$ProviderToken,

    [Parameter(Mandatory = $true)]
    [string]$SectionsRoot,

    [string]$OutputRoot = "D:\A.I\EchoesCinema\jobs",
    [string]$AudioRoot = "",
    [string]$ManifestCli = "",
    [string]$PythonExecutable = "D:\A.I\EchoesCinema\.venv-cinema\Scripts\python.exe",
    [string]$ProviderEndpoint = "http://127.0.0.1:8081/v1/render",
    [string]$ProviderHealthUrl = "http://127.0.0.1:8081/health",
    [string]$ProviderAllowlist = "127.0.0.1,localhost",
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8090,
    [int]$MaxWorkers = 1,
    [double]$StorageReserveGiB = 20,
    [double]$DefaultJobGiB = 8,
    [double]$MaxJobGiB = 200,
    [double]$ProviderTimeout = 180
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Resolve-RequiredPath {
    param(
        [string]$Path,
        [string]$Label,
        [switch]$Container
    )
    $pathType = if ($Container) { "Container" } else { "Leaf" }
    if (-not (Test-Path -LiteralPath $Path -PathType $pathType)) {
        throw "$Label not found: $Path"
    }
    return (Resolve-Path -LiteralPath $Path).Path
}

if ($Port -le 0 -or $Port -gt 65535) {
    throw "Port must be between 1 and 65535"
}
if ($MaxWorkers -le 0) {
    throw "MaxWorkers must be positive"
}
if ($StorageReserveGiB -lt 0) {
    throw "StorageReserveGiB must be non-negative"
}
if ($DefaultJobGiB -le 0 -or $MaxJobGiB -le 0) {
    throw "DefaultJobGiB and MaxJobGiB must be positive"
}
if ($DefaultJobGiB -gt $MaxJobGiB) {
    throw "DefaultJobGiB must not exceed MaxJobGiB"
}
if ($ProviderTimeout -le 0) {
    throw "ProviderTimeout must be positive"
}

$python = Resolve-RequiredPath -Path $PythonExecutable -Label "Cinema Python"
$sections = Resolve-RequiredPath -Path $SectionsRoot -Label "Sections root" -Container
$audio = if ($AudioRoot) { Resolve-RequiredPath -Path $AudioRoot -Label "Audio root" -Container } else { "" }
$manifest = if ($ManifestCli) { Resolve-RequiredPath -Path $ManifestCli -Label "RenderManifestCli" } else { "" }

$output = [System.IO.Path]::GetFullPath($OutputRoot)
$outputDrive = [System.IO.Path]::GetPathRoot($output)
if (-not $outputDrive -or $outputDrive.TrimEnd("\").ToUpperInvariant() -eq "C:") {
    throw "Cinema job output must not use drive C:. Current path: $output"
}
New-Item -ItemType Directory -Path $output -Force | Out-Null

$service = (Resolve-Path (Join-Path $PSScriptRoot "..\tools\cinema_job_service_durable.py")).Path
if (-not (Test-Path -LiteralPath $service -PathType Leaf)) {
    throw "Durable Cinema job service not found: $service"
}

$env:ECHOES_CINEMA_SERVICE_TOKEN = $ServiceToken
$env:ECHOES_RENDER_TOKEN = $ProviderToken
$env:ECHOES_RENDER_ENDPOINT = $ProviderEndpoint
$env:ECHOES_RENDER_HEALTH_URL = $ProviderHealthUrl
$env:ECHOES_RENDER_HOST_ALLOWLIST = $ProviderAllowlist
$env:ECHOES_CINEMA_STORAGE_RESERVE_GIB = "$StorageReserveGiB"
$env:ECHOES_CINEMA_DEFAULT_JOB_GIB = "$DefaultJobGiB"
$env:ECHOES_CINEMA_MAX_JOB_GIB = "$MaxJobGiB"

$arguments = @(
    $service,
    "--host", $HostAddress,
    "--port", "$Port",
    "--token", $ServiceToken,
    "--sections-root", $sections,
    "--output-root", $output,
    "--provider-timeout", "$ProviderTimeout",
    "--max-workers", "$MaxWorkers"
)
if ($audio) {
    $arguments += @("--audio-root", $audio)
}
if ($manifest) {
    $arguments += @("--manifest-cli", $manifest)
}

Write-Host "Starting restart-safe Echoes Cinema service on http://${HostAddress}:$Port"
Write-Host "Provider endpoint: $ProviderEndpoint"
Write-Host "Sections root: $sections"
Write-Host "Output root: $output"
Write-Host "Durable ledger: $(Join-Path $output '_service\job-ledger.json')"
Write-Host "Priority workers: $MaxWorkers"
Write-Host "Storage policy: reserve ${StorageReserveGiB} GiB; default job ${DefaultJobGiB} GiB; maximum job ${MaxJobGiB} GiB"
Write-Host "Manifest generator: $(if ($manifest) { 'native CLI' } else { 'compiler-free Python' })"
Write-Host "Tokens remain only in this process environment and are not printed."

& $python @arguments
exit $LASTEXITCODE
