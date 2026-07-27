[CmdletBinding()]
param(
    [string]$SourceRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$RvcRuntimeRoot = "D:\A.I\EchoesRvcRuntime",
    [string]$PythonExecutable,
    [string]$GitExecutable,
    [switch]$SkipDependencyInstall,
    [switch]$ForceCpu,
    [switch]$AllowNonDDrive,
    [switch]$NoOpen
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$PinnedRepository = "https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI.git"
$PinnedRepositoryName = "RVC-Project/Retrieval-based-Voice-Conversion-WebUI"
$PinnedCommit = "4338f12c3c28c80b3ac015e2d0df66c41592746d"
$RequiredFiles = @(
    "README.md",
    "LICENSE",
    "webui.py",
    "requirments_cu118_py312.txt",
    "requirments_cpu_py312.txt"
)

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Write-JsonAtomic([string]$Path, [object]$Value) {
    $parent = Split-Path -Parent $Path
    if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    $temporary = "$Path.tmp-$PID"
    $Value | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $temporary -Encoding utf8
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

function Invoke-External([string]$Executable, [string[]]$Arguments, [string]$Label) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

function Resolve-Python312([string]$Explicit) {
    $candidates = @()
    if (-not [string]::IsNullOrWhiteSpace($Explicit)) {
        $candidates += [IO.Path]::GetFullPath($Explicit)
    }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($null -ne $py) {
        $resolved = & $py.Source -3.12 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($resolved)) {
            $candidates += $resolved.Trim()
        }
    }
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($null -ne $pythonCommand) { $candidates += $pythonCommand.Source }

    foreach ($candidate in $candidates | Select-Object -Unique) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        $version = & $candidate -c "import sys; print('.'.join(map(str, sys.version_info[:3])))" 2>$null
        if ($LASTEXITCODE -eq 0 -and $version -match '^3\.12\.') {
            return [IO.Path]::GetFullPath($candidate)
        }
    }
    throw "Python 3.12 x64 is required for the pinned RVC runtime"
}

function Resolve-Git([string]$Explicit) {
    if (-not [string]::IsNullOrWhiteSpace($Explicit)) {
        $full = [IO.Path]::GetFullPath($Explicit)
        if (Test-Path -LiteralPath $full -PathType Leaf) { return $full }
    }
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($null -eq $git) { throw "Git is required to provision the pinned RVC source" }
    return $git.Source
}

if ($env:OS -ne "Windows_NT") {
    throw "Echoes RVC foundation installer currently supports Windows only"
}

$runtime = [IO.Path]::GetFullPath($RvcRuntimeRoot)
if (-not $AllowNonDDrive -and -not $runtime.StartsWith("D:\", [StringComparison]::OrdinalIgnoreCase)) {
    throw "Production RVC runtime must remain on D:"
}

$source = Join-Path $runtime "source"
$venv = Join-Path $runtime ".venv"
$control = Join-Path $runtime "control"
$manifestPath = Join-Path $runtime "rvc-runtime-manifest.json"
$python = Resolve-Python312 $PythonExecutable
$git = Resolve-Git $GitExecutable

New-Item -ItemType Directory -Force -Path $runtime,$control | Out-Null

if (-not (Test-Path -LiteralPath (Join-Path $source ".git") -PathType Container)) {
    if (Test-Path -LiteralPath $source) {
        $nonEmpty = @(Get-ChildItem -LiteralPath $source -Force -ErrorAction SilentlyContinue).Count -gt 0
        if ($nonEmpty) { throw "RVC managed source directory exists but is not a Git checkout: $source" }
    }
    Invoke-External $git @("clone", "--filter=blob:none", "--no-checkout", $PinnedRepository, $source) "Pinned RVC clone"
} else {
    Invoke-External $git @("-C", $source, "remote", "set-url", "origin", $PinnedRepository) "RVC remote normalization"
}
Invoke-External $git @("-C", $source, "fetch", "--depth", "1", "origin", $PinnedCommit) "Pinned RVC fetch"
Invoke-External $git @("-C", $source, "checkout", "--detach", "--force", $PinnedCommit) "Pinned RVC checkout"
$head = (& $git -C $source rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $head -ne $PinnedCommit) {
    throw "Pinned RVC checkout mismatch expected=$PinnedCommit actual=$head"
}

$installedFiles = @()
foreach ($relative in $RequiredFiles) {
    $path = Join-Path $source $relative
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Pinned RVC required file is missing: $relative"
    }
    $installedFiles += [ordered]@{
        relativePath = $relative
        path = [IO.Path]::GetFullPath($path)
        sha256 = Get-Sha256 $path
        sizeBytes = (Get-Item -LiteralPath $path).Length
    }
}

