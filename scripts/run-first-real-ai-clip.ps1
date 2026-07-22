param(
    [string]$TorchIndexUrl = "https://download.pytorch.org/whl/cu128",
    [string]$ModelId = "ali-vilab/text-to-video-ms-1.7b",
    [string]$WorkspaceRoot = "D:\A.I\EchoesCinema",
    [int]$MinimumFreeGiB = 35,
    [int]$Port = 8081,
    [int]$TimeoutSeconds = 3600,
    [switch]$RecreateEnvironment
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not (Test-Path "D:\")) {
    throw "Drive D: is required for this proof because drive C: must not be used."
}

$workspace = [System.IO.Path]::GetFullPath($WorkspaceRoot)
$workspaceDrive = [System.IO.Path]::GetPathRoot($workspace)
if (-not $workspaceDrive -or $workspaceDrive.TrimEnd("\").ToUpperInvariant() -eq "C:") {
    throw "WorkspaceRoot must be on drive D: or another non-C: drive. Current value: $workspace"
}

$cacheRoot = Join-Path $workspace "cache"
$hubCache = Join-Path $cacheRoot "huggingface\hub"
$transformersCache = Join-Path $cacheRoot "huggingface\transformers"
$torchCache = Join-Path $cacheRoot "torch"
$pipCache = Join-Path $cacheRoot "pip"
$xdgCache = Join-Path $cacheRoot "xdg"
$cudaCache = Join-Path $cacheRoot "cuda"
$numbaCache = Join-Path $cacheRoot "numba"
$pycacheRoot = Join-Path $cacheRoot "python-bytecode"
$tempRoot = Join-Path $workspace "temp"
$venvRoot = Join-Path $workspace ".venv-cinema"
$outputRoot = Join-Path $workspace "proofs\first-real-ai-clip"
$buildDir = Join-Path $workspace "build-first-real-cli"

$bootstrap = Join-Path $repoRoot "scripts\bootstrap-cinema-ai.ps1"
$provider = Join-Path $repoRoot "providers\modelscope_proof_provider.py"
$runner = Join-Path $repoRoot "tools\cinema_job_runner.py"
$fixture = Join-Path $repoRoot "tests\fixtures\first_real_clip_sections.csv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
$providerLog = Join-Path $outputRoot "provider.log"
$providerErrorLog = Join-Path $outputRoot "provider-error.log"
$audioPath = Join-Path $outputRoot "proof-audio.wav"
$jobId = "echoes-first-real-ai-clip"
$tokenBytes = New-Object byte[] 32
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($tokenBytes)
$rng.Dispose()
$token = -join ($tokenBytes | ForEach-Object { $_.ToString("x2") })
$providerProcess = $null

function Assert-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name is not available in PATH"
    }
}

function Assert-WorkspaceCapacity {
    $root = [System.IO.Path]::GetPathRoot($workspace)
    if (-not $root) { throw "Cannot determine workspace drive: $workspace" }
    $driveName = $root.TrimEnd("\").TrimEnd(":")
    $drive = Get-PSDrive -Name $driveName -ErrorAction Stop
    $freeGiB = [math]::Round($drive.Free / 1GB, 2)
    $systemDrive = Get-PSDrive -Name "C" -ErrorAction SilentlyContinue
    $systemFreeGiB = if ($systemDrive) { [math]::Round($systemDrive.Free / 1GB, 2) } else { $null }
    $report = [ordered]@{
        schema = "echoes.cinema-storage-report.v1"
        workspace = $workspace
        workspaceDrive = $root
        workspaceFreeGiB = $freeGiB
        minimumRequiredGiB = $MinimumFreeGiB
        systemDrive = "C:\"
        systemDriveFreeGiB = $systemFreeGiB
        systemDriveWritesAllowed = $false
        status = if ($freeGiB -ge $MinimumFreeGiB) { "PASS" } else { "FAILED" }
    }
    New-Item -ItemType Directory -Path $workspace -Force | Out-Null
    $report | ConvertTo-Json | Set-Content -Path (Join-Path $workspace "storage-report.json") -Encoding utf8
    Write-Host "Workspace drive free space: $freeGiB GiB"
    Write-Host "System drive C: writes allowed: false"
    if ($freeGiB -lt $MinimumFreeGiB) {
        throw "The real-model proof needs at least $MinimumFreeGiB GiB free on $root. Available: $freeGiB GiB"
    }
}

function Configure-NonSystemStorage {
    $directories = @(
        $workspace,
        $cacheRoot,
        $hubCache,
        $transformersCache,
        $torchCache,
        $pipCache,
        $xdgCache,
        $cudaCache,
        $numbaCache,
        $pycacheRoot,
        $tempRoot,
        $outputRoot
    )
    foreach ($directory in $directories) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }

    $env:HF_HOME = Join-Path $cacheRoot "huggingface"
    $env:HF_HUB_CACHE = $hubCache
    $env:HUGGINGFACE_HUB_CACHE = $hubCache
    $env:TRANSFORMERS_CACHE = $transformersCache
    $env:TORCH_HOME = $torchCache
    $env:PIP_CACHE_DIR = $pipCache
    $env:XDG_CACHE_HOME = $xdgCache
    $env:CUDA_CACHE_PATH = $cudaCache
    $env:NUMBA_CACHE_DIR = $numbaCache
    $env:PYTHONPYCACHEPREFIX = $pycacheRoot
    $env:TEMP = $tempRoot
    $env:TMP = $tempRoot
    $env:TMPDIR = $tempRoot
    $env:HF_HUB_DISABLE_SYMLINKS_WARNING = "1"
    $env:PYTORCH_CUDA_ALLOC_CONF = "expandable_segments:True"

    Write-Host "All model, Python, Torch, pip, CUDA, temp, build and proof files are redirected to: $workspace"
}

