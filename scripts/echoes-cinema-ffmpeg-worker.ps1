param(
    [string]$WorkspaceRoot = "D:\A.I\EchoesCinema",
    [string]$RepoRoot = "",
    [int]$MaximumRetrySeconds = 300,
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

function Get-FailurePolicy {
    param([string]$Message)
    $text = ([string]$Message).ToLowerInvariant()
    foreach ($permanent in @(
        "sha-256 mismatch",
        "usable sha-256 digest",
        "does not contain ffmpeg.exe and ffprobe.exe",
        "does not report the pinned version",
        "failed validation after installation",
        "runtime lock is missing",
        "unsupported ffmpeg runtime lock schema",
        "storage must not use drive c:"
    )) {
        if ($text.Contains($permanent)) { return "BLOCKED" }
    }
    return "RETRY"
}

if ($SelfTest) {
    if ((Get-FailurePolicy -Message "Pinned FFmpeg archive SHA-256 mismatch") -ne "BLOCKED") { throw "Digest mismatch classification failed." }
    if ((Get-FailurePolicy -Message "GitHub did not provide a usable SHA-256 digest") -ne "BLOCKED") { throw "Missing digest classification failed." }
    if ((Get-FailurePolicy -Message "The remote name could not be resolved") -ne "RETRY") { throw "Transient network classification failed." }
    Write-Host "Echoes Cinema FFmpeg worker PASS permanent=blocked transient=retry dashboard=nonblocking"
    exit 0
}

if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
} else {
    $RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
}
$workspace = [System.IO.Path]::GetFullPath($WorkspaceRoot)
if ([System.IO.Path]::GetPathRoot($workspace).TrimEnd("\").ToUpperInvariant() -eq "C:") {
    throw "FFmpeg worker storage must not use drive C:."
}

$runtimeRoot = Join-Path $workspace "runtime"
$logsRoot = Join-Path $workspace "logs"
$statusPath = Join-Path $runtimeRoot "ffmpeg-worker-status.json"
$pidPath = Join-Path $runtimeRoot "ffmpeg-worker.pid"
$stopSignalPath = Join-Path $runtimeRoot "stop.signal"
$ensureScript = Join-Path $RepoRoot "scripts\ensure-ffmpeg-on-d.ps1"
$lockPath = Join-Path $RepoRoot "providers\ffmpeg-runtime-lock.json"
$expectedBin = Join-Path $workspace "tools\ffmpeg\bin"
foreach ($directory in @($workspace, $runtimeRoot, $logsRoot)) { New-Item -ItemType Directory -Path $directory -Force | Out-Null }
Set-Content -LiteralPath $pidPath -Value $PID -Encoding ascii

$attempt = 0
$retrySeconds = 15
try {
    while (-not (Test-Path -LiteralPath $stopSignalPath)) {
        $attempt++
        $attemptUtc = [DateTime]::UtcNow
        Write-AtomicJson -Path $statusPath -Payload @{
            schema = "echoes.ffmpeg-worker.v1"
            status = "PREPARING"
            timestampUtc = $attemptUtc.ToString("o")
            attempt = $attempt
            retryable = $true
            automaticRetry = $true
            installRoot = (Join-Path $workspace "tools\ffmpeg")
            binPath = $expectedBin
            systemDriveWritesAllowed = $false
        }

        try {
            if (-not (Test-Path -LiteralPath $ensureScript -PathType Leaf)) { throw "FFmpeg provisioner is missing: $ensureScript" }
            if (-not (Test-Path -LiteralPath $lockPath -PathType Leaf)) { throw "FFmpeg runtime lock is missing: $lockPath" }
            $raw = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ensureScript -WorkspaceRoot $workspace -LockPath $lockPath
            if ($LASTEXITCODE -ne 0) { throw "Pinned FFmpeg provisioner exited with code $LASTEXITCODE" }
            $bin = [string]($raw | Select-Object -Last 1)
            $ffmpeg = Join-Path $bin "ffmpeg.exe"
            $ffprobe = Join-Path $bin "ffprobe.exe"
            if (-not (Test-Path -LiteralPath $ffmpeg -PathType Leaf) -or -not (Test-Path -LiteralPath $ffprobe -PathType Leaf)) {
                throw "Pinned FFmpeg provisioning did not return ffmpeg.exe and ffprobe.exe: $bin"
            }
            $env:PATH = "$bin;$env:PATH"
            Write-AtomicJson -Path $statusPath -Payload @{
                schema = "echoes.ffmpeg-worker.v1"
                status = "PASS"
                timestampUtc = [DateTime]::UtcNow.ToString("o")
                attempt = $attempt
                retryable = $false
                automaticRetry = $false
                binPath = $bin
                ffmpegPath = $ffmpeg
                ffprobePath = $ffprobe
                operatorAction = "No action is required. The pinned media runtime is ready."
                systemDriveWritesAllowed = $false
            }
            exit 0
        } catch {
            $message = $_.Exception.Message
            $policy = Get-FailurePolicy -Message $message
            if ($policy -eq "BLOCKED") {
                Write-AtomicJson -Path $statusPath -Payload @{
                    schema = "echoes.ffmpeg-worker.v1"
                    status = "BLOCKED"
                    timestampUtc = [DateTime]::UtcNow.ToString("o")
                    attempt = $attempt
                    error = $message
                    failureClass = "FFMPEG_RUNTIME_INTEGRITY_BLOCKER"
                    retryable = $false
                    automaticRetry = $false
                    operatorAction = "The FFmpeg package failed an integrity or version check. The dashboard stays online; inspect this exact blocker before retrying."
                    binPath = $expectedBin
                    systemDriveWritesAllowed = $false
                }
                while (-not (Test-Path -LiteralPath $stopSignalPath)) { Start-Sleep -Seconds 5 }
                exit 2
            }

            $nextRetry = [DateTime]::UtcNow.AddSeconds($retrySeconds)
            Write-AtomicJson -Path $statusPath -Payload @{
                schema = "echoes.ffmpeg-worker.v1"
                status = "RETRY_WAIT"
                timestampUtc = [DateTime]::UtcNow.ToString("o")
                attempt = $attempt
                error = $message
                failureClass = "FFMPEG_RUNTIME_TRANSIENT"
                retryable = $true
                automaticRetry = $true
                operatorAction = "No action is required. The dashboard remains online and FFmpeg provisioning will retry automatically."
                nextRetryUtc = $nextRetry.ToString("o")
                retrySeconds = $retrySeconds
                binPath = $expectedBin
                systemDriveWritesAllowed = $false
            }
            $remaining = [math]::Min($retrySeconds, [math]::Max(1, $MaximumRetrySeconds))
            while ($remaining -gt 0 -and -not (Test-Path -LiteralPath $stopSignalPath)) {
                $slice = [math]::Min(2, $remaining)
                Start-Sleep -Seconds $slice
                $remaining -= $slice
            }
            $retrySeconds = [math]::Min([math]::Max(15, $MaximumRetrySeconds), [math]::Max(15, $retrySeconds * 2))
        }
    }
} finally {
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
}
