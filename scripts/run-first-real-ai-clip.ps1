param(
    [string]$TorchIndexUrl = "https://download.pytorch.org/whl/cu128",
    [string]$ModelId = "ali-vilab/text-to-video-ms-1.7b",
    [string]$WorkspaceRoot = "D:\A.I\EchoesCinema",
    [int]$MinimumFreeGiB = 35,
    [int]$Port = 8081,
    [int]$TimeoutSeconds = 7200,
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
$proofsRoot = Join-Path $workspace "proofs"
$outputRoot = Join-Path $proofsRoot "first-real-ai-clip"
$archiveRoot = Join-Path $proofsRoot "archive"

$bootstrap = Join-Path $repoRoot "scripts\bootstrap-cinema-ai.ps1"
$provider = Join-Path $repoRoot "providers\modelscope_low_vram_provider.py"
$runner = Join-Path $repoRoot "tools\cinema_job_runner.py"
$preflight = Join-Path $repoRoot "tools\cinema_p0_preflight.py"
$fixture = Join-Path $repoRoot "tests\fixtures\first_real_clip_sections.csv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
$providerLog = Join-Path $outputRoot "provider.log"
$providerErrorLog = Join-Path $outputRoot "provider-error.log"
$preflightReport = Join-Path $outputRoot "preflight-report.json"
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
        $proofsRoot,
        $archiveRoot
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

    Write-Host "All model, Python, Torch, pip, CUDA, temp and proof files are redirected to: $workspace"
}

function Prepare-FreshProofDirectory {
    if (Test-Path $outputRoot) {
        $existing = Get-ChildItem -LiteralPath $outputRoot -Force -ErrorAction SilentlyContinue
        if ($existing) {
            $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
            $destination = Join-Path $archiveRoot "first-real-ai-clip-$stamp"
            Move-Item -LiteralPath $outputRoot -Destination $destination
            Write-Host "Previous proof archived: $destination"
        } else {
            Remove-Item -LiteralPath $outputRoot -Recurse -Force
        }
    }
    New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
}

function Show-ModelLoadProgress {
    $downloadedBytes = 0
    if (Test-Path $hubCache) {
        $measurement = Get-ChildItem -LiteralPath $hubCache -Recurse -File -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum
        if ($measurement.Sum) { $downloadedBytes = [double]$measurement.Sum }
    }
    $downloadedGiB = [math]::Round($downloadedBytes / 1GB, 2)
    Write-Host "Model still loading/downloaded cache: $downloadedGiB GiB on D:"
    if (Test-Path $providerErrorLog) {
        $tail = Get-Content -LiteralPath $providerErrorLog -Tail 2 -ErrorAction SilentlyContinue
        foreach ($line in $tail) {
            if ($line) { Write-Host "  provider: $line" }
        }
    }
}

