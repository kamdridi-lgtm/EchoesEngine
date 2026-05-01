# STATUS: PHASE4 ACTIVATION / DRY-RUN DEFAULT / ROLLBACK SAFE
param(
    [switch]$DryRun = $true,
    [switch]$Activate = $false
)

$ErrorActionPreference = "Stop"
$RepoRoot = "C:\Users\Administrator\OneDrive\Documents\EchoesEngine_complete\EchoesEngine"
if (-not (Test-Path $RepoRoot)) {
    $RepoRoot = Split-Path -Parent $PSScriptRoot
}
Set-Location $RepoRoot

function Write-Safe($Message, $Color = "Cyan") {
    Write-Host "[Phase4] $Message" -ForegroundColor $Color
}

function Test-PreviousGateway {
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:3000/health" -UseBasicParsing -TimeoutSec 5
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

Write-Safe "Pre-flight checks..."
if (-not (Test-PreviousGateway)) {
    Write-Safe "Gateway :3000 unreachable. Keep Phase 4 dormant until previous phases are active." "Yellow"
}

if ($DryRun -and -not $Activate) {
    Write-Safe "DRY RUN complete. Use -Activate to start the localhost-only public layer." "Yellow"
    exit 0
}

$runnerCode = @"
import { PublicApi } from './gateway/public-api.ts';
import { LoraManager } from './agents/lora-manager.ts';
import { NodeDiscovery } from './agents/node-discovery.ts';

async function main() {
  const api = new PublicApi({ dryRun: false });
  await api.init();

  const lora = new LoraManager({ dryRun: false });
  await lora.init();
  await lora.scanVersions();
  const dataset = await lora.checkDatasetThreshold(50);
  console.log('[Phase4Runner] Dataset ready:', dataset.ready, '(' + dataset.count + ' entries)');

  const nodes = new NodeDiscovery(['http://127.0.0.1:8081'], false);
  await nodes.init();
  await nodes.refresh();
  console.log('[Phase4Runner] Nodes online:', nodes.snapshot().online);

  await api.listen(4000);
  console.log('[Phase4Runner] Public API active on 127.0.0.1:4000');
  await new Promise(() => {});
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
"@

Set-Content "agents\_phase4_runner.ts" $runnerCode -Encoding UTF8

Write-Safe "Starting Phase 4 runner..."
$process = Start-Process pnpm -ArgumentList "dlx tsx agents\_phase4_runner.ts" -PassThru -WindowStyle Hidden
Write-Safe "Phase 4 runner PID: $($process.Id)" "Green"
Write-Safe "Rollback: Stop-Process -Id $($process.Id) -Force" "Yellow"