$licenseText = Get-Content -LiteralPath (Join-Path $source "LICENSE") -Raw
if (-not $licenseText.Contains("MIT License")) {
    throw "Pinned RVC checkout does not expose the expected MIT licence"
}

$venvPython = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    Invoke-External $python @("-m", "venv", $venv) "RVC Python 3.12 virtual environment creation"
}
$venvVersion = (& $venvPython -c "import sys; print('.'.join(map(str, sys.version_info[:3])))").Trim()
if ($LASTEXITCODE -ne 0 -or $venvVersion -notmatch '^3\.12\.') {
    throw "RVC virtual environment is not Python 3.12: $venvVersion"
}

$provider = "uninstalled"
$torchInfo = [ordered]@{
    version = $null
    cudaAvailable = $false
    cudaVersion = $null
}
$dependencyStatus = "SKIPPED"
$dependencyLog = Join-Path $control "dependency-install.log"

if (-not $SkipDependencyInstall) {
    $nvidia = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    $useCuda = (-not $ForceCpu) -and ($null -ne $nvidia)
    $provider = if ($useCuda) { "cuda" } else { "cpu" }
    $requirementsSource = Join-Path $source (if ($useCuda) { "requirments_cu118_py312.txt" } else { "requirments_cpu_py312.txt" })
    $requirementsGenerated = Join-Path $control (if ($useCuda) { "requirements-echoes-cu118.txt" } else { "requirements-echoes-cpu.txt" })
    $requirements = Get-Content -LiteralPath $requirementsSource -Raw
    $requirements = $requirements.Replace("https://mirrors.pku.edu.cn/pypi/simple", "https://pypi.org/simple")
    $requirements = $requirements.Replace("https://mirrors.nju.edu.cn/pytorch/whl/cu118", "https://download.pytorch.org/whl/cu118")
    $requirements = $requirements.Replace("https://mirrors.nju.edu.cn/pytorch/whl/cpu", "https://download.pytorch.org/whl/cpu")
    $requirements | Set-Content -LiteralPath $requirementsGenerated -Encoding utf8

    $allOutput = New-Object System.Collections.Generic.List[string]
    $commands = @(
        @("-m", "pip", "install", "--upgrade", "pip", "setuptools<81", "wheel")
    )
    if ($useCuda) {
        $commands += ,@("-m", "pip", "install", "torch==2.7.1+cu118", "torchaudio==2.7.1+cu118", "--index-url", "https://download.pytorch.org/whl/cu118", "--extra-index-url", "https://pypi.org/simple")
    } else {
        $commands += ,@("-m", "pip", "install", "torch==2.7.1+cpu", "torchaudio==2.7.1+cpu", "--index-url", "https://download.pytorch.org/whl/cpu", "--extra-index-url", "https://pypi.org/simple")
    }
    $commands += ,@("-m", "pip", "install", "-r", $requirementsGenerated)

    foreach ($arguments in $commands) {
        $output = & $venvPython @arguments 2>&1
        foreach ($line in $output) { $allOutput.Add([string]$line) }
        if ($LASTEXITCODE -ne 0) {
            $allOutput | Set-Content -LiteralPath $dependencyLog -Encoding utf8
            throw "RVC dependency installation failed; see $dependencyLog"
        }
    }
    $allOutput | Set-Content -LiteralPath $dependencyLog -Encoding utf8

    $torchJson = & $venvPython -c "import json,torch,torchaudio; print(json.dumps({'torch':torch.__version__,'torchaudio':torchaudio.__version__,'cudaAvailable':torch.cuda.is_available(),'cudaVersion':torch.version.cuda}))"
    if ($LASTEXITCODE -ne 0) { throw "Installed RVC Torch/Torchaudio import proof failed" }
    $parsedTorch = $torchJson | ConvertFrom-Json
    if (-not ([string]$parsedTorch.torch).StartsWith("2.7.1")) { throw "Installed Torch version drifted: $($parsedTorch.torch)" }
    if ($useCuda -and ($parsedTorch.cudaAvailable -ne $true -or -not ([string]$parsedTorch.cudaVersion).StartsWith("11.8"))) {
        throw "CUDA 11.8 Torch was installed but CUDA execution is unavailable"
    }
    $torchInfo = [ordered]@{
        version = [string]$parsedTorch.torch
        torchaudioVersion = [string]$parsedTorch.torchaudio
        cudaAvailable = [bool]$parsedTorch.cudaAvailable
        cudaVersion = if ($null -eq $parsedTorch.cudaVersion) { $null } else { [string]$parsedTorch.cudaVersion }
    }
    $dependencyStatus = "PASS"
}

