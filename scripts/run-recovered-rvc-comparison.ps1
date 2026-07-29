[CmdletBinding()]
param(
    [string]$ModelRoot = "D:\A.I\EchoesRvcRecovered\model_2",
    [string]$OutputRoot = "D:\A.I\EchoesRvcRecovered\comparison_output",
    [string]$InputPath,
    [string]$ApplioRoot,
    [string]$PythonExecutable,
    [string]$CorePath,
    [switch]$AllowNonDDrive,
    [switch]$NoOpen
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-Sha256([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    try {
        $algorithm = [Security.Cryptography.SHA256]::Create()
        try {
            $bytes = $algorithm.ComputeHash($stream)
            return ([BitConverter]::ToString($bytes)).Replace("-", "").ToLowerInvariant()
        }
        finally {
            $algorithm.Dispose()
        }
    }
    finally {
        $stream.Dispose()
    }
}

function Invoke-Applio([string]$Python, [string]$Core, [string[]]$Arguments, [string]$LogPath) {
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & $Python $Core @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    @($output) | ForEach-Object { [string]$_ } | Set-Content -LiteralPath $LogPath -Encoding utf8
    return $exitCode
}

function Resolve-ApplioRuntime([string]$RequestedRoot, [string]$RequestedPython, [string]$RequestedCore, [string]$ControlDirectory) {
    $roots = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrWhiteSpace($RequestedRoot)) {
        $roots.Add([IO.Path]::GetFullPath($RequestedRoot))
    }
    foreach ($candidate in @(
        "D:\RECOVERED_FROM_C\Applio",
        "D:\A.I\STORAGE_FROM_C\Models\Applio",
        "D:\A.I\Applio",
        "D:\Applio",
        "C:\Applio"
    )) {
        if (-not $roots.Contains($candidate)) {
            $roots.Add($candidate)
        }
    }

    $coreCandidates = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrWhiteSpace($RequestedCore)) {
        $coreCandidates.Add([IO.Path]::GetFullPath($RequestedCore))
    }
    foreach ($root in $roots) {
        $candidate = Join-Path $root "core.py"
        if (-not $coreCandidates.Contains($candidate)) {
            $coreCandidates.Add($candidate)
        }
    }

    foreach ($core in $coreCandidates) {
        if (-not (Test-Path -LiteralPath $core -PathType Leaf)) {
            continue
        }
        $root = Split-Path -Parent $core
        $pythonCandidates = New-Object System.Collections.Generic.List[string]
        if (-not [string]::IsNullOrWhiteSpace($RequestedPython)) {
            $pythonCandidates.Add([IO.Path]::GetFullPath($RequestedPython))
        }
        foreach ($candidate in @(
            (Join-Path $root "env\python.exe"),
            (Join-Path $root "env\Scripts\python.exe"),
            (Join-Path $root ".venv\Scripts\python.exe"),
            (Join-Path $root "venv\Scripts\python.exe"),
            (Join-Path $root "runtime\python.exe")
        )) {
            if (-not $pythonCandidates.Contains($candidate)) {
                $pythonCandidates.Add($candidate)
            }
        }

        foreach ($python in $pythonCandidates) {
            if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
                continue
            }
            $probeLog = Join-Path $ControlDirectory "applio-infer-help.log"
            $probeCode = Invoke-Applio $python $core @("infer", "--help") $probeLog
            if ($probeCode -eq 0) {
                return [pscustomobject]@{
                    Root = $root
                    Python = $python
                    Core = $core
                    ProbeLog = $probeLog
                }
            }
        }
    }
    throw "No targeted Applio runtime could execute core.py infer --help"
}

if ($env:OS -ne "Windows_NT") {
    throw "Recovered RVC comparison currently supports Windows only"
}

$modelDirectory = [IO.Path]::GetFullPath($ModelRoot)
$outputDirectory = [IO.Path]::GetFullPath($OutputRoot)
if (-not $AllowNonDDrive) {
    if (-not $modelDirectory.StartsWith("D:\", [StringComparison]::OrdinalIgnoreCase)) {
        throw "Recovered RVC models must remain on D:"
    }
    if (-not $outputDirectory.StartsWith("D:\", [StringComparison]::OrdinalIgnoreCase)) {
        throw "Recovered RVC outputs must remain on D:"
    }
}

$models = @(
    [pscustomobject]@{ Label = "700"; File = "model_2_700e_63700s.pth" },
    [pscustomobject]@{ Label = "1000"; File = "model_2_1000e_91000s.pth" },
    [pscustomobject]@{ Label = "1500"; File = "model_2_1500e_136500s.pth" }
)
$indexPath = Join-Path $modelDirectory "model_2.index"
foreach ($model in $models) {
    $model | Add-Member -NotePropertyName Path -NotePropertyValue (Join-Path $modelDirectory $model.File)
}

foreach ($required in @($indexPath) + @($models | ForEach-Object { $_.Path })) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required recovered RVC file is missing: $required"
    }
    if ((Get-Item -LiteralPath $required).Length -le 0) {
        throw "Required recovered RVC file is empty: $required"
    }
}

