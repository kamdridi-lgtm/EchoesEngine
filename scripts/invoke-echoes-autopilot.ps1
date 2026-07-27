[CmdletBinding()]
param(
    [string]$AutopilotRoot = "D:\A.I\EchoesAutopilot",
    [string]$RuntimeRoot = "D:\A.I\EchoesEngineRuntime",
    [string]$InboxRoot = "D:\A.I\EchoesInbox",
    [string]$ResultsRoot = "D:\A.I\EchoesResults",
    [string]$ControlRoot = "D:\A.I\EchoesControl",
    [string]$PolicyPath = "",
    [string]$RemotePolicyUrl = "https://raw.githubusercontent.com/kamdridi-lgtm/EchoesEngine/main/config/echoes-autopilot-policy.v1.json",
    [switch]$Interactive,
    [switch]$SkipRemotePolicy
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "Echoes Autopilot currently supports Windows only"
}

$runtime = [IO.Path]::GetFullPath($RuntimeRoot)
$autopilot = [IO.Path]::GetFullPath($AutopilotRoot)
$python = Join-Path $runtime ".venv\Scripts\python.exe"
$core = Join-Path $autopilot "echoes_autopilot.py"
$policy = if ($PolicyPath) { [IO.Path]::GetFullPath($PolicyPath) } else { Join-Path $autopilot "echoes-autopilot-policy.v1.json" }

foreach ($required in @($python, $core, $policy)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Echoes Autopilot installed file is missing: $required"
    }
}

$arguments = @(
    $core,
    "--autopilot-root", $autopilot,
    "--runtime-root", $runtime,
    "--inbox-root", ([IO.Path]::GetFullPath($InboxRoot)),
    "--results-root", ([IO.Path]::GetFullPath($ResultsRoot)),
    "--control-root", ([IO.Path]::GetFullPath($ControlRoot)),
    "--policy", $policy,
    "--remote-policy-url", $RemotePolicyUrl
)
if ($Interactive) { $arguments += "--interactive" }
if ($SkipRemotePolicy) { $arguments += "--skip-remote-policy" }

& $python @arguments
$exitCode = $LASTEXITCODE
if ($exitCode -notin @(0, 2)) {
    throw "Echoes Autopilot core failed with exit code $exitCode"
}
exit $exitCode
