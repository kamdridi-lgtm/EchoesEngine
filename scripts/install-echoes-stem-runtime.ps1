[CmdletBinding()]
param(
    [string]$SourceRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$StemRuntimeRoot = "D:\A.I\EchoesStemRuntime",
    [string]$ResultsRoot = "D:\A.I\EchoesResults",
    [string]$ControlRoot = "D:\A.I\EchoesControl",
    [string]$AnalysisLedgerPath = "D:\A.I\EchoesControl\autopilot-ledger.json",
    [string]$PythonExecutable = "",
    [switch]$AllowNonDDrive,
    [switch]$SkipPrerequisiteInstall,
    [switch]$SkipScheduledTask,
    [switch]$NoInitialRun,
    [switch]$NoOpen
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$InstallSchema = "echoes.stem-runtime-installation.v1"
$DemucsVersion = "4.1.0"
$DemucsWheelSha256 = "4916a804702033ce934a6cdfa7e38dde03f7a7a6e85f41d0120eefe9e2966758"
$NumpyVersion = "1.26.4"
$TorchVersion = "2.7.1"
$TorchaudioVersion = "2.7.1"
$ModelSha256 = "8726e21a993978c7ba086d3872e7608d7d5bfca646ca4aca459ffda844faa8b4"
$TaskName = "Echoes Stem Autopilot"

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Invoke-NativeChecked([string]$Executable, [string[]]$Arguments, [string]$Label) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

function Assert-DrivePolicy([string]$Path, [string]$Label) {
    $full = [IO.Path]::GetFullPath($Path)
    if (-not [IO.Path]::IsPathRooted($full)) { throw "$Label must be an absolute Windows path" }
    $drive = [IO.Path]::GetPathRoot($full).TrimEnd("\")
    if (-not $AllowNonDDrive -and $drive -ne "D:") {
        throw "$Label must be on D:\A.I unless -AllowNonDDrive is used for CI"
    }
    return $full
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
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            $candidates += [pscustomobject]@{ Executable = $path; Prefix = @() }
        }
    }
    foreach ($candidate in $candidates) {
        try {
            $version = & $candidate.Executable @($candidate.Prefix) -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ($LASTEXITCODE -eq 0 -and $version -in @("3.10", "3.11")) {
                return [pscustomobject]@{
                    Executable = $candidate.Executable
                    Prefix = @($candidate.Prefix)
                    Version = ([string]$version).Trim()
                }
            }
        } catch { continue }
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

function Resolve-FfmpegBinary([string]$Name) {
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -ne $command) { return $command.Source }
    $link = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links\$Name.exe"
    if (Test-Path -LiteralPath $link -PathType Leaf) { return $link }
    $packages = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
    if (Test-Path -LiteralPath $packages -PathType Container) {
        $candidate = Get-ChildItem -LiteralPath $packages -Filter "$Name.exe" -File -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($null -ne $candidate) { return $candidate.FullName }
    }
    return $null
}

if ($env:OS -ne "Windows_NT") {
    throw "Echoes stem runtime currently supports Windows only"
}

$source = [IO.Path]::GetFullPath($SourceRoot)
$runtime = Assert-DrivePolicy $StemRuntimeRoot "StemRuntimeRoot"
$results = Assert-DrivePolicy $ResultsRoot "ResultsRoot"
$control = Assert-DrivePolicy $ControlRoot "ControlRoot"
$analysisLedger = [IO.Path]::GetFullPath($AnalysisLedgerPath)

$requiredSourceFiles = @(
    "tools\provision_demucs_htdemucs.py",
    "tools\separate_song_stems.py",
    "tools\echoes_stem_autopilot.py",
    "scripts\invoke-echoes-stem-autopilot.ps1",
    "config\echoes-stem-runtime.v1.json"
)
foreach ($relative in $requiredSourceFiles) {
    $candidate = Join-Path $source $relative
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        throw "Required source file is missing: $candidate"
    }
}

