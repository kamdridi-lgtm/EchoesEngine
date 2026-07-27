[CmdletBinding()]
param(
    [string]$StemRuntimeRoot = "D:\A.I\EchoesStemRuntime",
    [string]$ResultsRoot = "D:\A.I\EchoesResults",
    [string]$ControlRoot = "D:\A.I\EchoesControl",
    [string]$AnalysisLedgerPath = "D:\A.I\EchoesControl\autopilot-ledger.json",
    [int]$MaxFiles = 2,
    [switch]$DeclareUserSong,
    [switch]$Interactive
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "Echoes Stem Autopilot currently supports Windows only"
}

$runtime = [IO.Path]::GetFullPath($StemRuntimeRoot)
$python = Join-Path $runtime ".venv\Scripts\python.exe"
$core = Join-Path $runtime "tools\echoes_stem_autopilot.py"
foreach ($required in @($python, $core, $AnalysisLedgerPath)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Echoes Stem Autopilot required file is missing: $required"
    }
}

$arguments = @(
    $core,
    "--stem-runtime-root", $runtime,
    "--results-root", ([IO.Path]::GetFullPath($ResultsRoot)),
    "--control-root", ([IO.Path]::GetFullPath($ControlRoot)),
    "--analysis-ledger", ([IO.Path]::GetFullPath($AnalysisLedgerPath)),
    "--max-files", [string]$MaxFiles
)
if ($DeclareUserSong) { $arguments += "--declare-user-song" }
if ($Interactive) { $arguments += "--interactive" }

& $python @arguments
$exitCode = $LASTEXITCODE
if ($exitCode -notin @(0, 2)) {
    throw "Echoes Stem Autopilot core failed with exit code $exitCode"
}
exit $exitCode
