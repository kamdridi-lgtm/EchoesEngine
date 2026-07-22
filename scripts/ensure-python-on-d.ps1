param(
    [string]$InstallRoot = "D:\A.I\Python310",
    [string]$WorkspaceRoot = "D:\A.I\EchoesCinema",
    [string]$PythonVersion = "3.10.20",
    [switch]$ForceInstall
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Test-PythonCandidate {
    param([string]$Path)

    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }

    try {
        $probe = & $Path -c "import json, struct, sys; print(json.dumps({'path':sys.executable,'major':sys.version_info.major,'minor':sys.version_info.minor,'bits':struct.calcsize('P')*8,'version':sys.version.split()[0]}))" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $probe) { return $null }
        $info = ($probe | Select-Object -Last 1) | ConvertFrom-Json
        if ($info.major -ne 3 -or $info.minor -notin @(10, 11) -or $info.bits -ne 64) {
            return $null
        }
        & $Path -m venv --help *> $null
        if ($LASTEXITCODE -ne 0) { return $null }
        return [pscustomobject]@{
            path = (Resolve-Path -LiteralPath $Path).Path
            version = $info.version
            bits = $info.bits
        }
    } catch {
        return $null
    }
}

function Add-Candidate {
    param(
        [System.Collections.Generic.List[string]]$List,
        [string]$Path
    )
    if (-not $Path) { return }
    $clean = $Path.Trim().Trim('"')
    if ($clean -and -not $List.Contains($clean)) {
        [void]$List.Add($clean)
    }
}

function Get-RegisteredPythonCandidates {
    $candidates = [System.Collections.Generic.List[string]]::new()
    foreach ($root in @(
        "HKCU:\Software\Python\PythonCore",
        "HKLM:\Software\Python\PythonCore",
        "HKLM:\Software\WOW6432Node\Python\PythonCore"
    )) {
        if (-not (Test-Path $root)) { continue }
        foreach ($versionKey in Get-ChildItem $root -ErrorAction SilentlyContinue) {
            if ($versionKey.PSChildName -notmatch '^3\.(10|11)') { continue }
            $installPathKey = Join-Path $versionKey.PSPath "InstallPath"
            try {
                $installDirectory = (Get-ItemProperty -Path $installPathKey -ErrorAction Stop).'(default)'
                if ($installDirectory) {
                    Add-Candidate -List $candidates -Path (Join-Path $installDirectory "python.exe")
                }
            } catch {}
        }
    }
    return $candidates
}