$launcherPath = Join-Path $runtime "Open-Echoes-Rvc.ps1"
$launcher = @'
[CmdletBinding()]
param([switch]$NoAutoOpen)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$runtime = Split-Path -Parent $MyInvocation.MyCommand.Path
$manifestPath = Join-Path $runtime "rvc-runtime-manifest.json"
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw "RVC runtime manifest is missing" }
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.status -ne "PASS") { throw "RVC dependencies are not fully installed; runtime status is $($manifest.status)" }
$python = Join-Path $runtime ".venv\Scripts\python.exe"
$source = Join-Path $runtime "source"
$arguments = @((Join-Path $source "webui.py"))
if ($NoAutoOpen) { $arguments += "--noautoopen" }
Push-Location $source
try { & $python @arguments } finally { Pop-Location }
exit $LASTEXITCODE
'@
$launcher | Set-Content -LiteralPath $launcherPath -Encoding utf8

$status = if ($dependencyStatus -eq "PASS") { "PASS" } else { "PARTIAL" }
$manifest = [ordered]@{
    schema = "echoes.rvc-runtime-installation.v1"
    version = "1.0.0"
    status = $status
    installedAtUtc = [DateTime]::UtcNow.ToString("o")
    installRoot = $runtime
    sourceCheckout = [ordered]@{
        root = $source
        head = $head
        detached = $true
    }
    upstream = [ordered]@{
        repository = $PinnedRepositoryName
        url = $PinnedRepository
        commit = $PinnedCommit
        license = "MIT"
    }
    python = [ordered]@{
        version = $venvVersion
        executable = $venvPython
        isolatedVirtualEnvironment = $true
    }
    torch = $torchInfo
    provider = $provider
    cpuFallback = $true
    dependencies = [ordered]@{
        status = $dependencyStatus
        skipped = [bool]$SkipDependencyInstall
        log = if (Test-Path -LiteralPath $dependencyLog -PathType Leaf) { $dependencyLog } else { $null }
    }
    installedFiles = $installedFiles
    launcher = $launcherPath
    truthBoundary = [ordered]@{
        sourceCheckoutVerified = $true
        pinnedCommitVerified = $true
        requiredSourceHashesRecorded = $true
        pythonRuntimeVerified = $true
        torchImportVerified = $dependencyStatus -eq "PASS"
        productionDependenciesInstalled = $dependencyStatus -eq "PASS"
        hpOmenRuntimeInstalled = $false
        kamDridiVoiceModelVerified = $false
        cudaInferenceProven = $false
        cpuInferenceProven = $false
        rvcInferenceProven = $false
        voiceConversionProven = $false
        convertedAudioGenerated = $false
        audioUploaded = $false
        executionAuthorized = $false
        requiresOperatorApproval = $true
    }
}
Write-JsonAtomic $manifestPath $manifest

$readme = @"
ECHOES RVC RUNTIME FOUNDATION

Runtime: $runtime
Pinned source: $PinnedRepositoryName@$PinnedCommit
Status: $status
Provider: $provider

PARTIAL means the official source and Python 3.12 environment are ready, but dependencies are not fully installed.
PASS means pinned dependencies imported successfully. It does not mean a voice model was approved or a conversion occurred.

The launcher refuses to start while status is not PASS.
"@
$readme | Set-Content -LiteralPath (Join-Path $runtime "RVC-RUNTIME-STATUS.txt") -Encoding utf8

Write-Host "EchoesRvcFoundation $status runtime=$runtime commit=$head provider=$provider dependencies=$dependencyStatus conversion=false"
if (-not $NoOpen) { Start-Process explorer.exe -ArgumentList $runtime }
exit 0
