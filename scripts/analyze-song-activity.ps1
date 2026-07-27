[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InputPath,
    [string]$RuntimeRoot = $PSScriptRoot,
    [string]$OutputRoot = "",
    [string]$JobId = "",
    [string]$ExpectedInputSha256 = "",
    [string]$FfmpegPath = "",
    [switch]$DeclareUserSong
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ExpectedModelSha256 = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"
$ExpectedModelSize = 2327524
$AllowedCompressedExtensions = @(".mp3", ".flac", ".m4a", ".aac", ".ogg")

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Invoke-NativeChecked([string]$Executable, [string[]]$Arguments, [string]$LogPath, [string]$Label) {
    $lines = & $Executable @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    $lines | Tee-Object -FilePath $LogPath | Out-Host
    if ($exitCode -ne 0) {
        throw "$Label failed with exit code $exitCode"
    }
}

if ($env:OS -ne "Windows_NT") {
    throw "Analyze-EchoesSong.ps1 currently supports Windows only"
}

$runtime = [IO.Path]::GetFullPath($RuntimeRoot)
$runtimeManifestPath = Join-Path $runtime "runtime-manifest.json"
$pythonPath = Join-Path $runtime ".venv\Scripts\python.exe"
$modelPath = Join-Path $runtime "models\silero_vad.onnx"
$builderPath = Join-Path $runtime "tools\build_song_activity_timeline.py"

foreach ($requiredPath in @($runtimeManifestPath, $pythonPath, $modelPath, $builderPath)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Installed runtime file is missing: $requiredPath"
    }
}

$runtimeManifest = Get-Content -LiteralPath $runtimeManifestPath -Raw | ConvertFrom-Json
if ($runtimeManifest.schema -ne "echoes.local-song-activity-runtime-installation.v1" -or $runtimeManifest.status -ne "PASS") {
    throw "The local runtime manifest is invalid or not PASS"
}
$modelSha = Get-Sha256 $modelPath
$modelSize = (Get-Item -LiteralPath $modelPath).Length
if ($modelSha -ne $ExpectedModelSha256 -or $modelSize -ne $ExpectedModelSize -or $runtimeManifest.model.sha256 -ne $ExpectedModelSha256) {
    throw "The installed Silero model failed SHA-256 verification"
}

$sourcePath = [IO.Path]::GetFullPath($InputPath)
if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
    throw "Input audio file does not exist: $sourcePath"
}
$sourceExtension = [IO.Path]::GetExtension($sourcePath).ToLowerInvariant()
if ($sourceExtension -ne ".wav" -and $AllowedCompressedExtensions -notcontains $sourceExtension) {
    throw "Unsupported input extension: $sourceExtension"
}
$sourceSha = Get-Sha256 $sourcePath
if ($ExpectedInputSha256 -and $sourceSha -ne $ExpectedInputSha256.Trim().ToLowerInvariant()) {
    throw "Input audio SHA-256 does not match ExpectedInputSha256"
}

if (-not $OutputRoot) {
    $OutputRoot = Join-Path $runtime "jobs"
}
$outputRootFull = [IO.Path]::GetFullPath($OutputRoot)
New-Item -ItemType Directory -Force -Path $outputRootFull | Out-Null

if (-not $JobId) {
    $safeName = [regex]::Replace([IO.Path]::GetFileNameWithoutExtension($sourcePath), "[^A-Za-z0-9_-]+", "-").Trim("-")
    if (-not $safeName) { $safeName = "audio" }
    $JobId = "$([DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ'))-$safeName-$($sourceSha.Substring(0, 10))"
}
if ($JobId -notmatch "^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$") {
    throw "JobId must contain only letters, numbers, hyphen, or underscore and be at most 80 characters"
}
$jobDirectory = Join-Path $outputRootFull $JobId
if (Test-Path -LiteralPath $jobDirectory) {
    throw "JOB_ALREADY_EXISTS: $jobDirectory"
}
New-Item -ItemType Directory -Path $jobDirectory | Out-Null

$analysisInput = $sourcePath
$converted = $false
$ffmpegUsed = $null
$conversionLog = Join-Path $jobDirectory "ffmpeg.log"
if ($sourceExtension -ne ".wav") {
    if ($FfmpegPath) {
        $ffmpegUsed = [IO.Path]::GetFullPath($FfmpegPath)
        if (-not (Test-Path -LiteralPath $ffmpegUsed -PathType Leaf)) {
            throw "Specified FFmpeg executable does not exist: $ffmpegUsed"
        }
    } else {
        $ffmpegCommand = Get-Command ffmpeg -ErrorAction SilentlyContinue
        if ($null -eq $ffmpegCommand) {
            throw "FFMPEG_REQUIRED: compressed audio requires ffmpeg.exe on PATH or -FfmpegPath"
        }
        $ffmpegUsed = $ffmpegCommand.Source
    }
    $analysisInput = Join-Path $jobDirectory "input-16k-mono.wav"
    $ffmpegArguments = @(
        "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-i", $sourcePath, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", $analysisInput
    )
    Invoke-NativeChecked $ffmpegUsed $ffmpegArguments $conversionLog "FFmpeg normalization"
    if (-not (Test-Path -LiteralPath $analysisInput -PathType Leaf)) {
        throw "FFmpeg did not produce the normalized WAV"
    }
    $converted = $true
}

