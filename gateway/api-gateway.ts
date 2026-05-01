// STATUS: DORMANT / v2.4.0-PREP / NOT ACTIVE
// Purpose: Secure gateway routing to diffusion (:8081) & post-process (:8082) workers.
// Safe to commit. Exports only. NO auto-listen. NO intervals. No network calls on import/direct init.
// Manual activation only: routeToWorker() performs fetch only when explicitly called.

export interface TierConfig {
  rpm: number;
  monthlyLimit: number;
  priority: number;
}

export interface UserState {
  tier: string;
  used: number;
  hash: string;
}

export type WorkerTarget = "diffusion" | "post";

export type GatewayRouteResult = {
  ok: boolean;
  status: number;
  body?: unknown;
};

export class ApiGateway {
  private tiers: Record<string, TierConfig> = {
    free: { rpm: 5, monthlyLimit: 10, priority: 0 },
    pro: { rpm: 30, monthlyLimit: 150, priority: 1 },
    studio: { rpm: 100, monthlyLimit: 1000, priority: 2 }
  };

  private users = new Map<string, UserState>();
  private diffusionWorker = process.env.ECHOES_DIFFUSION_WORKER_URL ?? "http://127.0.0.1:8081";
  private postWorker = process.env.ECHOES_POST_WORKER_URL ?? "http://127.0.0.1:8082";

  async init(): Promise<void> {
    console.log("[ApiGateway] Initialized (dormant mode)");
  }

  registerKey(hash: string, tier: string = "free"): void {
    this.users.set(hash, { tier, used: 0, hash });
  }

  checkQuota(hash: string): boolean {
    const user = this.users.get(hash);
    if (!user) return false;

    const tier = this.tiers[user.tier] || this.tiers.free;
    return user.used < tier.monthlyLimit;
  }

  /** Route une requete vers le worker approprie avec priorite & quota check. Manual only. */
  async routeToWorker(hash: string, target: WorkerTarget, payload: Record<string, unknown>): Promise<GatewayRouteResult> {
    if (!this.checkQuota(hash)) {
      return { ok: false, status: 429, body: { error: "quota_exceeded" } };
    }

    const user = this.users.get(hash);
    if (!user) {
      return { ok: false, status: 401, body: { error: "unknown_user" } };
    }

    const tier = this.tiers[user.tier] || this.tiers.free;
    const enriched = { ...payload, priority: tier.priority, tier: user.tier };
    const workerUrl = target === "diffusion" ? `${this.diffusionWorker}/generate` : `${this.postWorker}/process`;

    try {
      const response = await fetch(workerUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(enriched)
      });

      const body = await response.json().catch(() => null);
      if (response.ok) user.used++;

      return { ok: response.ok, status: response.status, body };
    } catch {
      return { ok: false, status: 502, body: { error: "worker_unreachable" } };
    }
  }

  snapshot(): { tiers: Record<string, TierConfig>; users: number; workers: Record<WorkerTarget, string> } {
    return {
      tiers: this.tiers,
      users: this.users.size,
      workers: {
        diffusion: this.diffusionWorker,
        post: this.postWorker
      }
    };
  }
}

// Backward-compatible alias from earlier dormant gateway prep.
export const EchoesApiGateway = ApiGateway;

const isDirectRun = process.argv[1]?.replace(/\\/g, "/").endsWith("gateway/api-gateway.ts");

if (isDirectRun) {
  const gateway = new ApiGateway();
  gateway.init().catch((error) => {
    console.error("[ApiGateway] Init failed", error);
    process.exitCode = 1;
  });
}
