/**
 * STATUS: DORMANT / v2.4.0-PREP / NOT ACTIVE
 * Purpose: future adaptive job orchestrator for EchoesEngine.
 * Safe to commit. It does not auto-submit jobs and does not affect the current C++ daemon.
 * Activation requires explicit code changes or a direct method call from a runner.
 */

type EchoesJobStatus = "SUBMITTED" | "RUNNING" | "FINISHED" | "FAILED";

type EchoesJobRecord = {
  id: string;
  status: EchoesJobStatus;
  style?: string;
  submittedAt: string;
  attempts: number;
};

const DEFAULT_API = process.env.ECHOES_API_URL ?? "http://127.0.0.1:8080";

export class AdaptiveOrchestrator {
  private readonly apiUrl: string;
  private readonly jobs = new Map<string, EchoesJobRecord>();
  private initialized = false;

  constructor(apiUrl = DEFAULT_API) {
    this.apiUrl = apiUrl;
  }

  async init(): Promise<void> {
    this.initialized = true;
    console.log("[AdaptiveOrchestrator] Initialized (dormant mode)");
  }

  /**
   * Dormant by design. This method exists for future wiring, but it is not called automatically.
   */
  async submitIfCapacity(): Promise<false> {
    if (!this.initialized) {
      throw new Error("AdaptiveOrchestrator must be initialized before submitIfCapacity().");
    }

    console.log(`[AdaptiveOrchestrator] Dormant: no job submitted. API target: ${this.apiUrl}`);
    return false;
  }

  snapshot(): EchoesJobRecord[] {
    return [...this.jobs.values()];
  }
}

if (process.argv[1]?.toLowerCase().endsWith("adaptive-orchestrator.ts")) {
  const orchestrator = new AdaptiveOrchestrator();
  orchestrator.init().catch((error) => {
    console.error("[AdaptiveOrchestrator] Init failed", error);
    process.exitCode = 1;
  });
}