function Find-ManifestCli {
    $candidates = @(
        (Join-Path $buildDir "Release\RenderManifestCli.exe"),
        (Join-Path $buildDir "bin\Release\RenderManifestCli.exe"),
        (Join-Path $buildDir "RenderManifestCli.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return (Resolve-Path $candidate).Path }
    }
    $found = Get-ChildItem $buildDir -Recurse -Filter RenderManifestCli.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($found) { return $found.FullName }
    throw "RenderManifestCli.exe was not produced"
}

try {
    Assert-Command "cmake"
    Assert-Command "ffmpeg"
    Assert-Command "ffprobe"
    Assert-Command "py"
    Assert-WorkspaceCapacity
    Configure-NonSystemStorage

    $bootstrapArgs = @(
        "-ExecutionPolicy", "Bypass",
        "-File", $bootstrap,
        "-PythonLauncher", "py",
        "-PythonVersion", "3.10",
        "-VenvPath", $venvRoot,
        "-TorchIndexUrl", $TorchIndexUrl
    )
    if ($RecreateEnvironment) { $bootstrapArgs += "-Recreate" }

    Write-Host "[1/7] Preparing the CUDA Diffusers environment on $workspace"
    & powershell @bootstrapArgs
    if ($LASTEXITCODE -ne 0) { throw "Cinema bootstrap failed" }
    if (-not (Test-Path $venvPython)) { throw "Cinema Python not found: $venvPython" }

    Write-Host "[2/7] Recording GPU evidence"
    $gpuReport = & $venvPython -c "import json, torch; assert torch.cuda.is_available(), 'CUDA unavailable'; p=torch.cuda.get_device_properties(0); print(json.dumps({'available':True,'name':torch.cuda.get_device_name(0),'vramBytes':p.total_memory,'vramGiB':round(p.total_memory/1024**3,2),'torch':torch.__version__,'cuda':torch.version.cuda}, indent=2))"
    if ($LASTEXITCODE -ne 0) { throw "GPU diagnostic failed" }
    $gpuReport | Set-Content -Path (Join-Path $outputRoot "gpu-report.json") -Encoding utf8
    Write-Host $gpuReport

    Write-Host "[3/7] Building RenderManifestCli on $workspaceDrive"
    if (Test-Path $buildDir) { Remove-Item $buildDir -Recurse -Force }
    & cmake -S (Join-Path $repoRoot "cmake\prompt_director") -B $buildDir -G "Visual Studio 17 2022" -A x64
    if ($LASTEXITCODE -ne 0) { throw "RenderManifestCli configure failed" }
    & cmake --build $buildDir --config Release --target RenderManifestCli -- /m:1
    if ($LASTEXITCODE -ne 0) { throw "RenderManifestCli build failed" }
    $manifestCli = Find-ManifestCli
    Write-Host "Manifest CLI: $manifestCli"

    Write-Host "[4/7] Creating a four-second proof audio bed on D:"
    & ffmpeg -hide_banner -loglevel error -y -f lavfi -i "sine=frequency=110:sample_rate=44100" -t 4 -ac 2 -c:a pcm_s16le $audioPath
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $audioPath)) { throw "Proof audio generation failed" }

    Write-Host "[5/7] Loading the real video model. The first download is large and remains in $cacheRoot"
    $env:ECHOES_RENDER_TOKEN = $token
    $providerArgs = @(
        $provider,
        "--host", "127.0.0.1",
        "--port", "$Port",
        "--token", $token,
        "--model-id", $ModelId,
        "--device", "cuda",
        "--width", "576",
        "--height", "320",
        "--fps", "8",
        "--inference-steps", "25",
        "--max-frames", "32"
    )
    $providerProcess = Start-Process -FilePath $venvPython -ArgumentList $providerArgs -WorkingDirectory $workspace -RedirectStandardOutput $providerLog -RedirectStandardError $providerErrorLog -PassThru

    $healthUrl = "http://127.0.0.1:$Port/health"
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $health = $null
    while ((Get-Date) -lt $deadline) {
        if ($providerProcess.HasExited) {
            $detail = if (Test-Path $providerErrorLog) { Get-Content $providerErrorLog -Raw } else { "provider exited" }
            throw "Provider exited before becoming ready: $detail"
        }
        try {
            $headers = @{ Authorization = "Bearer $token" }
            $health = Invoke-RestMethod -Uri $healthUrl -Headers $headers -TimeoutSec 10
            if ($health.realModelLoaded -eq $true) { break }
            if ($health.loadError) { throw "Model load failed: $($health.loadError)" }
        } catch {
            if ($_.Exception.Message -like "Model load failed:*") { throw }
        }
        Start-Sleep -Seconds 5
    }
    if (-not $health -or $health.realModelLoaded -ne $true) {
        throw "Timed out waiting for realModelLoaded=true. See $providerLog and $providerErrorLog"
    }
    $health | ConvertTo-Json -Depth 12 | Set-Content -Path (Join-Path $outputRoot "provider-health.json") -Encoding utf8
    if ($health.commercialUseAllowed -ne $false) { throw "Proof provider license classification is missing" }
    Write-Host "Real model loaded: $($health.modelId) on $($health.gpu.name)"

    Write-Host "[6/7] Rendering the first real AI clip through the canonical Cinema runner"
    $env:ECHOES_RENDER_ENDPOINT = "http://127.0.0.1:$Port/v1/render"
    $env:ECHOES_RENDER_HEALTH_URL = $healthUrl
    $env:ECHOES_RENDER_HOST_ALLOWLIST = "127.0.0.1,localhost"
    $env:ECHOES_RENDER_TOKEN = $token

    & $venvPython $runner `
        $fixture `
        $outputRoot `
        --manifest-cli $manifestCli `
        --job-id $jobId `
        --seed 7331 `
        --backend http `
        --audio $audioPath `
        --provider-timeout 1800
    if ($LASTEXITCODE -ne 0) { throw "Real Cinema job failed" }

    Write-Host "[7/7] Verifying truth status and final media"
    $resultPath = Join-Path $outputRoot "job-result.json"
    if (-not (Test-Path $resultPath)) { throw "job-result.json is missing" }
    $result = Get-Content $resultPath -Raw | ConvertFrom-Json
    if ($result.status -ne "PASS") { throw "Cinema job did not PASS: $($result.error)" }
    if ($result.backendStatus -ne "REAL") { throw "Cinema job was not classified REAL: $($result.backendStatus)" }
    $finalMp4 = Join-Path $outputRoot "$jobId.mp4"
    if (-not (Test-Path $finalMp4) -or (Get-Item $finalMp4).Length -le 0) { throw "Final MP4 is missing or empty" }

    Write-Host ""
    Write-Host "FIRST REAL ECHOES CINEMA AI CLIP: PASS"
    Write-Host "Video: $finalMp4"
    Write-Host "Result: $resultPath"
    Write-Host "Provider health: $(Join-Path $outputRoot 'provider-health.json')"
    Write-Host "GPU report: $(Join-Path $outputRoot 'gpu-report.json')"
    Write-Host "Storage report: $(Join-Path $workspace 'storage-report.json')"
    Write-Host "C: was not selected for model caches, virtual environment, temp, build or output."
    Write-Host "License note: this proof model is non-commercial and must be replaced before paid production."
}
finally {
    if ($providerProcess -and -not $providerProcess.HasExited) {
        Stop-Process -Id $providerProcess.Id -Force -ErrorAction SilentlyContinue
    }
    @(
        "ECHOES_RENDER_TOKEN",
        "ECHOES_RENDER_ENDPOINT",
        "ECHOES_RENDER_HEALTH_URL",
        "ECHOES_RENDER_HOST_ALLOWLIST"
    ) | ForEach-Object { Remove-Item "Env:$_" -ErrorAction SilentlyContinue }
}
