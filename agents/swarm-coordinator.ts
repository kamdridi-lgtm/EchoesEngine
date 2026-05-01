// STATUS: DORMANT / v2.4.0-PREP / NOT ACTIVE
// Purpose: Future swarm routing, heartbeat & load balancing.
// Safe to commit. Exports only. NO auto-execution. NO intervals. NO network calls.
// Activation requires explicit import + manual method calls.

export type AgentRole = "director" | "renderer" | "critic" | "publisher" | "watchdog";

export interface AgentNode {
  id: string;
  role: AgentRole;
  status: "online" | "busy" | "degraded" | "offline";
  lastHeartbeat: string;
  capacity: number;
  currentLoad: number;
}

export class SwarmCoordinator {
  private registry = new Map<string, AgentNode>();
  private stateDir: string;

  constructor(stateDir?: string) {
    this.stateDir = stateDir || "agents/_state";
  }

  /** Initialise le coordinateur en mode dormant. Aucune boucle, aucun I/O. */
  async init(): Promise<void> {
    console.log("[SwarmCoordinator] Initialized (dormant mode)");
  }

  /** Enregistre un noeud dans le registre local. */
  registerAgent(id: string, role: AgentRole, capacity: number = 3): void {
    this.registry.set(id, {
      id,
      role,
      status: "online",
      lastHeartbeat: new Date().toISOString(),
      capacity,
      currentLoad: 0
    });
  }

  /** Met a jour la charge et le statut d'un noeud. */
  heartbeat(id: string, delta: number = 0): void {
    const node = this.registry.get(id);
    if (!node) return;

    node.currentLoad = Math.max(0, Math.min(node.capacity, node.currentLoad + delta));
    node.status = node.currentLoad >= node.capacity ? "busy" : "online";
    node.lastHeartbeat = new Date().toISOString();
  }

  /** Retourne le meilleur noeud disponible pour un role donne, ou null. */
  routeTask(role: AgentRole): AgentNode | null {
    const candidates = Array.from(this.registry.values())
      .filter((node) => node.role === role && node.status !== "offline" && node.currentLoad < node.capacity)
      .sort((a, b) => a.currentLoad - b.currentLoad);

    return candidates[0] || null;
  }

  /** Retourne un instantane lecture-seule du registre actuel. */
  snapshot(): Record<string, AgentNode> {
    return Object.fromEntries(this.registry);
  }
}

const isDirectRun = process.argv[1]?.replace(/\\/g, "/").endsWith("agents/swarm-coordinator.ts");

if (isDirectRun) {
  const coordinator = new SwarmCoordinator();
  coordinator.init().catch((error) => {
    console.error("[SwarmCoordinator] Init failed", error);
    process.exitCode = 1;
  });
}