$python = Resolve-CompatiblePython $PythonExecutable
if ($null -eq $python -and -not $SkipPrerequisiteInstall) {
    Ensure-WingetPackage "Python.Python.3.11" "Python 3.11"
    $python = Resolve-CompatiblePython $PythonExecutable
}
if ($null -eq $python) {
    throw "Python 3.10 or 3.11 is required and automatic installation did not resolve it"
}

$ffmpeg = Resolve-FfmpegBinary "ffmpeg"
$ffprobe = Resolve-FfmpegBinary "ffprobe"
if (($null -eq $ffmpeg -or $null -eq $ffprobe) -and -not $SkipPrerequisiteInstall) {
    Ensure-WingetPackage "Gyan.FFmpeg" "FFmpeg"
    $ffmpeg = Resolve-FfmpegBinary "ffmpeg"
    $ffprobe = Resolve-FfmpegBinary "ffprobe"
}
if ($null -eq $ffmpeg -or $null -eq $ffprobe) {
    throw "FFmpeg and ffprobe are required and automatic installation did not resolve them"
}

foreach ($directory in @(
    $runtime,
    (Join-Path $runtime "tools"),
    (Join-Path $runtime "models"),
    (Join-Path $runtime "downloads"),
    $results,
    $control,
    (Join-Path $control "logs")
)) {
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
}

$copyMap = [ordered]@{
    "tools\provision_demucs_htdemucs.py" = "tools\provision_demucs_htdemucs.py"
    "tools\separate_song_stems.py" = "tools\separate_song_stems.py"
    "tools\echoes_stem_autopilot.py" = "tools\echoes_stem_autopilot.py"
    "scripts\invoke-echoes-stem-autopilot.ps1" = "Run-Echoes-Stems.ps1"
    "config\echoes-stem-runtime.v1.json" = "echoes-stem-runtime.v1.json"
}
foreach ($sourceRelative in $copyMap.Keys) {
    $destination = Join-Path $runtime $copyMap[$sourceRelative]
    $parent = Split-Path -Parent $destination
    if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    Copy-Item -LiteralPath (Join-Path $source $sourceRelative) -Destination $destination -Force
}

$venvRoot = Join-Path $runtime ".venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    Invoke-NativeChecked $python.Executable (@($python.Prefix) + @("-m", "venv", $venvRoot)) "Stem virtual environment creation"
}
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    throw "Stem virtual environment was not created: $venvPython"
}

Invoke-NativeChecked $venvPython @("-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--upgrade", "pip") "Stem pip bootstrap"
Invoke-NativeChecked $venvPython @("-m", "pip", "install", "--disable-pip-version-check", "--no-input", "numpy==$NumpyVersion") "Pinned NumPy installation"

$nvidiaSmi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
$gpuDetected = $null -ne $nvidiaSmi
$torchIndex = if ($gpuDetected) { "https://download.pytorch.org/whl/cu118" } else { "https://download.pytorch.org/whl/cpu" }
$torchInstall = @(
    "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--upgrade",
    "torch==$TorchVersion", "torchaudio==$TorchaudioVersion", "--index-url", $torchIndex
)
& $venvPython @torchInstall
if ($LASTEXITCODE -ne 0 -and $gpuDetected) {
    $torchIndex = "https://download.pytorch.org/whl/cpu"
    Invoke-NativeChecked $venvPython @(
        "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--upgrade", "--force-reinstall",
        "torch==$TorchVersion", "torchaudio==$TorchaudioVersion", "--index-url", $torchIndex
    ) "CPU PyTorch fallback installation"
} elseif ($LASTEXITCODE -ne 0) {
    throw "PyTorch installation failed"
}

