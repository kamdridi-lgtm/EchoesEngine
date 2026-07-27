[CmdletBinding()]
param(
    [string]$InstallRoot = "D:\A.I\EchoesEngineRuntime",
    [string]$SourceRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$PythonExecutable = "",
    [switch]$AllowNonDDrive,
    [switch]$SkipModelProvision
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ExpectedModelSha256 = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"
$ExpectedModelSize = 2327524
$RuntimeSchema = "echoes.local-song-activity-runtime-installation.v1"

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Invoke-NativeChecked([string]$Executable, [string[]]$Arguments, [string]$Label) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

function Resolve-CompatiblePython([string]$RequestedExecutable) {
    $candidates = @()
    if ($RequestedExecutable) {
        $candidates += [pscustomobject]@{ Executable = $RequestedExecutable; Prefix = @() }
    } else {
        $py = Get-Command py -ErrorAction SilentlyContinue
        if ($null -ne $py) {
            $candidates += [pscustomobject]@{ Executable = $py.Source; Prefix = @("-3.10") }
            $candidates += [pscustomobject]@{ Executable = $py.Source; Prefix = @("-3.11") }
        }
        $python = Get-Command python -ErrorAction SilentlyContinue
        if ($null -ne $python) {
            $candidates += [pscustomobject]@{ Executable = $python.Source; Prefix = @() }
        }
    }

    foreach ($candidate in $candidates) {
        try {
            $version = & $candidate.Executable @($candidate.Prefix) -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ($LASTEXITCODE -eq 0 -and $version -in @("3.10", "3.11")) {
                return [pscustomobject]@{
                    Executable = $candidate.Executable
                    Prefix = @($candidate.Prefix)
                    Version = [string]$version
                }
            }
        } catch {
            continue
        }
    }
    throw "Python 3.10 or 3.11 is required. Python 3.12+ and 3.14 are not accepted by this locked runtime."
}

if ($env:OS -ne "Windows_NT") {
    throw "The local Echoes song activity runtime currently supports Windows only"
}

$source = [IO.Path]::GetFullPath($SourceRoot)
$install = [IO.Path]::GetFullPath($InstallRoot)
if (-not [IO.Path]::IsPathRooted($install)) {
    throw "InstallRoot must be an absolute Windows path"
}
$installDrive = [IO.Path]::GetPathRoot($install).TrimEnd("\")
if (-not $AllowNonDDrive -and $installDrive -ne "D:") {
    throw "INSTALL_ROOT_BLOCKED: the production runtime must be installed on D:\A.I unless -AllowNonDDrive is explicitly used for CI"
}
if ($install -eq [IO.Path]::GetPathRoot($install)) {
    throw "InstallRoot cannot be a drive root"
}

$requiredSourceFiles = @(
    "tools\provision_silero_vad.py",
    "tools\prove_silero_vad.py",
    "tools\silero_speech_segments.py",
    "tools\song_activity_timeline.py",
    "tools\build_song_activity_timeline.py",
    "scripts\analyze-song-activity.ps1",
    "requirements\song-activity-runtime-windows.txt",
    "config\local-song-activity-runtime.v1.json",
    "config\models\silero-vad-6.2.1.json"
)
foreach ($relative in $requiredSourceFiles) {
    $candidate = Join-Path $source $relative
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        throw "Required source file is missing: $candidate"
    }
}

$python = Resolve-CompatiblePython $PythonExecutable
Write-Host "Using Python $($python.Version): $($python.Executable) $($python.Prefix -join ' ')"

$directories = @(
    $install,
    (Join-Path $install "tools"),
    (Join-Path $install "config"),
    (Join-Path $install "config\models"),
    (Join-Path $install "models"),
    (Join-Path $install "jobs"),
    (Join-Path $install "provisioning")
)
foreach ($directory in $directories) {
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
}

$copyMap = [ordered]@{
    "tools\provision_silero_vad.py" = "tools\provision_silero_vad.py"
    "tools\prove_silero_vad.py" = "tools\prove_silero_vad.py"
    "tools\silero_speech_segments.py" = "tools\silero_speech_segments.py"
    "tools\song_activity_timeline.py" = "tools\song_activity_timeline.py"
    "tools\build_song_activity_timeline.py" = "tools\build_song_activity_timeline.py"
    "requirements\song-activity-runtime-windows.txt" = "requirements.txt"
    "config\local-song-activity-runtime.v1.json" = "config\local-song-activity-runtime.v1.json"
    "config\models\silero-vad-6.2.1.json" = "config\models\silero-vad-6.2.1.json"
    "scripts\analyze-song-activity.ps1" = "Analyze-EchoesSong.ps1"
}
foreach ($sourceRelative in $copyMap.Keys) {
    $destinationRelative = $copyMap[$sourceRelative]
    $destination = Join-Path $install $destinationRelative
    $destinationParent = Split-Path -Parent $destination
    if ($destinationParent) { New-Item -ItemType Directory -Force -Path $destinationParent | Out-Null }
    Copy-Item -LiteralPath (Join-Path $source $sourceRelative) -Destination $destination -Force
}

$venvRoot = Join-Path $install ".venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    $venvArguments = @($python.Prefix) + @("-m", "venv", $venvRoot)
    Invoke-NativeChecked $python.Executable $venvArguments "Virtual environment creation"
}
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    throw "Virtual environment Python was not created: $venvPython"
}

Invoke-NativeChecked $venvPython @("-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--upgrade", "pip") "pip bootstrap"
Invoke-NativeChecked $venvPython @("-m", "pip", "install", "--disable-pip-version-check", "--no-input", "-r", (Join-Path $install "requirements.txt")) "Pinned dependency installation"