if (-not (Test-Path "D:\")) {
    throw "Drive D: is required. Python will not be installed on drive C:."
}

$installRootFull = [System.IO.Path]::GetFullPath($InstallRoot)
$workspaceFull = [System.IO.Path]::GetFullPath($WorkspaceRoot)
foreach ($pathToCheck in @($installRootFull, $workspaceFull)) {
    $root = [System.IO.Path]::GetPathRoot($pathToCheck)
    if (-not $root -or $root.TrimEnd('\').ToUpperInvariant() -eq 'C:') {
        throw "Python and Cinema workspace must be on D: or another non-C: drive: $pathToCheck"
    }
}

$installerDirectory = Join-Path $workspaceFull "installers"
$tempDirectory = Join-Path $workspaceFull "temp"
$cacheDirectory = Join-Path $workspaceFull "cache"
foreach ($directory in @($installRootFull, $installerDirectory, $tempDirectory, $cacheDirectory)) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}

$env:TEMP = $tempDirectory
$env:TMP = $tempDirectory
$env:TMPDIR = $tempDirectory
$env:PIP_CACHE_DIR = Join-Path $cacheDirectory "pip"
New-Item -ItemType Directory -Path $env:PIP_CACHE_DIR -Force | Out-Null

$candidates = [System.Collections.Generic.List[string]]::new()
Add-Candidate -List $candidates -Path (Join-Path $installRootFull "python.exe")
Add-Candidate -List $candidates -Path $env:ECHOES_PYTHON_EXE
Add-Candidate -List $candidates -Path "D:\Python310\python.exe"
Add-Candidate -List $candidates -Path "D:\Python311\python.exe"
Add-Candidate -List $candidates -Path "D:\A.I\Python310\python.exe"
Add-Candidate -List $candidates -Path "D:\A.I\Python311\python.exe"

foreach ($commandName in @("python", "python3")) {
    $command = Get-Command $commandName -ErrorAction SilentlyContinue
    if ($command -and $command.Source) {
        Add-Candidate -List $candidates -Path $command.Source
    }
}

foreach ($registered in Get-RegisteredPythonCandidates) {
    Add-Candidate -List $candidates -Path $registered
}

$pyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($pyLauncher) {
    try {
        $launcherOutput = & $pyLauncher.Source -0p 2>$null
        foreach ($line in $launcherOutput) {
            if ($line -match '([A-Za-z]:\\[^\r\n]*?python\.exe)') {
                Add-Candidate -List $candidates -Path $Matches[1]
            }
        }
    } catch {}
}

if (-not $ForceInstall) {
    foreach ($candidate in $candidates) {
        $valid = Test-PythonCandidate -Path $candidate
        if ($valid) {
            Write-Host "Using verified Python $($valid.version) at $($valid.path)"
            Write-Output $valid.path
            exit 0
        }
    }
}

$installerName = "python-$PythonVersion-amd64.exe"
$installerPath = Join-Path $installerDirectory $installerName
$installerUrl = "https://www.python.org/ftp/python/$PythonVersion/$installerName"

Write-Host "No usable 64-bit Python 3.10/3.11 installation was found."
Write-Host "The Windows Python launcher is stale or points to a missing executable."
Write-Host "Installing official Python $PythonVersion on D: at $installRootFull"

if (-not (Test-Path $installerPath -PathType Leaf)) {
    Invoke-WebRequest -Uri $installerUrl -OutFile $installerPath -UseBasicParsing
}

$signature = Get-AuthenticodeSignature -FilePath $installerPath
if ($signature.Status -ne "Valid") {
    throw "Downloaded Python installer signature is not valid: $($signature.Status)"
}
$signer = if ($signature.SignerCertificate) { $signature.SignerCertificate.Subject } else { "" }
if ($signer -notmatch "Python Software Foundation") {
    throw "Downloaded installer signer is unexpected: $signer"
}

$installArguments = @(
    "/quiet",
    "InstallAllUsers=0",
    "TargetDir=$installRootFull",
    "DefaultJustForMeTargetDir=$installRootFull",
    "AssociateFiles=0",
    "CompileAll=0",
    "Include_debug=0",
    "Include_dev=1",
    "Include_doc=0",
    "Include_exe=1",
    "Include_launcher=0",
    "Include_lib=1",
    "Include_pip=1",
    "Include_symbols=0",
    "Include_tcltk=0",
    "Include_test=0",
    "Include_tools=1",
    "PrependPath=0",
    "Shortcuts=0",
    "SimpleInstall=1"
)

$process = Start-Process -FilePath $installerPath -ArgumentList $installArguments -Wait -PassThru
if ($process.ExitCode -notin @(0, 3010)) {
    throw "Python installer failed with exit code $($process.ExitCode)"
}

$installedPython = Join-Path $installRootFull "python.exe"
$verified = Test-PythonCandidate -Path $installedPython
if (-not $verified) {
    throw "Python installation completed but a usable interpreter was not found at $installedPython"
}

$report = [ordered]@{
    schema = "echoes.python-bootstrap.v1"
    status = "PASS"
    python = $verified.path
    version = $verified.version
    bits = $verified.bits
    installRoot = $installRootFull
    installer = $installerPath
    signer = $signer
    workspace = $workspaceFull
    systemDriveDataTarget = $false
}
$report | ConvertTo-Json -Depth 6 | Set-Content -Path (Join-Path $workspaceFull "python-bootstrap-report.json") -Encoding utf8

Write-Host "Python bootstrap PASS: $($verified.path)"
Write-Output $verified.path
