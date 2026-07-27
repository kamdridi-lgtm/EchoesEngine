[CmdletBinding()]
param(
    [string]$SourceRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$AutopilotRoot = "D:\A.I\EchoesAutopilot",
    [string]$RuntimeRoot = "D:\A.I\EchoesEngineRuntime",
    [string]$InboxRoot = "D:\A.I\EchoesInbox",
    [string]$ResultsRoot = "D:\A.I\EchoesResults",
    [string]$ControlRoot = "D:\A.I\EchoesControl",
    [string]$PythonExecutable = "",
    [string[]]$InputPaths = @(),
    [switch]$AllowNonDDrive,
    [switch]$SkipPrerequisiteInstall,
    [switch]$SkipScheduledTask,
    [switch]$SkipRemotePolicy,
    [switch]$NoInitialRun,
    [switch]$NoOpen
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$InstallSchema = "echoes.autopilot-installation.v1"
$TaskName = "Echoes Autopilot"
$SupportedExtensions = @(".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg")

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Invoke-NativeChecked([string]$Executable, [string[]]$Arguments, [string]$Label) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Label failed with exit code $LASTEXITCODE" }
}

function Resolve-CompatiblePython([string]$RequestedExecutable) {
    $candidates = @()
    if ($RequestedExecutable) {
        $candidates += [pscustomobject]@{ Executable = $RequestedExecutable; Prefix = @() }
    }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($null -ne $py) {
        $candidates += [pscustomobject]@{ Executable = $py.Source; Prefix = @("-3.11") }
        $candidates += [pscustomobject]@{ Executable = $py.Source; Prefix = @("-3.10") }
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($null -ne $python) { $candidates += [pscustomobject]@{ Executable = $python.Source; Prefix = @() } }
    foreach ($path in @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python310\python.exe")
    )) {
        if (Test-Path -LiteralPath $path -PathType Leaf) { $candidates += [pscustomobject]@{ Executable = $path; Prefix = @() } }
    }
    foreach ($candidate in $candidates) {
        try {
            $version = & $candidate.Executable @($candidate.Prefix) -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ($LASTEXITCODE -eq 0 -and $version -in @("3.10", "3.11")) {
                return [pscustomobject]@{ Executable = $candidate.Executable; Prefix = @($candidate.Prefix); Version = [string]$version }
            }
        } catch { continue }
    }
    return $null
}

function Resolve-Ffmpeg {
    $command = Get-Command ffmpeg -ErrorAction SilentlyContinue
    if ($null -ne $command) { return $command.Source }
    $link = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links\ffmpeg.exe"
    if (Test-Path -LiteralPath $link -PathType Leaf) { return $link }
    $packages = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
    if (Test-Path -LiteralPath $packages -PathType Container) {
        $candidate = Get-ChildItem -LiteralPath $packages -Filter ffmpeg.exe -File -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($null -ne $candidate) { return $candidate.FullName }
    }
    return $null
}

function Ensure-WingetPackage([string]$PackageId, [string]$Label) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($null -eq $winget) { throw "$Label is missing and winget is unavailable" }
    Invoke-NativeChecked $winget.Source @(
        "install", "--id", $PackageId, "--exact", "--scope", "user", "--silent",
        "--accept-package-agreements", "--accept-source-agreements", "--disable-interactivity"
    ) "$Label installation"
}