$modelPath = Join-Path $install "models\silero_vad.onnx"
$provisioningRoot = Join-Path $install "provisioning"
if (-not $SkipModelProvision) {
    $provisionArguments = @(
        (Join-Path $install "tools\provision_silero_vad.py"),
        "--output-dir", $provisioningRoot,
        "--expected-model-sha256", $ExpectedModelSha256,
        "--expected-model-size", [string]$ExpectedModelSize
    )
    Invoke-NativeChecked $venvPython $provisionArguments "Pinned Silero model provisioning"
    $provisionedModel = Join-Path $provisioningRoot "model\silero_vad.onnx"
    if (-not (Test-Path -LiteralPath $provisionedModel -PathType Leaf)) {
        throw "Provisioning did not produce the pinned Silero model"
    }
    Copy-Item -LiteralPath $provisionedModel -Destination $modelPath -Force
}
if (-not (Test-Path -LiteralPath $modelPath -PathType Leaf)) {
    throw "Installed Silero model is missing: $modelPath"
}
$modelSha = Get-Sha256 $modelPath
$modelSize = (Get-Item -LiteralPath $modelPath).Length
if ($modelSha -ne $ExpectedModelSha256 -or $modelSize -ne $ExpectedModelSize) {
    throw "Installed Silero model digest or size does not match the pinned production model"
}

$dependencyJson = & $venvPython -c 'import json, numpy, onnx, onnxruntime; print(json.dumps({"numpy": numpy.__version__, "onnx": onnx.__version__, "onnxruntime": onnxruntime.__version__}))'
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect installed Python dependencies"
}
$dependencies = $dependencyJson | ConvertFrom-Json
if ($dependencies.numpy -ne "1.26.4" -or $dependencies.onnx -ne "1.16.2" -or $dependencies.onnxruntime -ne "1.20.1") {
    throw "Installed dependency versions drifted from the runtime contract"
}

$sourceCommit = $null
$git = Get-Command git -ErrorAction SilentlyContinue
if ($null -ne $git) {
    $candidateCommit = & $git.Source -C $source rev-parse HEAD 2>$null
    if ($LASTEXITCODE -eq 0 -and $candidateCommit) {
        $sourceCommit = ([string]$candidateCommit).Trim()
    }
}
$ffmpegCommand = Get-Command ffmpeg -ErrorAction SilentlyContinue
$ffmpegPath = if ($null -ne $ffmpegCommand) { $ffmpegCommand.Source } else { $null }

$installedHashes = [ordered]@{}
foreach ($relative in @(
    "Analyze-EchoesSong.ps1",
    "requirements.txt",
    "tools\provision_silero_vad.py",
    "tools\prove_silero_vad.py",
    "tools\silero_speech_segments.py",
    "tools\song_activity_timeline.py",
    "tools\build_song_activity_timeline.py",
    "config\local-song-activity-runtime.v1.json",
    "config\models\silero-vad-6.2.1.json"
)) {
    $installedHashes[$relative] = Get-Sha256 (Join-Path $install $relative)
}

$manifest = [ordered]@{
    schema = $RuntimeSchema
    status = "PASS"
    installedAtUtc = [DateTime]::UtcNow.ToString("o")
    installRoot = $install
    sourceRoot = $source
    sourceCommit = $sourceCommit
    host = [ordered]@{
        computerName = $env:COMPUTERNAME
        os = [Environment]::OSVersion.VersionString
    }
    python = [ordered]@{
        bootstrapExecutable = $python.Executable
        bootstrapPrefix = @($python.Prefix)
        version = $python.Version
        virtualEnvironment = $venvRoot
        executable = $venvPython
        dependencies = [ordered]@{
            numpy = $dependencies.numpy
            onnx = $dependencies.onnx
            onnxruntime = $dependencies.onnxruntime
        }
    }
    model = [ordered]@{
        id = "silero-vad-6.2.1"
        path = $modelPath
        sha256 = $modelSha
        sizeBytes = $modelSize
        provider = "CPUExecutionProvider"
        integrityVerified = $true
    }
    ffmpeg = [ordered]@{
        detected = ($null -ne $ffmpegPath)
        path = $ffmpegPath
        requiredOnlyForCompressedInputs = $true
    }
    entrypoints = [ordered]@{
        analyze = (Join-Path $install "Analyze-EchoesSong.ps1")
        jobs = (Join-Path $install "jobs")
    }
    installedFileSha256 = $installedHashes
    truthBoundary = [ordered]@{
        runtimeInstalledOnCurrentHost = $true
        productionModelIntegrityProven = $true
        localAnalysisEntrypointInstalled = $true
        hpOmenExecutionProven = $false
        userSongAnalyzed = $false
        instrumentalClassificationProven = $false
        vocalIsolationProven = $false
        stemSeparationProven = $false
        voiceConversionProven = $false
        gpuInferenceProven = $false
        tensorRtInferenceProven = $false
        autonomousExecutionProven = $false
        executionAuthorized = $false
        requiresOperatorApproval = $true
    }
}
$manifestPath = Join-Path $install "runtime-manifest.json"
$manifest | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $manifestPath -Encoding utf8

Write-Host "EchoesLocalSongActivityInstall PASS root=$install model=$modelSha python=$($python.Version) ffmpeg=$($null -ne $ffmpegPath) hpOmen=false userSong=false"
Write-Host "Analyze with: powershell -ExecutionPolicy Bypass -File `"$(Join-Path $install 'Analyze-EchoesSong.ps1')`" -InputPath `"D:\Music\Your Song.wav`" -DeclareUserSong"
