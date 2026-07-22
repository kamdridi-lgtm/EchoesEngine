param(
    [Parameter(Mandatory = $true)]
    [string]$ModelId,

    [Parameter(Mandatory = $true)]
    [string]$Token,

    [int]$Port = 8081,
    [string]$HostAddress = "127.0.0.1",
    [ValidateSet("auto", "cuda", "cpu")]
    [string]$Device = "auto",
    [int]$Width = 576,
    [int]$Height = 320,
    [int]$Fps = 8,
    [int]$InferenceSteps = 30,
    [int]$MaxFrames = 24
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python is not available in PATH"
}
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    throw "FFmpeg is not available in PATH"
}
if ($Width % 8 -ne 0 -or $Height % 8 -ne 0) {
    throw "Width and Height must be divisible by 8"
}

$provider = Join-Path $PSScriptRoot "..\providers\diffusers_video_provider.py"
if (-not (Test-Path $provider)) {
    throw "Provider not found: $provider"
}

$env:ECHOES_RENDER_TOKEN = $Token
$env:ECHOES_DIFFUSERS_MODEL_ID = $ModelId
$env:ECHOES_DIFFUSERS_DEVICE = $Device
$env:ECHOES_DIFFUSERS_WIDTH = "$Width"
$env:ECHOES_DIFFUSERS_HEIGHT = "$Height"
$env:ECHOES_DIFFUSERS_FPS = "$Fps"
$env:ECHOES_DIFFUSERS_STEPS = "$InferenceSteps"
$env:ECHOES_DIFFUSERS_MAX_FRAMES = "$MaxFrames"

Write-Host "Starting Echoes Diffusers provider on http://${HostAddress}:$Port"
Write-Host "Model: $ModelId"
Write-Host "Device: $Device | ${Width}x${Height} | ${Fps} fps | max ${MaxFrames} frames"
Write-Host "The token is stored only in this process environment and is not printed."

& python $provider \
    --host $HostAddress \
    --port $Port \
    --model-id $ModelId \
    --device $Device \
    --width $Width \
    --height $Height \
    --fps $Fps \
    --inference-steps $InferenceSteps \
    --max-frames $MaxFrames

exit $LASTEXITCODE
