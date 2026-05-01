// STATUS: DORMANT / v2.4.0-PREP / NOT ACTIVE
// Purpose: Future public API gateway (auth, quotas, rate-limit, proxy to :8080).
// Safe to commit. Exports only. NO auto-listen. NO intervals. NO network calls on import.
// Activation requires explicit instantiation + manual .listen() call in future code.

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

export type ProxyResult = {
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
  private internalApi = process.env.ECHOES_API_URL ?? "http://127.0.0.1:8080";

  /** Initialise la gateway en mode dormant. Aucune ecoute reseau. */
  async init(): Promise<void> {
    console.log("[ApiGateway] Initialized (dormant mode)");
  }

  /** Enregistre une cle API hachee. Aucun I/O. */
  registerKey(hash: string, tier: string = "free"): void {
    this.users.set(hash, { tier, used: 0, hash });
  }

  /** Verifie le quota sans effet de bord. Retourne true si autorise. */
  checkQuota(hash: string): boolean {
    const user = this.users.get(hash);
    if (!user) return false;

    const tier = this.tiers[user.tier] || this.tiers.free;
    return user.used < tier.monthlyLimit;
  }

  /** Proxy simule (dormant). Ne fait aucun fetch reel tant que non active. */
  async proxyRequest(_payload: Record<string, unknown>): Promise<ProxyResult> {
    return {
      ok: true,
      status: 200,
      body: {
        message: "dormant-proxy-ready",
        internalApi: this.internalApi
      }
    };
  }

  /** Retourne un instantane lecture-seule de la config. */
  snapshot(): { tiers: Record<string, TierConfig>; users: number; internalApi: string } {
    return {
      tiers: this.tiers,
      users: this.users.size,
      internalApi: this.internalApi
    };
  }
}

// Backward-compatible alias from the previous dormant gateway prep.
export const EchoesApiGateway = ApiGateway;

const isDirectRun = process.argv[1]?.replace(/\\/g, "/").endsWith("gateway/api-gateway.ts");

if (isDirectRun) {
  const gateway = new ApiGateway();
  gateway.init().catch((error) => {
    console.error("[ApiGateway] Init failed", error);
    process.exitCode = 1;
  });
}
