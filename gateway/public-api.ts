// STATUS: DORMANT / PHASE4-PREP / NOT ACTIVE
// Purpose: Localhost-only public API facade for future external access.
// Safe to commit. No auto-listen, no intervals, no network calls on import.

import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { RateLimiter } from "../agents/rate-limiter.ts";

type JsonRecord = Record<string, unknown>;

export class PublicApi {
  private server: Server | null = null;
  private readonly limiter: RateLimiter;

  constructor(
    private readonly options: {
      dryRun?: boolean;
      internalGateway?: string;
    } = {}
  ) {
    this.limiter = new RateLimiter({ dryRun: this.dryRun });
  }

  async init(): Promise<void> {
    await this.limiter.init();
    console.log(`[PublicApi] Initialized (dryRun=${this.dryRun})`);
  }

  private get dryRun(): boolean {
    return this.options.dryRun ?? true;
  }

  private get internalGateway(): string {
    return this.options.internalGateway ?? "http://127.0.0.1:3000";
  }

  async listen(port = 4000): Promise<void> {
    if (this.dryRun) {
      console.log(`[PublicApi] DryRun: would listen on 127.0.0.1:${port}`);
      return;
    }

    if (this.server) return;

    this.server = createServer((request, response) => {
      this.handle(request, response).catch((error) => {
        this.writeJson(response, 500, { error: "internal_error", message: String(error) });
      });
    });

    await new Promise<void>((resolve) => {
      this.server?.listen(port, "127.0.0.1", resolve);
    });

    console.log(`[PublicApi] Listening on 127.0.0.1:${port}`);
  }

  async close(): Promise<void> {
    if (!this.server) return;

    await new Promise<void>((resolve, reject) => {
      this.server?.close((error) => error ? reject(error) : resolve());
    });

    this.server = null;
  }

  snapshot(): { dryRun: boolean; internalGateway: string; limiter: ReturnType<RateLimiter["snapshot"]> } {
    return {
      dryRun: this.dryRun,
      internalGateway: this.internalGateway,
      limiter: this.limiter.snapshot()
    };
  }

  private async handle(request: IncomingMessage, response: ServerResponse): Promise<void> {
    const url = new URL(request.url ?? "/", "http://127.0.0.1");

    if (request.method === "GET" && url.pathname === "/v1/health") {
      this.writeJson(response, 200, { status: "ready", dryRun: this.dryRun });
      return;
    }

    const key = String(request.headers["x-api-key"] ?? request.socket.remoteAddress ?? "anonymous");
    if (!this.limiter.consume(key)) {
      this.writeJson(response, 429, { error: "rate_limit_exceeded" });
      return;
    }

    if (request.method === "POST" && url.pathname === "/v1/generate") {
      const body = await this.readJson(request);
      const result = await this.proxyGenerate(body, key);
      this.writeJson(response, result.status, result.body);
      return;
    }

    this.writeJson(response, 404, { error: "not_found" });
  }

  private async proxyGenerate(body: JsonRecord, apiKey: string): Promise<{ status: number; body: unknown }> {
    try {
      const response = await fetch(`${this.internalGateway}/generate`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Echoes-Key": apiKey
        },
        body: JSON.stringify(body)
      });

      const payload = await response.json().catch(() => ({ ok: response.ok }));
      return { status: response.status, body: payload };
    } catch {
      return { status: 502, body: { error: "internal_gateway_unreachable" } };
    }
  }

  private async readJson(request: IncomingMessage): Promise<JsonRecord> {
    const chunks: Buffer[] = [];
    for await (const chunk of request) {
      chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
    }

    const raw = Buffer.concat(chunks).toString("utf-8");
    if (!raw.trim()) return {};

    try {
      return JSON.parse(raw) as JsonRecord;
    } catch {
      return {};
    }
  }

  private writeJson(response: ServerResponse, status: number, body: unknown): void {
    response.writeHead(status, {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "*"
    });
    response.end(JSON.stringify(body));
  }
}

const isDirectRun = process.argv[1]?.replace(/\\/g, "/").endsWith("gateway/public-api.ts");

if (isDirectRun) {
  const api = new PublicApi();
  api.init().catch((error) => {
    console.error("[PublicApi] Init failed", error);
    process.exitCode = 1;
  });
}