try {
    Assert-Command "ffmpeg"
    Assert-Command "ffprobe"
    Assert-WorkspaceCapacity
    Configure-NonSystemStorage
    Prepare-FreshProofDirectory

    $bootstrapArgs = @(
        "-ExecutionPolicy", "Bypass",
        "-File", $bootstrap,
        "-VenvPath", $venvRoot,
        "-TorchIndexUrl", $TorchIndexUrl
    )
    if ($RecreateEnvironment) { $bootstrapArgs += "-Recreate" }

    Write-Host "[1/7] Preparing the CUDA Diffusers environment on $workspace"
    & powershell @bootstrapArgs
    if ($LASTEXITCODE -ne 0) { throw "Cinema bootstrap failed" }
    if (-not (Test-Path $venvPython)) { throw "Cinema Python not found: $venvPython" }
    if (-not (Test-Path $preflight -PathType Leaf)) { throw "Cinema P0 preflight not found: $preflight" }

    Write-Host "[2/7] Running fail-closed P0 preflight before model load"
    & $venvPython $preflight `
        --workspace $workspace `
        --output $preflightReport `
        --minimum-free-gib $MinimumFreeGiB `
        --expected-drive "D:" `
        --provider-host "127.0.0.1" `
        --provider-port $Port `
        --require-cuda
    if ($LASTEXITCODE -ne 0) {
        throw "Cinema P0 preflight failed. See $preflightReport"
    }

    Write-Host "[3/7] Recording GPU evidence and validating the low-VRAM provider"
    $gpuReport = & $venvPython -c "import json, torch; assert torch.cuda.is_available(), 'CUDA unavailable'; p=torch.cuda.get_device_properties(0); print(json.dumps({'available':True,'name':torch.cuda.get_device_name(0),'vramBytes':p.total_memory,'vramGiB':round(p.total_memory/1024**3,2),'torch':torch.__version__,'cuda':torch.version.cuda}, indent=2))"
    if ($LASTEXITCODE -ne 0) { throw "GPU diagnostic failed" }
    $gpuReport | Set-Content -Path (Join-Path $outputRoot "gpu-report.json") -Encoding utf8
    Write-Host $gpuReport
    & $venvPython $provider --self-test
    if ($LASTEXITCODE -ne 0) { throw "Low-VRAM provider self-test failed" }

    Write-Host "[4/7] Creating a four-second proof audio bed on D:"
    & ffmpeg -hide_banner -loglevel error -y -f lavfi -i "sine=frequency=110:sample_rate=44100" -t 4 -ac 2 -c:a pcm_s16le $audioPath
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $audioPath)) { throw "Proof audio generation failed" }

    Write-Host "[5/7] Loading the real video model. The first download remains in $cacheRoot"
    Write-Host "RTX 2060 profile: 384x216, 4 fps, 16 frames, 15 inference steps"
    Write-Host "Memory strategy: sequential CPU offload with one smaller OOM retry"
    $env:ECHOES_RENDER_TOKEN = $token
    $providerArgs = @(
        $provider,
        "--host", "127.0.0.1",
        "--port", "$Port",
        "--token", $token,
        "--model-id", $ModelId,
        "--device", "cuda",
        "--width", "384",
        "--height", "216",
        "--fps", "4",
        "--inference-steps", "15",
        "--max-frames", "16"
    )
    $providerProcess = Start-Process -FilePath $venvPython -ArgumentList $providerArgs -WorkingDirectory $workspace -RedirectStandardOutput $providerLog -RedirectStandardError $providerErrorLog -PassThru

    $healthUrl = "http://127.0.0.1:$Port/health"
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastProgress = (Get-Date).AddSeconds(-31)
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
        if (((Get-Date) - $lastProgress).TotalSeconds -ge 30) {
            Show-ModelLoadProgress
            $lastProgress = Get-Date
        }
        Start-Sleep -Seconds 5
    }
    if (-not $health -or $health.realModelLoaded -ne $true) {
        throw "Timed out waiting for realModelLoaded=true. See $providerLog and $providerErrorLog"
    }
    $health | ConvertTo-Json -Depth 12 | Set-Content -Path (Join-Path $outputRoot "provider-health.json") -Encoding utf8
    if ($health.commercialUseAllowed -ne $false) { throw "Proof provider license classification is missing" }
    if ([double]$health.gpu.vramGiB -le 6.5 -and $health.offloadStrategy -ne "sequential-cpu-offload") {
        throw "Low-VRAM GPU did not activate sequential CPU offload"
    }
    Write-Host "Real model loaded: $($health.modelId) on $($health.gpu.name)"
    Write-Host "Offload strategy: $($health.offloadStrategy)"

    Write-Host "[6/7] Rendering through the compiler-free Python manifest path"
    $env:ECHOES_RENDER_ENDPOINT = "http://127.0.0.1:$Port/v1/render"
    $env:ECHOES_RENDER_HEALTH_URL = $healthUrl
    $env:ECHOES_RENDER_HOST_ALLOWLIST = "127.0.0.1,localhost"
    $env:ECHOES_RENDER_TOKEN = $token

    & $venvPython $runner `
        $fixture `
        $outputRoot `
        --job-id $jobId `
        --seed 7331 `
        --backend http `
        --audio $audioPath `
        --provider-timeout 3600
    if ($LASTEXITCODE -ne 0) { throw "Real Cinema job failed" }

    Write-Host "[7/7] Verifying truth status and final media"
    $resultPath = Join-Path $outputRoot "job-result.json"
    if (-not (Test-Path $resultPath)) { throw "job-result.json is missing" }
    $result = Get-Content $resultPath -Raw | ConvertFrom-Json
    if ($result.status -ne "PASS") { throw "Cinema job did not PASS: $($result.error)" }
    if ($result.backendStatus -ne "REAL") { throw "Cinema job was not classified REAL: $($result.backendStatus)" }
    if ($result.manifestGenerator -ne "python-render-manifest-v1") { throw "Compiler-free manifest generator was not used" }
    $finalMp4 = Join-Path $outputRoot "$jobId.mp4"
    if (-not (Test-Path $finalMp4) -or (Get-Item $finalMp4).Length -le 0) { throw "Final MP4 is missing or empty" }

    Write-Host ""
    Write-Host "FIRST REAL ECHOES CINEMA AI CLIP: PASS"
    Write-Host "Video: $finalMp4"
    Write-Host "Result: $resultPath"
    Write-Host "Preflight: $preflightReport"
    Write-Host "Provider health: $(Join-Path $outputRoot 'provider-health.json')"
    Write-Host "GPU report: $(Join-Path $outputRoot 'gpu-report.json')"
    Write-Host "Storage report: $(Join-Path $workspace 'storage-report.json')"
    Write-Host "Visual Studio was not required for this proof."
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
