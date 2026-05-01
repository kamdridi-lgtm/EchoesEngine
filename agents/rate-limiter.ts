// STATUS: DORMANT / PHASE4-PREP / NOT ACTIVE
// Purpose: Token-bucket rate limiter for future public API routing.
// Safe to commit. No auto-execution, no intervals, no network calls.

export interface RateLimitConfig {
  windowMs: number;
  maxRequests: number;
  dryRun: boolean;
}

type Bucket = {
  tokens: number;
  lastRefill: number;
};

export class RateLimiter {
  private buckets = new Map<string, Bucket>();
  private config: RateLimitConfig;

  constructor(config: Partial<RateLimitConfig> = {}) {
    this.config = {
      windowMs: 60_000,
      maxRequests: 10,
      dryRun: true,
      ...config
    };
  }

  async init(): Promise<void> {
    console.log(`[RateLimiter] Initialized (dryRun=${this.config.dryRun})`);
  }

  check(key: string): boolean {
    const now = Date.now();
    const bucket = this.buckets.get(key) ?? {
      tokens: this.config.maxRequests,
      lastRefill: now
    };

    if (now - bucket.lastRefill >= this.config.windowMs) {
      bucket.tokens = this.config.maxRequests;
      bucket.lastRefill = now;
    }

    this.buckets.set(key, bucket);
    return this.config.dryRun || bucket.tokens > 0;
  }

  consume(key: string): boolean {
    if (!this.check(key)) return false;

    const bucket = this.buckets.get(key);
    if (bucket && !this.config.dryRun) {
      bucket.tokens = Math.max(0, bucket.tokens - 1);
    }

    return true;
  }

  snapshot(): { keys: number; dryRun: boolean; windowMs: number; maxRequests: number } {
    return {
      keys: this.buckets.size,
      dryRun: this.config.dryRun,
      windowMs: this.config.windowMs,
      maxRequests: this.config.maxRequests
    };
  }
}

const isDirectRun = process.argv[1]?.replace(/\\/g, "/").endsWith("agents/rate-limiter.ts");

if (isDirectRun) {
  const limiter = new RateLimiter();
  limiter.init().catch((error) => {
    console.error("[RateLimiter] Init failed", error);
    process.exitCode = 1;
  });
}
