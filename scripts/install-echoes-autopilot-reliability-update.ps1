[CmdletBinding()]
param(
    [string]$SourceRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$AutopilotRoot = "D:\A.I\EchoesAutopilot",
    [string]$RuntimeRoot = "D:\A.I\EchoesEngineRuntime",
    [string]$InboxRoot = "D:\A.I\EchoesInbox",
    [string]$ResultsRoot = "D:\A.I\EchoesResults",
    [string]$ControlRoot = "D:\A.I\EchoesControl",
    [switch]$AllowNonDDrive,
    [switch]$ForceStartupFallback
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$TaskName = "Echoes Autopilot"

function Resolve-Root([string]$Path, [string]$Label) {
    $full = [IO.Path]::GetFullPath($Path)
    if (-not $AllowNonDDrive -and [IO.Path]::GetPathRoot($full).TrimEnd("\") -ne "D:") {
        throw "$Label must be on D: unless -AllowNonDDrive is used"
    }
    return $full
}

function New-ShellShortcut([string]$Path, [string]$Target, [string]$WorkingDirectory) {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($Path)
    $shortcut.TargetPath = $Target
    $shortcut.WorkingDirectory = $WorkingDirectory
    $shortcut.Save()
}

if ($env:OS -ne "Windows_NT") { throw "Echoes Autopilot reliability update supports Windows only" }

$source = [IO.Path]::GetFullPath($SourceRoot)
$autopilot = Resolve-Root $AutopilotRoot "AutopilotRoot"
$runtime = Resolve-Root $RuntimeRoot "RuntimeRoot"
$inbox = Resolve-Root $InboxRoot "InboxRoot"
$results = Resolve-Root $ResultsRoot "ResultsRoot"
$control = Resolve-Root $ControlRoot "ControlRoot"
$sourceController = Join-Path $source "scripts\invoke-echoes-autopilot.ps1"
$installedController = Join-Path $autopilot "Invoke-EchoesAutopilot.ps1"
$policyPath = Join-Path $autopilot "echoes-autopilot-policy.v1.json"
$manifestPath = Join-Path $control "autopilot-installation-manifest.json"
foreach ($required in @($sourceController, $policyPath, $manifestPath)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Reliability update required file is missing: $required" }
}

Copy-Item -LiteralPath $sourceController -Destination $installedController -Force
$policy = Get-Content -LiteralPath $policyPath -Raw | ConvertFrom-Json
$intervalMinutes = [Math]::Max(1, [Math]::Min(1440, [int]$policy.scanIntervalMinutes))
$intervalSeconds = $intervalMinutes * 60
$runNowCmd = Join-Path $autopilot "RUN-ECHOES-AUTOPILOT-NOW.cmd"
$loopCmd = Join-Path $autopilot "RUN-ECHOES-AUTOPILOT-LOOP.cmd"

@"
@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$installedController" -AutopilotRoot "$autopilot" -RuntimeRoot "$runtime" -InboxRoot "$inbox" -ResultsRoot "$results" -ControlRoot "$control" -PolicyPath "$policyPath" -Interactive
if errorlevel 1 pause
"@ | Set-Content -LiteralPath $runNowCmd -Encoding ascii

@"
@echo off
:echoes_loop
powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "$installedController" -AutopilotRoot "$autopilot" -RuntimeRoot "$runtime" -InboxRoot "$inbox" -ResultsRoot "$results" -ControlRoot "$control" -PolicyPath "$policyPath"
timeout /t $intervalSeconds /nobreak >nul
goto echoes_loop
"@ | Set-Content -LiteralPath $loopCmd -Encoding ascii

$taskInstalled = $false
$startupFallbackInstalled = $false
$taskError = $null
if (-not $ForceStartupFallback) {
    try {
        $taskArguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$installedController`" -AutopilotRoot `"$autopilot`" -RuntimeRoot `"$runtime`" -InboxRoot `"$inbox`" -ResultsRoot `"$results`" -ControlRoot `"$control`" -PolicyPath `"$policyPath`""
        $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $taskArguments
        $trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) -RepetitionInterval (New-TimeSpan -Minutes $intervalMinutes) -RepetitionDuration (New-TimeSpan -Days 3650)
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Description "Processes EchoesInbox with verified local AI audio analysis" -Force -RunLevel Limited | Out-Null
        $taskInstalled = $true
    } catch {
        $taskError = $_.Exception.Message
    }
}

if (-not $taskInstalled) {
    try {
        $startup = [Environment]::GetFolderPath("Startup")
        if (-not $startup) { throw "Windows Startup folder is unavailable" }
        New-ShellShortcut (Join-Path $startup "Echoes Autopilot Loop.lnk") $loopCmd $autopilot
        $startupFallbackInstalled = $true
    } catch {
        $taskError = if ($taskError) { "$taskError | startup fallback failed: $($_.Exception.Message)" } else { $_.Exception.Message }
    }
}

if (-not $taskInstalled -and -not $startupFallbackInstalled) {
    throw "Autopilot reliability update could not install a repeating execution path: $taskError"
}

$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$manifest.automation.scheduledTaskInstalled = $taskInstalled
$manifest.automation.startupFallbackInstalled = $startupFallbackInstalled
$manifest.automation.intervalMinutes = $intervalMinutes
$manifest.automation.taskError = $taskError
$manifest.truthBoundary.autonomousLoopInstalled = ($taskInstalled -or $startupFallbackInstalled)
$reliability = [ordered]@{
    schema = "echoes.autopilot-reliability-update.v1"
    status = "PASS"
    installedAtUtc = [DateTime]::UtcNow.ToString("o")
    intervalMinutes = $intervalMinutes
    controller = $installedController
    loopLauncher = $loopCmd
    scheduledTaskInstalled = $taskInstalled
    startupFallbackInstalled = $startupFallbackInstalled
    resolvedFfmpegPropagationEnabled = $true
    refreshedReportRequired = $true
    historicalBundlePreservationEnabled = $true
}
$manifest | Add-Member -NotePropertyName reliabilityUpdate -NotePropertyValue $reliability -Force
$manifest | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $manifestPath -Encoding utf8

Write-Host "EchoesAutopilotReliabilityUpdate PASS interval=$intervalMinutes task=$taskInstalled fallback=$startupFallbackInstalled"
Write-Host "Controller: $installedController"
Write-Host "Loop: $loopCmd"
