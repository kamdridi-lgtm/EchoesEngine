// STATUS: DORMANT / v2.4.0-PREP / NOT ACTIVE
// Purpose: Future public API gateway facade for EchoesEngine.
// Safe to commit. NO auto-listen. NO server startup on import. NO network calls on import.
// Activation requires explicit import + manual method calls or a future runner.

export type EchoesTier = "free" | "pro" | "elite";

export type EchoesGatewayUser = {
  apiKeyHash: string;
  tier: EchoesTier;
  rendersUsed: number;
  monthlyLimit: number;
};

export type EchoesGeneratePayload = {
  prompt: string;
  style?: string;
  audio?: string;
  duration?: number;
  resolution?: string;
};

export type EchoesGatewayDecision = {
  ok: boolean;
  status: number;
  reason?: string;
  priority?: number;
};

const TIER_PRIORITY: Record<EchoesTier, number> = {
  free: 0,
  pro: 1,
  elite: 2
};

export class EchoesApiGateway {
  private readonly internalApiBase: string;

  constructor(internalApiBase = process.env.ECHOES_API_URL ?? "http://127.0.0.1:8080") {
    this.internalApiBase = internalApiBase;
  }

  async init(): Promise<void> {
    console.log("[EchoesApiGateway] Initialized (dormant mode)");
  }

  validateUser(user: EchoesGatewayUser | null | undefined): EchoesGatewayDecision {
    if (!user) return { ok: false, status: 401, reason: "missing_user" };
    if (user.rendersUsed >= user.monthlyLimit) return { ok: false, status: 429, reason: "quota_exceeded" };
    return { ok: true, status: 200, priority: TIER_PRIORITY[user.tier] ?? 0 };
  }

  prepareGeneratePayload(user: EchoesGatewayUser, payload: EchoesGeneratePayload): EchoesGeneratePayload & { tier: EchoesTier; priority: number } {
    const priority = TIER_PRIORITY[user.tier] ?? 0;

    return {
      ...payload,
      tier: user.tier,
      priority
    };
  }

  targetUrl(path: string): string {
    const cleanPath = path.startsWith("/") ? path : `/${path}`;
    return `${this.internalApiBase}${cleanPath}`;
  }
}

const isDirectRun = process.argv[1]?.replace(/\\/g, "/").endsWith("gateway/api-gateway.ts");

if (isDirectRun) {
  const gateway = new EchoesApiGateway();
  gateway.init().catch((error) => {
    console.error("[EchoesApiGateway] Init failed", error);
    process.exitCode = 1;
  });
}