function Assert-DrivePolicy([string]$Path, [string]$Label) {
    $full = [IO.Path]::GetFullPath($Path)
    if (-not [IO.Path]::IsPathRooted($full)) { throw "$Label must be an absolute Windows path" }
    $drive = [IO.Path]::GetPathRoot($full).TrimEnd("\")
    if (-not $AllowNonDDrive -and $drive -ne "D:") { throw "$Label must be on D:\A.I unless -AllowNonDDrive is used for CI" }
    return $full
}

function Copy-AudioIntoInbox([string]$Path, [string]$Inbox) {
    $full = [IO.Path]::GetFullPath($Path)
    if (-not (Test-Path -LiteralPath $full -PathType Leaf)) { return }
    $extension = [IO.Path]::GetExtension($full).ToLowerInvariant()
    if ($SupportedExtensions -notcontains $extension) { return }
    $sha = Get-Sha256 $full
    $safeBase = [regex]::Replace([IO.Path]::GetFileNameWithoutExtension($full), "[^A-Za-z0-9 _-]+", "-").Trim()
    if (-not $safeBase) { $safeBase = "audio" }
    $destination = Join-Path $Inbox "$safeBase-$($sha.Substring(0, 10))$extension"
    if (-not (Test-Path -LiteralPath $destination -PathType Leaf)) {
        Copy-Item -LiteralPath $full -Destination $destination
    }
}

function New-ShellShortcut([string]$Path, [string]$Target, [string]$Arguments = "", [string]$WorkingDirectory = "") {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($Path)
    $shortcut.TargetPath = $Target
    $shortcut.Arguments = $Arguments
    if ($WorkingDirectory) { $shortcut.WorkingDirectory = $WorkingDirectory }
    $shortcut.Save()
}

if ($env:OS -ne "Windows_NT") { throw "Echoes Autopilot currently supports Windows only" }

$source = [IO.Path]::GetFullPath($SourceRoot)
$autopilot = Assert-DrivePolicy $AutopilotRoot "AutopilotRoot"
$runtime = Assert-DrivePolicy $RuntimeRoot "RuntimeRoot"
$inbox = Assert-DrivePolicy $InboxRoot "InboxRoot"
$results = Assert-DrivePolicy $ResultsRoot "ResultsRoot"
$control = Assert-DrivePolicy $ControlRoot "ControlRoot"

$requiredSourceFiles = @(
    "scripts\install-song-activity-runtime.ps1",
    "scripts\analyze-song-activity.ps1",
    "scripts\invoke-echoes-autopilot.ps1",
    "config\echoes-autopilot-policy.v1.json",
    "config\local-song-activity-runtime.v1.json",
    "config\models\silero-vad-6.2.1.json",
    "requirements\song-activity-runtime-windows.txt",
    "tools\provision_silero_vad.py",
    "tools\prove_silero_vad.py",
    "tools\silero_speech_segments.py",
    "tools\song_activity_timeline.py",
    "tools\build_song_activity_timeline.py"
)
foreach ($relative in $requiredSourceFiles) {
    $candidate = Join-Path $source $relative
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { throw "Package payload is missing: $candidate" }
}

$python = Resolve-CompatiblePython $PythonExecutable
if ($null -eq $python -and -not $SkipPrerequisiteInstall) {
    Ensure-WingetPackage "Python.Python.3.11" "Python 3.11"
    $python = Resolve-CompatiblePython ""
}
if ($null -eq $python) { throw "Python 3.10 or 3.11 could not be resolved" }

$ffmpegPath = Resolve-Ffmpeg
if ($null -eq $ffmpegPath -and -not $SkipPrerequisiteInstall) {
    Ensure-WingetPackage "Gyan.FFmpeg" "FFmpeg"
    $ffmpegPath = Resolve-Ffmpeg
}

foreach ($directory in @($autopilot, $inbox, $results, $control, (Join-Path $control "logs"))) {
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
}

$runtimeInstaller = Join-Path $source "scripts\install-song-activity-runtime.ps1"
$runtimeArguments = @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $runtimeInstaller,
    "-InstallRoot", $runtime,
    "-SourceRoot", $source,
    "-PythonExecutable", $python.Executable
)
if ($AllowNonDDrive) { $runtimeArguments += "-AllowNonDDrive" }
Invoke-NativeChecked "powershell.exe" $runtimeArguments "Echoes local runtime installation"

$installedController = Join-Path $autopilot "Invoke-EchoesAutopilot.ps1"
$installedPolicy = Join-Path $autopilot "echoes-autopilot-policy.v1.json"
Copy-Item -LiteralPath (Join-Path $source "scripts\invoke-echoes-autopilot.ps1") -Destination $installedController -Force
Copy-Item -LiteralPath (Join-Path $source "config\echoes-autopilot-policy.v1.json") -Destination $installedPolicy -Force

$runNowCmd = Join-Path $autopilot "RUN-ECHOES-AUTOPILOT-NOW.cmd"
$runNowContent = @"
@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$installedController" -AutopilotRoot "$autopilot" -RuntimeRoot "$runtime" -InboxRoot "$inbox" -ResultsRoot "$results" -ControlRoot "$control" -PolicyPath "$installedPolicy" -Interactive
if errorlevel 1 pause
"@
$runNowContent | Set-Content -LiteralPath $runNowCmd -Encoding ascii

$copiedInputPaths = @()
$seedFolder = Join-Path $source "SONGS-TO-ANALYZE"
if (Test-Path -LiteralPath $seedFolder -PathType Container) {
    $InputPaths += @(Get-ChildItem -LiteralPath $seedFolder -File -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName)
}
foreach ($inputPath in $InputPaths) {
    $before = @(Get-ChildItem -LiteralPath $inbox -File -ErrorAction SilentlyContinue).Count
    Copy-AudioIntoInbox $inputPath $inbox
    $after = @(Get-ChildItem -LiteralPath $inbox -File -ErrorAction SilentlyContinue).Count
    if ($after -gt $before) { $copiedInputPaths += [IO.Path]::GetFullPath($inputPath) }
}

