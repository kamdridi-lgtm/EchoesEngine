param(
    [string]$WorkspaceRoot = "D:\A.I\EchoesCinema",
    [int]$KeepProofArchives = 2,
    [double]$JobRetentionDays = 3,
    [int]$KeepNewestTerminalJobs = 3,
    [string]$PythonExecutable = "D:\A.I\EchoesCinema\.venv-cinema\Scripts\python.exe",
    [switch]$AfterRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($KeepProofArchives -lt 0) {
    throw "KeepProofArchives must be non-negative"
}
if ($JobRetentionDays -lt 0) {
    throw "JobRetentionDays must be non-negative"
}
if ($KeepNewestTerminalJobs -lt 0) {
    throw "KeepNewestTerminalJobs must be non-negative"
}

$workspace = [System.IO.Path]::GetFullPath($WorkspaceRoot)
$workspaceDrive = [System.IO.Path]::GetPathRoot($workspace)
if (-not $workspaceDrive -or $workspaceDrive.TrimEnd("\").ToUpperInvariant() -eq "C:") {
    throw "Cleanup is restricted to a non-C: Echoes Cinema workspace. Current path: $workspace"
}
if (-not (Test-Path -LiteralPath $workspace -PathType Container)) {
    return
}

function Get-DirectoryBytes {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) { return [int64]0 }
    $sum = (Get-ChildItem -LiteralPath $Path -File -Recurse -Force -ErrorAction SilentlyContinue |
        Measure-Object -Property Length -Sum).Sum
    return [int64]($sum ?? 0)
}

function Remove-SafeDirectoryContents {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) { return [int64]0 }
    $before = Get-DirectoryBytes -Path $Path
    Get-ChildItem -LiteralPath $Path -Force -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    return $before
}

function Remove-SafePath {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return [int64]0 }
    $bytes = if (Test-Path -LiteralPath $Path -PathType Container) {
        Get-DirectoryBytes -Path $Path
    } else {
        [int64](Get-Item -LiteralPath $Path -Force).Length
    }
    Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue
    return $bytes
}

$freed = [int64]0
$removed = New-Object System.Collections.Generic.List[string]
$janitorSummary = $null

# Always disposable: interrupted temporary files and bytecode caches.
foreach ($relative in @("temp", "cache\python-bytecode", "cache\cuda", "cache\numba")) {
    $path = Join-Path $workspace $relative
    $bytes = Remove-SafeDirectoryContents -Path $path
    if ($bytes -gt 0) {
        $freed += $bytes
        $removed.Add($relative)
    }
}

# Obsolete compiler/build folders from previous attempts.
foreach ($relative in @("build-first-real-cli", "build-first-real-proof", "build-temp")) {
    $path = Join-Path $workspace $relative
    $bytes = Remove-SafePath -Path $path
    if ($bytes -gt 0) {
        $freed += $bytes
        $removed.Add($relative)
    }
}

# Python installers are no longer required after Python is installed on D:.
$installerRoot = Join-Path $workspace "installers"
if (Test-Path -LiteralPath "D:\A.I\Python310\python.exe" -PathType Leaf) {
    $bytes = Remove-SafeDirectoryContents -Path $installerRoot
    if ($bytes -gt 0) {
        $freed += $bytes
        $removed.Add("installers")
    }
}

# Keep the virtual environment and Hugging Face model cache. Purge only pip's wheel/download cache after a run.
if ($AfterRun) {
    $pipCache = Join-Path $workspace "cache\pip"
    $bytes = Remove-SafeDirectoryContents -Path $pipCache
    if ($bytes -gt 0) {
        $freed += $bytes
        $removed.Add("cache\pip")
    }
}

# Keep the current proof. Retain only the newest N archived proof folders.
$archiveRoot = Join-Path $workspace "proofs\archive"
if (Test-Path -LiteralPath $archiveRoot -PathType Container) {
    $archives = @(Get-ChildItem -LiteralPath $archiveRoot -Directory -Force -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTimeUtc -Descending)
    if ($archives.Count -gt $KeepProofArchives) {
        foreach ($archive in $archives[$KeepProofArchives..($archives.Count - 1)]) {
            $bytes = Remove-SafePath -Path $archive.FullName
            if ($bytes -gt 0) {
                $freed += $bytes
                $removed.Add("proofs\archive\$($archive.Name)")
            }
        }
    }
}

# After a run, prune only disposable intermediates from old terminal jobs.
# Active, recoverable, pinned, newest, final MP4, JSON evidence, and logs are always preserved.
if ($AfterRun) {
    $jobsRoot = Join-Path $workspace "jobs"
    $ledgerPath = Join-Path $jobsRoot "_service\job-ledger.json"
    $janitorPath = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\tools\cinema_storage_janitor.py"))
    $janitorReportPath = Join-Path $jobsRoot "_service\storage-janitor-report.json"

    if ((Test-Path -LiteralPath $ledgerPath -PathType Leaf) -and
        (Test-Path -LiteralPath $janitorPath -PathType Leaf) -and
        (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
        $previousPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            & $PythonExecutable $janitorPath `
                --output-root $jobsRoot `
                --ledger $ledgerPath `
                --report $janitorReportPath `
                --minimum-age-days "$JobRetentionDays" `
                --keep-newest-terminal-jobs "$KeepNewestTerminalJobs"
            $janitorExitCode = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $previousPreference
        }

        if (Test-Path -LiteralPath $janitorReportPath -PathType Leaf) {
            $janitorSummary = Get-Content -LiteralPath $janitorReportPath -Raw | ConvertFrom-Json
            $janitorFreed = [int64]($janitorSummary.freedBytes ?? 0)
            $freed += $janitorFreed
            foreach ($entry in @($janitorSummary.removed)) {
                foreach ($target in @($entry.targets)) {
                    $removed.Add("jobs\$($entry.jobId)\$target")
                }
            }
        }
        if ($janitorExitCode -ne 0) {
            Write-Warning "Cinema job janitor reported a partial cleanup. See $janitorReportPath"
        }
    }
}

$driveName = $workspaceDrive.TrimEnd("\").TrimEnd(":")
$drive = Get-PSDrive -Name $driveName -ErrorAction Stop
$report = [ordered]@{
    schema = "echoes.cinema-cleanup-report.v2"
    timestampUtc = [DateTime]::UtcNow.ToString("o")
    workspace = $workspace
    mode = if ($AfterRun) { "after-run" } else { "before-run" }
    freedBytes = $freed
    freedGiB = [math]::Round($freed / 1GB, 3)
    freeGiBAfter = [math]::Round($drive.Free / 1GB, 2)
    removed = @($removed)
    jobJanitor = $janitorSummary
    preserved = @(
        ".venv-cinema",
        "cache\huggingface",
        "cache\torch",
        "proofs\first-real-ai-clip",
        "two newest proof archives",
        "jobs in QUEUED, RUNNING, or RECOVERABLE state",
        "pinned jobs and .keep jobs",
        "newest terminal jobs",
        "final MP4 files, JSON evidence, and logs"
    )
    status = if ($janitorSummary -and $janitorSummary.status -eq "PARTIAL") { "PARTIAL" } else { "PASS" }
}
$reportPath = Join-Path $workspace (if ($AfterRun) { "cleanup-after-run.json" } else { "cleanup-before-run.json" })
$report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $reportPath -Encoding utf8
Write-Host "Cinema cleanup $($report.mode): freed $($report.freedGiB) GiB; D: free $($report.freeGiBAfter) GiB"
