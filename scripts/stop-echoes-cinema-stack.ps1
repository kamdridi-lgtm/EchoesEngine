param(
    [string]$WorkspaceRoot = "D:\A.I\EchoesCinema",
    [int]$GraceSeconds = 20
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$workspace = [System.IO.Path]::GetFullPath($WorkspaceRoot)
$runtimeRoot = Join-Path $workspace "runtime"
$statePath = Join-Path $runtimeRoot "stack-state.json"
$stopSignalPath = Join-Path $runtimeRoot "stop.signal"

function Get-State {
    if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) { return $null }
    try { return Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json } catch { return $null }
}

function Stop-VerifiedProcess {
    param(
        [object]$ProcessId,
        [string[]]$RequiredCommandFragments
    )
    if ($null -eq $ProcessId -or "$ProcessId" -notmatch '^\d+$') { return $false }
    $pidNumber = [int]$ProcessId
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$pidNumber" -ErrorAction SilentlyContinue
    if (-not $process) { return $false }
    $commandLine = [string]$process.CommandLine
    $matched = $false
    foreach ($fragment in $RequiredCommandFragments) {
        if ($commandLine -like "*$fragment*") { $matched = $true; break }
    }
    if (-not $matched) {
        Write-Warning "Refusing to stop PID $pidNumber because it is not an Echoes Cinema process."
        return $false
    }
    Stop-Process -Id $pidNumber -Force -ErrorAction SilentlyContinue
    return $true
}

New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
Set-Content -LiteralPath $stopSignalPath -Value ([DateTime]::UtcNow.ToString("o")) -Encoding ascii
$state = Get-State
$supervisorPid = if ($state) { $state.supervisorPid } else { $null }

$deadline = (Get-Date).AddSeconds([math]::Max(1, $GraceSeconds))
while ((Get-Date) -lt $deadline) {
    if ($null -eq $supervisorPid -or "$supervisorPid" -notmatch '^\d+$') { break }
    if (-not (Get-Process -Id ([int]$supervisorPid) -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Milliseconds 500
}

$state = Get-State
if ($state) {
    Stop-VerifiedProcess -ProcessId $state.providerPid -RequiredCommandFragments @("modelscope_low_vram_provider.py", "modelscope_low_vram_provider_v2.py") | Out-Null
    Stop-VerifiedProcess -ProcessId $state.providerWorkerPid -RequiredCommandFragments @("echoes-cinema-provider-worker.ps1") | Out-Null
    Stop-VerifiedProcess -ProcessId $state.servicePid -RequiredCommandFragments @("cinema_control_center.py") | Out-Null
    Stop-VerifiedProcess -ProcessId $state.supervisorPid -RequiredCommandFragments @("echoes-cinema-stack-supervisor.ps1") | Out-Null
}

foreach ($pidFile in @("provider.pid", "provider-worker.pid", "service.pid", "supervisor.pid")) {
    Remove-Item -LiteralPath (Join-Path $runtimeRoot $pidFile) -Force -ErrorAction SilentlyContinue
}
Write-Host "Echoes Cinema stack stopped. Workspace preserved: $workspace"