$taskInstalled = $false
$taskError = $null
$startupFallbackInstalled = $false
if (-not $SkipScheduledTask) {
    try {
        $taskArguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$installedController`" -AutopilotRoot `"$autopilot`" -RuntimeRoot `"$runtime`" -InboxRoot `"$inbox`" -ResultsRoot `"$results`" -ControlRoot `"$control`" -PolicyPath `"$installedPolicy`""
        $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $taskArguments
        $trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Description "Processes EchoesInbox with verified local AI audio analysis" -Force -RunLevel Limited | Out-Null
        $taskInstalled = $true
    } catch {
        $taskError = $_.Exception.Message
        try {
            $startup = [Environment]::GetFolderPath("Startup")
            New-ShellShortcut (Join-Path $startup "Echoes Autopilot.lnk") $runNowCmd "" $autopilot
            $startupFallbackInstalled = $true
        } catch {
            $taskError = "$taskError | startup fallback failed: $($_.Exception.Message)"
        }
    }
}

try {
    $desktop = [Environment]::GetFolderPath("Desktop")
    New-ShellShortcut (Join-Path $desktop "Echoes Autopilot.lnk") $runNowCmd "" $autopilot
    New-ShellShortcut (Join-Path $desktop "Echoes Inbox.lnk") "explorer.exe" "`"$inbox`"" $inbox
    New-ShellShortcut (Join-Path $desktop "Echoes Results.lnk") "explorer.exe" "`"$results`"" $results
} catch {
    Write-Warning "Desktop shortcuts were not created: $($_.Exception.Message)"
}

$manifest = [ordered]@{
    schema = $InstallSchema
    status = "PASS"
    installedAtUtc = [DateTime]::UtcNow.ToString("o")
    version = "1.0.0"
    sourceRoot = $source
    paths = [ordered]@{
        autopilotRoot = $autopilot
        runtimeRoot = $runtime
        inboxRoot = $inbox
        resultsRoot = $results
        controlRoot = $control
        controller = $installedController
        policy = $installedPolicy
        runNow = $runNowCmd
    }
    prerequisites = [ordered]@{
        pythonVersion = $python.Version
        pythonExecutable = $python.Executable
        ffmpegDetected = ($null -ne $ffmpegPath)
        ffmpegPath = $ffmpegPath
        prerequisiteInstallSkipped = [bool]$SkipPrerequisiteInstall
    }
    automation = [ordered]@{
        scheduledTaskName = $TaskName
        scheduledTaskInstalled = $taskInstalled
        startupFallbackInstalled = $startupFallbackInstalled
        taskError = $taskError
        intervalMinutes = 5
    }
    seededInputs = $copiedInputPaths
    fileSha256 = [ordered]@{
        controller = Get-Sha256 $installedController
        policy = Get-Sha256 $installedPolicy
        runtimeManifest = Get-Sha256 (Join-Path $runtime "runtime-manifest.json")
    }
    truthBoundary = [ordered]@{
        packageInstalledOnCurrentHost = $true
        autonomousLoopInstalled = ($taskInstalled -or $startupFallbackInstalled)
        remotePolicyFetchEnabled = (-not $SkipRemotePolicy)
        hpOmenExecutionProven = $false
        userSongAnalyzed = $false
        audioUploadAuthorized = $false
        sourceDeletionAuthorized = $false
        arbitraryRemoteCommandsAuthorized = $false
        requiresOperatorApproval = $true
    }
}
$manifestPath = Join-Path $control "autopilot-installation-manifest.json"
$manifest | ConvertTo-Json -Depth 16 | Set-Content -LiteralPath $manifestPath -Encoding utf8

if (-not $NoInitialRun) {
    $controllerArguments = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $installedController,
        "-AutopilotRoot", $autopilot,
        "-RuntimeRoot", $runtime,
        "-InboxRoot", $inbox,
        "-ResultsRoot", $results,
        "-ControlRoot", $control,
        "-PolicyPath", $installedPolicy,
        "-Interactive"
    )
    if ($SkipRemotePolicy) { $controllerArguments += "-SkipRemotePolicy" }
    if ($NoOpen) { $controllerArguments = @($controllerArguments | Where-Object { $_ -ne "-Interactive" }) }
    & powershell.exe @controllerArguments
    $controllerExit = $LASTEXITCODE
    if ($controllerExit -notin @(0, 2)) { throw "Initial Echoes Autopilot run failed with exit code $controllerExit" }
}

Write-Host "EchoesAutopilotInstall PASS root=$autopilot runtime=$runtime task=$taskInstalled fallback=$startupFallbackInstalled ffmpeg=$($null -ne $ffmpegPath) upload=false delete=false hpOmen=false"
Write-Host "Put songs here: $inbox"
Write-Host "Run now: $runNowCmd"
Write-Host "Control bundle: $(Join-Path $control 'Echoes-Control-Bundle-Latest.zip')"