New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
$controlDirectory = Join-Path $outputDirectory "control"
New-Item -ItemType Directory -Force -Path $controlDirectory | Out-Null

if ([string]::IsNullOrWhiteSpace($InputPath)) {
    Add-Type -AssemblyName System.Windows.Forms
    $dialog = New-Object System.Windows.Forms.OpenFileDialog
    $dialog.Title = "Choose one VOCALS-ONLY file for RVC 700 / 1000 / 1500"
    $dialog.Filter = "Audio (*.wav;*.flac;*.mp3;*.m4a;*.aiff;*.aif)|*.wav;*.flac;*.mp3;*.m4a;*.aiff;*.aif|All files (*.*)|*.*"
    $dialog.Multiselect = $false
    if ($dialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) {
        throw "No vocals-only input was selected"
    }
    $InputPath = $dialog.FileName
}

$input = [IO.Path]::GetFullPath($InputPath)
if (-not (Test-Path -LiteralPath $input -PathType Leaf)) {
    throw "Selected vocal input is missing: $input"
}
if ((Get-Item -LiteralPath $input).Length -le 0) {
    throw "Selected vocal input is empty: $input"
}

$runtime = Resolve-ApplioRuntime $ApplioRoot $PythonExecutable $CorePath $controlDirectory
$inputHash = Get-Sha256 $input
$indexHash = Get-Sha256 $indexPath
$baseName = [IO.Path]::GetFileNameWithoutExtension($input)
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$results = New-Object System.Collections.Generic.List[object]

foreach ($model in $models) {
    $outputPath = Join-Path $outputDirectory ("{0}_RVC_{1}E.wav" -f $baseName, $model.Label)
    if (Test-Path -LiteralPath $outputPath -PathType Leaf) {
        $outputPath = Join-Path $outputDirectory ("{0}_RVC_{1}E_{2}.wav" -f $baseName, $model.Label, $timestamp)
    }
    $runLog = Join-Path $controlDirectory ("convert-{0}.log" -f $model.Label)
    $arguments = @(
        "infer",
        "--input_path", $input,
        "--output_path", $outputPath,
        "--pth_path", $model.Path,
        "--index_path", $indexPath
    )
    $exitCode = Invoke-Applio $runtime.Python $runtime.Core $arguments $runLog
    if ($exitCode -ne 0) {
        throw "RVC comparison conversion $($model.Label) failed with exit code $exitCode. See $runLog"
    }
    if (-not (Test-Path -LiteralPath $outputPath -PathType Leaf)) {
        throw "RVC comparison conversion $($model.Label) did not create its WAV"
    }
    $outputItem = Get-Item -LiteralPath $outputPath
    if ($outputItem.Length -le 44) {
        throw "RVC comparison output $($model.Label) is too small to be a valid WAV"
    }
    $results.Add([pscustomobject]@{
        label = $model.Label
        modelPath = $model.Path
        modelSha256 = Get-Sha256 $model.Path
        indexPath = $indexPath
        indexSha256 = $indexHash
        inputPath = $input
        inputSha256 = $inputHash
        outputPath = $outputPath
        outputSha256 = Get-Sha256 $outputPath
        outputSizeBytes = $outputItem.Length
        exitCode = $exitCode
        logPath = $runLog
        status = "PASS"
    })
}

$playlistPath = Join-Path $outputDirectory "ECOUTER-700-1000-1500.m3u8"
@("#EXTM3U") + @($results | ForEach-Object { $_.outputPath }) | Set-Content -LiteralPath $playlistPath -Encoding utf8

$report = [ordered]@{
    schema = "echoes.recovered-rvc-comparison-run.v1"
    status = "PASS"
    completedAtUtc = [DateTime]::UtcNow.ToString("o")
    runtime = [ordered]@{
        root = $runtime.Root
        python = $runtime.Python
        core = $runtime.Core
        probeLog = $runtime.ProbeLog
    }
    fixedParameters = [ordered]@{
        sameInput = $true
        sameIndex = $true
        pitchShiftSemitones = 0
        effectsApplied = $false
        masteringApplied = $false
        instrumentalMixed = $false
    }
    runs = @($results)
    playlistPath = $playlistPath
    truthBoundary = [ordered]@{
        localApplioRuntimeExecuted = $true
        threeConversionsExecuted = $true
        threeOutputFilesVerified = $true
        bestModelSelected = $false
        sourceAudioDeleted = $false
        audioUploaded = $false
    }
}
$reportPath = Join-Path $controlDirectory "RECOVERED-RVC-COMPARISON-REPORT.json"
$report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportPath -Encoding utf8

Write-Host "RVC COMPARISON COMPLETE" -ForegroundColor Green
foreach ($result in $results) {
    Write-Host ("{0}: {1}" -f $result.label, $result.outputPath)
}
Write-Host ("Report: {0}" -f $reportPath)
Write-Host ("Playlist: {0}" -f $playlistPath)

if (-not $NoOpen) {
    Start-Process explorer.exe -ArgumentList $outputDirectory
}
exit 0
