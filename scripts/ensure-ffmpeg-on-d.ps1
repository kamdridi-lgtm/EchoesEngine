param(
    [string]$WorkspaceRoot = "D:\A.I\EchoesCinema",
    [string]$LockPath = "",
    [switch]$MetadataOnly,
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-AtomicJson {
    param([string]$Path, [hashtable]$Payload)
    $directory = Split-Path -Parent $Path
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $temporary = "$Path.$PID.tmp"
    $Payload | ConvertTo-Json -Depth 16 | Set-Content -LiteralPath $temporary -Encoding utf8
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

function Read-JsonFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    try { return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json } catch { return $null }
}

function Read-FfmpegLock {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "FFmpeg runtime lock is missing: $Path" }
    $lock = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    if ([string]$lock.schema -ne "echoes.ffmpeg-runtime-lock.v1") { throw "Unsupported FFmpeg runtime lock schema." }
    foreach ($name in @("repository", "releaseTag", "assetName", "ffmpegVersion", "platform", "installRelativePath")) {
        if (-not [string]$lock.$name) { throw "FFmpeg runtime lock is missing: $name" }
    }
    if ([string]$lock.repository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') { throw "FFmpeg lock repository is invalid." }
    if ([string]$lock.releaseTag -notmatch '^\d+\.\d+(\.\d+)?$') { throw "FFmpeg release tag must be an exact version." }
    if ([string]$lock.assetName -notmatch '\.zip$') { throw "FFmpeg asset must be a ZIP archive." }
    if ([string]$lock.platform -ne "windows-x64") { throw "Only the pinned Windows x64 FFmpeg runtime is supported." }
    return $lock
}

function Get-GitHubHeaders {
    $headers = @{
        "Accept" = "application/vnd.github+json"
        "User-Agent" = "Echoes-Cinema-FFmpeg-Provisioner"
        "X-GitHub-Api-Version" = "2022-11-28"
    }
    if ($env:GITHUB_TOKEN) { $headers["Authorization"] = "Bearer $($env:GITHUB_TOKEN)" }
    return $headers
}

function Get-PinnedReleaseAsset {
    param([object]$Lock)
    $apiUrl = "https://api.github.com/repos/$($Lock.repository)/releases/tags/$($Lock.releaseTag)"
    $release = Invoke-RestMethod -Uri $apiUrl -Headers (Get-GitHubHeaders) -TimeoutSec 30
    $asset = @($release.assets) | Where-Object { [string]$_.name -eq [string]$Lock.assetName } | Select-Object -First 1
    if (-not $asset) { throw "Pinned FFmpeg asset was not found in release $($Lock.releaseTag): $($Lock.assetName)" }
    $digest = [string]$asset.digest
    if ($digest -notmatch '^sha256:([0-9a-fA-F]{64})$') {
        throw "GitHub did not provide a usable SHA-256 digest for the pinned FFmpeg asset. Installation stopped safely."
    }
    return [pscustomobject]@{
        apiUrl = $apiUrl
        releaseUrl = [string]$release.html_url
        releaseId = [long]$release.id
        assetId = [long]$asset.id
        assetName = [string]$asset.name
        assetSizeBytes = [long]$asset.size
        browserDownloadUrl = [string]$asset.browser_download_url
        digest = $digest.ToLowerInvariant()
        sha256 = $Matches[1].ToLowerInvariant()
        updatedAt = [string]$asset.updated_at
    }
}

function Get-BinaryVersionLine {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    try { return [string](& $Path -version 2>&1 | Select-Object -First 1) } catch { return $null }
}

function Test-InstalledRuntime {
    param(
        [string]$BinPath,
        [object]$Lock
    )
    $ffmpeg = Join-Path $BinPath "ffmpeg.exe"
    $ffprobe = Join-Path $BinPath "ffprobe.exe"
    $ffmpegVersion = Get-BinaryVersionLine -Path $ffmpeg
    $ffprobeVersion = Get-BinaryVersionLine -Path $ffprobe
    $expected = "version $($Lock.ffmpegVersion)"
    return [pscustomobject]@{
        healthy = [bool]($ffmpegVersion -and $ffprobeVersion -and $ffmpegVersion.Contains($expected) -and $ffprobeVersion.Contains($expected))
        ffmpegPath = $ffmpeg
        ffprobePath = $ffprobe
        ffmpegVersionLine = $ffmpegVersion
        ffprobeVersionLine = $ffprobeVersion
    }
}

if (-not $LockPath) {
    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    $LockPath = Join-Path $repoRoot "providers\ffmpeg-runtime-lock.json"
}
$lock = Read-FfmpegLock -Path $LockPath

if ($SelfTest) {
    if ([string]$lock.repository -ne "GyanD/codexffmpeg") { throw "Unexpected FFmpeg source repository." }
    if ([string]$lock.releaseTag -ne [string]$lock.ffmpegVersion) { throw "FFmpeg release and version must match." }
    if (-not ([string]$lock.assetName).Contains([string]$lock.ffmpegVersion)) { throw "FFmpeg asset name must include the pinned version." }
    if ([string]$lock.installRelativePath -ne "tools/ffmpeg") { throw "FFmpeg install path must remain deterministic." }
    Write-Host "Echoes FFmpeg lock PASS source=GitHub release=exact digest=required platform=windows-x64"
    exit 0
}

if ($MetadataOnly) {
    $asset = Get-PinnedReleaseAsset -Lock $lock
    @{
        schema = "echoes.ffmpeg-release-metadata.v1"
        status = "PASS"
        repository = $lock.repository
        releaseTag = $lock.releaseTag
        version = $lock.ffmpegVersion
        asset = $asset
        digestVerifiedBeforeInstall = $true
    } | ConvertTo-Json -Depth 10
    exit 0
}

$workspace = [System.IO.Path]::GetFullPath($WorkspaceRoot)
$workspaceDrive = [System.IO.Path]::GetPathRoot($workspace)
if (-not $workspaceDrive -or $workspaceDrive.TrimEnd("\").ToUpperInvariant() -eq "C:") {
    throw "FFmpeg runtime storage must not use drive C:. Current path: $workspace"
}

$installRoot = Join-Path $workspace ([string]$lock.installRelativePath).Replace("/", "\")
$binPath = Join-Path $installRoot "bin"
$runtimeRoot = Join-Path $workspace "runtime"
$tempRoot = Join-Path $workspace "temp\ffmpeg-provision"
$backupRoot = Join-Path $workspace "backups\ffmpeg"
$evidencePath = Join-Path $runtimeRoot "ffmpeg-runtime.json"
foreach ($directory in @($runtimeRoot, $tempRoot, $backupRoot)) { New-Item -ItemType Directory -Path $directory -Force | Out-Null }

$current = Test-InstalledRuntime -BinPath $binPath -Lock $lock
if ($current.healthy) {
    $previousEvidence = Read-JsonFile -Path $evidencePath
    Write-AtomicJson -Path $evidencePath -Payload @{
        schema = "echoes.ffmpeg-runtime.v1"
        status = "PASS"
        timestampUtc = [DateTime]::UtcNow.ToString("o")
        sourceRepository = $lock.repository
        releaseTag = $lock.releaseTag
        assetName = $lock.assetName
        expectedDigest = if ($previousEvidence -and $previousEvidence.expectedDigest) { [string]$previousEvidence.expectedDigest } else { $null }
        downloadedSha256 = if ($previousEvidence -and $previousEvidence.downloadedSha256) { [string]$previousEvidence.downloadedSha256 } else { $null }
        installRoot = $installRoot
        binPath = $binPath
        ffmpegPath = $current.ffmpegPath
        ffprobePath = $current.ffprobePath
        ffmpegVersionLine = $current.ffmpegVersionLine
        ffprobeVersionLine = $current.ffprobeVersionLine
        reusedExistingInstall = $true
        networkMetadataRequested = $false
        systemDriveWritesAllowed = $false
    }
    Write-Output $binPath
    exit 0
}

$asset = Get-PinnedReleaseAsset -Lock $lock
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$archivePath = Join-Path $tempRoot "$stamp-$($lock.assetName)"
$extractPath = Join-Path $tempRoot "extract-$stamp"
$stagedInstall = Join-Path $tempRoot "install-$stamp"
New-Item -ItemType Directory -Path $extractPath, (Join-Path $stagedInstall "bin") -Force | Out-Null

try {
    Invoke-WebRequest -Uri $asset.browserDownloadUrl -Headers (Get-GitHubHeaders) -OutFile $archivePath -TimeoutSec 600
    $actualHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $asset.sha256) {
        throw "Pinned FFmpeg archive SHA-256 mismatch. Expected $($asset.sha256), received $actualHash."
    }
    Expand-Archive -LiteralPath $archivePath -DestinationPath $extractPath -Force
    $ffmpegSource = Get-ChildItem -LiteralPath $extractPath -Filter "ffmpeg.exe" -File -Recurse | Select-Object -First 1
    $ffprobeSource = Get-ChildItem -LiteralPath $extractPath -Filter "ffprobe.exe" -File -Recurse | Select-Object -First 1
    if (-not $ffmpegSource -or -not $ffprobeSource) { throw "Pinned FFmpeg archive does not contain ffmpeg.exe and ffprobe.exe." }
    if ($ffmpegSource.DirectoryName -ne $ffprobeSource.DirectoryName) { throw "FFmpeg and FFprobe were found in different archive directories." }
    Get-ChildItem -LiteralPath $ffmpegSource.DirectoryName -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $stagedInstall "bin") -Force -Recurse
    }
    $staged = Test-InstalledRuntime -BinPath (Join-Path $stagedInstall "bin") -Lock $lock
    if (-not $staged.healthy) {
        throw "Extracted FFmpeg runtime does not report the pinned version. FFmpeg=$($staged.ffmpegVersionLine) FFprobe=$($staged.ffprobeVersionLine)"
    }

    if (Test-Path -LiteralPath $installRoot) {
        $backup = Join-Path $backupRoot $stamp
        Move-Item -LiteralPath $installRoot -Destination $backup -Force
    }
    Move-Item -LiteralPath $stagedInstall -Destination $installRoot -Force
    $installed = Test-InstalledRuntime -BinPath $binPath -Lock $lock
    if (-not $installed.healthy) { throw "FFmpeg runtime failed validation after installation." }

    Write-AtomicJson -Path $evidencePath -Payload @{
        schema = "echoes.ffmpeg-runtime.v1"
        status = "PASS"
        timestampUtc = [DateTime]::UtcNow.ToString("o")
        sourceRepository = $lock.repository
        releaseTag = $lock.releaseTag
        releaseUrl = $asset.releaseUrl
        assetId = $asset.assetId
        assetName = $asset.assetName
        assetSizeBytes = $asset.assetSizeBytes
        expectedDigest = $asset.digest
        downloadedSha256 = $actualHash
        installRoot = $installRoot
        binPath = $binPath
        ffmpegPath = $installed.ffmpegPath
        ffprobePath = $installed.ffprobePath
        ffmpegVersionLine = $installed.ffmpegVersionLine
        ffprobeVersionLine = $installed.ffprobeVersionLine
        reusedExistingInstall = $false
        networkMetadataRequested = $true
        systemDriveWritesAllowed = $false
    }
    Write-Output $binPath
} finally {
    Remove-Item -LiteralPath $archivePath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $extractPath -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $stagedInstall -Recurse -Force -ErrorAction SilentlyContinue
}