$downloads = Join-Path $runtime "downloads"
Get-ChildItem -LiteralPath $downloads -Filter "demucs-*.whl" -File -ErrorAction SilentlyContinue | Remove-Item -Force
Invoke-NativeChecked $venvPython @(
    "-m", "pip", "download", "--disable-pip-version-check", "--no-deps", "demucs==$DemucsVersion", "--dest", $downloads
) "Demucs wheel download"
$demucsWheel = Get-ChildItem -LiteralPath $downloads -Filter "demucs-$DemucsVersion-*.whl" -File | Select-Object -First 1
if ($null -eq $demucsWheel) { throw "Pinned Demucs wheel was not downloaded" }
$wheelSha = Get-Sha256 $demucsWheel.FullName
if ($wheelSha -ne $DemucsWheelSha256) {
    throw "Demucs wheel SHA-256 mismatch: expected $DemucsWheelSha256 got $wheelSha"
}
Invoke-NativeChecked $venvPython @(
    "-m", "pip", "install", "--disable-pip-version-check", "--no-input", $demucsWheel.FullName
) "Pinned Demucs installation"

$inventoryJson = & $venvPython -c 'import json,numpy,torch,torchaudio,demucs; print(json.dumps(dict(numpy=numpy.__version__,torch=torch.__version__,torchaudio=torchaudio.__version__,demucs=demucs.__version__,cudaAvailable=torch.cuda.is_available(),cudaRuntime=torch.version.cuda,deviceCount=torch.cuda.device_count(),deviceName=(torch.cuda.get_device_name(0) if torch.cuda.is_available() else None))))'
if ($LASTEXITCODE -ne 0) { throw "Unable to inspect installed stem dependencies" }
$inventory = $inventoryJson | ConvertFrom-Json
if ($inventory.numpy -ne $NumpyVersion -or -not ([string]$inventory.torch).StartsWith($TorchVersion) -or -not ([string]$inventory.torchaudio).StartsWith($TorchaudioVersion) -or $inventory.demucs -ne $DemucsVersion) {
    throw "Installed stem dependency versions drifted from the locked contract"
}

$modelRoot = Join-Path $runtime "models"
Invoke-NativeChecked $venvPython @(
    (Join-Path $runtime "tools\provision_demucs_htdemucs.py"),
    "--output-dir", $modelRoot,
    "--expected-sha256", $ModelSha256
) "HTDemucs model provisioning"
$modelManifestPath = Join-Path $modelRoot "model-provisioning-manifest.json"
if (-not (Test-Path -LiteralPath $modelManifestPath -PathType Leaf)) { throw "HTDemucs provisioning manifest is missing" }
$modelManifest = Get-Content -LiteralPath $modelManifestPath -Raw | ConvertFrom-Json
if ($modelManifest.status -ne "PASS" -or $modelManifest.model.sha256 -ne $ModelSha256) {
    throw "HTDemucs provisioning evidence is not PASS"
}

$installedHashes = [ordered]@{}
foreach ($relative in @(
    "Run-Echoes-Stems.ps1",
    "echoes-stem-runtime.v1.json",
    "tools\provision_demucs_htdemucs.py",
    "tools\separate_song_stems.py",
    "tools\echoes_stem_autopilot.py"
)) {
    $installedHashes[$relative] = Get-Sha256 (Join-Path $runtime $relative)
}

$gpuInfo = $null
if ($gpuDetected) {
    try {
        $gpuInfo = & $nvidiaSmi.Source --query-gpu=name,driver_version,memory.total --format=csv,noheader,nounits 2>$null
    } catch { $gpuInfo = $null }
}

