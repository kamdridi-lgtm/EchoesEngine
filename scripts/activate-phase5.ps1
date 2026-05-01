# STATUS: PHASE5 ACTIVATION / DRY-RUN DEFAULT / ROLLBACK SAFE
param(
    [switch]$DryRun = $true,
    [switch]$Activate = $false
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $RepoRoot "package.json"))) {
    $RepoRoot = "C:\Users\Administrator\OneDrive\Documents\EchoesEngine_complete\EchoesEngine"
}
Set-Location $RepoRoot

function Write-Safe($Message, $Color = "Cyan") {
    Write-Host "[Phase5] $Message" -ForegroundColor $Color
}

function Test-Phase4 {
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:4000/v1/health" -UseBasicParsing -TimeoutSec 5
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

Write-Safe "Pre-flight checks..."
if (-not (Test-Phase4)) {
    Write-Safe "Phase 4 public API :4000 is unreachable. Phase 5 will remain a local rehearsal." "Yellow"
}

if ($DryRun -and -not $Activate) {
    Write-Safe "DRY RUN complete. Use -Activate to run a finite closed-loop rehearsal." "Yellow"
    exit 0
}

$runnerCode = @"
import { ClosedLoopRunner } from './closed-loop-runner.ts';
import { RevenueReinvestor } from './revenue-reinvestor.ts';

async function main() {
  const loop = new ClosedLoopRunner(true);
  await loop.init();

  const reinvestor = new RevenueReinvestor({ dryRun: true, revenueThreshold: 50, upgradeProbability: 1 });
  await reinvestor.init();

  console.log('[Phase5Runner] Rehearsing three dry-run cycles...');
  for (let index = 0; index < 3; index++) {
    const cycle = await loop.runCycle();
    reinvestor.recordRevenue(25);
    const reinvestment = await reinvestor.evaluateReinvestment();
    console.log('[Phase5Runner] Cycle', cycle.cycle, 'reinvest:', reinvestment.action);
    await new Promise((resolve) => setTimeout(resolve, 500));
  }

  console.log('[Phase5Runner] Dry-run rehearsal complete.');
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
"@

Set-Content "agents\_phase5_runner.ts" $runnerCode -Encoding UTF8

Write-Safe "Starting finite Phase 5 rehearsal..."
pnpm exec tsx agents\_phase5_runner.ts
Write-Safe "Phase 5 rehearsal finished. Core stack untouched." "Green"