$analysisInputSha = Get-Sha256 $analysisInput
$timelineDirectory = Join-Path $jobDirectory "timeline"
New-Item -ItemType Directory -Force -Path $timelineDirectory | Out-Null
$analysisLog = Join-Path $jobDirectory "analysis.log"
$sourceLabelPrefix = if ($DeclareUserSong) { "user-song" } else { "local-audio" }
$builderArguments = @(
    $builderPath,
    "--model", $modelPath,
    "--input", $analysisInput,
    "--output-dir", $timelineDirectory,
    "--source-label", "${sourceLabelPrefix}:$([IO.Path]::GetFileName($sourcePath))",
    "--expected-input-sha256", $analysisInputSha
)
if ($DeclareUserSong) {
    $builderArguments += "--declare-user-song"
}
Invoke-NativeChecked $pythonPath $builderArguments $analysisLog "Song activity timeline analysis"

$timelineJsonPath = Join-Path $timelineDirectory "song-activity-timeline.json"
$timelineCsvPath = Join-Path $timelineDirectory "song-activity-timeline.csv"
foreach ($outputPath in @($timelineJsonPath, $timelineCsvPath)) {
    if (-not (Test-Path -LiteralPath $outputPath -PathType Leaf)) {
        throw "Expected analysis output is missing: $outputPath"
    }
}
$timeline = Get-Content -LiteralPath $timelineJsonPath -Raw | ConvertFrom-Json
if ($timeline.status -ne "PASS") {
    throw "The generated song activity timeline is not PASS"
}

$analysisManifest = [ordered]@{
    schema = "echoes.local-song-activity-analysis-run.v1"
    status = "PASS"
    jobId = $JobId
    createdAtUtc = [DateTime]::UtcNow.ToString("o")
    runtime = [ordered]@{
        root = $runtime
        manifestPath = $runtimeManifestPath
        manifestSha256 = Get-Sha256 $runtimeManifestPath
        python = $pythonPath
    }
    source = [ordered]@{
        path = $sourcePath
        filename = [IO.Path]::GetFileName($sourcePath)
        extension = $sourceExtension
        sha256 = $sourceSha
        sizeBytes = (Get-Item -LiteralPath $sourcePath).Length
        declaredUserSong = [bool]$DeclareUserSong
    }
    normalizedAudio = [ordered]@{
        path = $analysisInput
        sha256 = $analysisInputSha
        sizeBytes = (Get-Item -LiteralPath $analysisInput).Length
        converted = $converted
        ffmpegPath = $ffmpegUsed
    }
    model = [ordered]@{
        path = $modelPath
        sha256 = $modelSha
        sizeBytes = $modelSize
        provider = "CPUExecutionProvider"
    }
    timeline = [ordered]@{
        jsonPath = $timelineJsonPath
        jsonSha256 = Get-Sha256 $timelineJsonPath
        csvPath = $timelineCsvPath
        csvSha256 = Get-Sha256 $timelineCsvPath
        canonicalSha256 = $timeline.summary.canonicalSha256
        durationSeconds = $timeline.summary.durationSeconds
        spanCount = $timeline.summary.spanCount
        speechSeconds = $timeline.summary.speechSeconds
        nonSpeechSeconds = $timeline.summary.nonSpeechSeconds
    }
    truthBoundary = [ordered]@{
        localAudioFileAnalyzed = $true
        voiceActivityTimelineProduced = $true
        userSongAnalyzed = [bool]$DeclareUserSong
        hpOmenExecutionProven = $false
        instrumentalClassificationProven = $false
        vocalIsolationProven = $false
        stemSeparationProven = $false
        voiceConversionProven = $false
        gpuInferenceProven = $false
        tensorRtInferenceProven = $false
        executionAuthorized = $false
        requiresOperatorApproval = $true
    }
}
$analysisManifestPath = Join-Path $jobDirectory "analysis-run-manifest.json"
$analysisManifest | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $analysisManifestPath -Encoding utf8

Write-Host "EchoesLocalSongAnalysis PASS job=$JobId source=$sourceSha timeline=$($timeline.summary.canonicalSha256) spans=$($timeline.summary.spanCount) userSong=$([bool]$DeclareUserSong)"
Write-Host "Timeline JSON: $timelineJsonPath"
Write-Host "Timeline CSV:  $timelineCsvPath"
Write-Host "Run manifest:  $analysisManifestPath"