$runtimeManifest = [ordered]@{
    schema = $InstallSchema
    status = "PASS"
    installedAtUtc = [DateTime]::UtcNow.ToString("o")
    root = $runtime
    python = [ordered]@{
        bootstrapExecutable = $python.Executable
        bootstrapPrefix = @($python.Prefix)
        version = $python.Version
        executable = $venvPython
    }
    packages = [ordered]@{
        numpy = $inventory.numpy
        demucs = $inventory.demucs
        demucsWheelSha256 = $wheelSha
        torch = $inventory.torch
        torchaudio = $inventory.torchaudio
        torchIndex = $torchIndex
    }
    compute = [ordered]@{
        nvidiaSmiDetected = $gpuDetected
        nvidiaSmi = $gpuInfo
        cudaAvailable = [bool]$inventory.cudaAvailable
        cudaRuntime = $inventory.cudaRuntime
        deviceCount = [int]$inventory.deviceCount
        deviceName = $inventory.deviceName
        cpuFallbackEnabled = $true
    }
    model = $modelManifest.model
    ffmpeg = [ordered]@{
        executable = $ffmpeg
        ffprobe = $ffprobe
    }
    paths = [ordered]@{
        results = $results
        control = $control
        analysisLedger = $analysisLedger
        launcher = (Join-Path $runtime "Run-Echoes-Stems.ps1")
    }
    installedFileSha256 = $installedHashes
    truthBoundary = [ordered]@{
        runtimeInstalledOnCurrentHost = $true
        fullModelSha256Verified = $true
        realStemInferenceExecuted = $false
        hpOmenExecutionProven = $false
        userSongSeparated = $false
        vocalIsolationProven = $false
        stemSeparationProven = $false
        gpuInferenceProven = $false
        sourceAudioDeleted = $false
        sourceAudioUploaded = $false
        voiceConversionProven = $false
        scheduledExecutionObserved = $false
    }
}
$runtimeManifestPath = Join-Path $runtime "stem-runtime-manifest.json"
$runtimeManifest | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $runtimeManifestPath -Encoding utf8

$scheduledTaskInstalled = $false
$startupFallbackInstalled = $false
$launcherPath = Join-Path $runtime "Run-Echoes-Stems.ps1"
if (-not $SkipScheduledTask) {
    $taskCommand = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$launcherPath`" -StemRuntimeRoot `"$runtime`" -ResultsRoot `"$results`" -ControlRoot `"$control`" -AnalysisLedgerPath `"$analysisLedger`" -MaxFiles 2 -DeclareUserSong"
    & schtasks.exe /Create /F /SC MINUTE /MO 10 /TN $TaskName /TR $taskCommand | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $scheduledTaskInstalled = $true
    } else {
        $startup = [Environment]::GetFolderPath("Startup")
        if ($startup) {
            $fallback = Join-Path $startup "Echoes-Stem-Autopilot.cmd"
            "@echo off`r`npowershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$launcherPath`" -DeclareUserSong`r`n" | Set-Content -LiteralPath $fallback -Encoding ascii
            $startupFallbackInstalled = $true
        }
    }
}

$installStatus = [ordered]@{
    schema = "echoes.stem-autopilot-installation.v1"
    status = "PASS"
    installedAtUtc = [DateTime]::UtcNow.ToString("o")
    stemRuntimeRoot = $runtime
    resultsRoot = $results
    controlRoot = $control
    analysisLedgerPath = $analysisLedger
    scheduledTask = [ordered]@{
        name = $TaskName
        installed = $scheduledTaskInstalled
        startupFallbackInstalled = $startupFallbackInstalled
        intervalMinutes = 10
        executionObserved = $false
    }
    sourceAudioDeleted = $false
    sourceAudioUploaded = $false
}
$installStatus | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $control "stem-autopilot-installation.json") -Encoding utf8

if (-not $NoInitialRun) {
    if (Test-Path -LiteralPath $analysisLedger -PathType Leaf) {
        $initialArguments = @(
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $launcherPath,
            "-StemRuntimeRoot", $runtime,
            "-ResultsRoot", $results,
            "-ControlRoot", $control,
            "-AnalysisLedgerPath", $analysisLedger,
            "-MaxFiles", "2",
            "-DeclareUserSong"
        )
        if (-not $NoOpen) { $initialArguments += "-Interactive" }
        & powershell.exe @initialArguments
        if ($LASTEXITCODE -notin @(0, 2)) {
            throw "Initial Echoes Stem Autopilot run failed with exit code $LASTEXITCODE"
        }
    } else {
        Write-Host "Analysis ledger not found yet; stem controller installed and waiting: $analysisLedger"
    }
}

Write-Host "EchoesStemRuntimeInstall PASS root=$runtime numpy=$($inventory.numpy) demucs=$($inventory.demucs) torch=$($inventory.torch) cuda=$($inventory.cudaAvailable) model=$ModelSha256"
Write-Host "Results: $results"
Write-Host "Control: $(Join-Path $control 'Echoes-Stem-Control-Bundle-Latest.zip')"
