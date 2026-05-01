// STATUS: DORMANT / PHASE4-PREP / NOT ACTIVE
// Purpose: Localhost-first GPU worker discovery and health aggregation.
// Safe to commit. No auto-polling, no intervals, no network calls on import.

export type NodeStatus = "online" | "degraded" | "offline";

export interface NodeInfo {
  id: string;
  url: string;
  status: NodeStatus;
  vramGb: number;
  lastSeen: string;
}

export class NodeDiscovery {
  private nodes: NodeInfo[];

  constructor(
    initialUrls: string[] = ["http://127.0.0.1:8081"],
    private readonly dryRun = true
  ) {
    this.nodes = initialUrls.map((url, index) => ({
      id: `node-${index}`,
      url,
      status: "offline",
      vramGb: 0,
      lastSeen: new Date().toISOString()
    }));
  }

  async init(): Promise<void> {
    console.log(`[NodeDiscovery] Initialized (dryRun=${this.dryRun})`);
  }

  async refresh(): Promise<NodeInfo[]> {
    if (this.dryRun) {
      console.log(`[NodeDiscovery] DryRun: would poll ${this.nodes.length} node(s)`);
      return this.snapshotNodes();
    }

    for (const node of this.nodes) {
      try {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 2_000);
        const response = await fetch(`${node.url}/health`, { signal: controller.signal });
        clearTimeout(timeout);

        if (!response.ok) {
          node.status = "offline";
          continue;
        }

        const data = await response.json() as { vram_gb?: number; status?: string };
        node.vramGb = Number(data.vram_gb ?? 0);
        node.status = data.status === "ready" ? "online" : "degraded";
        node.lastSeen = new Date().toISOString();
      } catch {
        node.status = "offline";
      }
    }

    return this.snapshotNodes();
  }

  getBest(): NodeInfo | null {
    return this.nodes
      .filter((node) => node.status === "online")
      .sort((a, b) => a.vramGb - b.vramGb)[0] ?? null;
  }

  snapshot(): { total: number; online: number; dryRun: boolean } {
    return {
      total: this.nodes.length,
      online: this.nodes.filter((node) => node.status === "online").length,
      dryRun: this.dryRun
    };
  }

  snapshotNodes(): NodeInfo[] {
    return this.nodes.map((node) => ({ ...node }));
  }
}

const isDirectRun = process.argv[1]?.replace(/\\/g, "/").endsWith("agents/node-discovery.ts");

if (isDirectRun) {
  const discovery = new NodeDiscovery();
  discovery.init().catch((error) => {
    console.error("[NodeDiscovery] Init failed", error);
    process.exitCode = 1;
  });
}
