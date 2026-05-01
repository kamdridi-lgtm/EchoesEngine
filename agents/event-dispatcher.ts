// STATUS: DORMANT / v2.4.0-PREP / NOT ACTIVE
// Purpose: Format & queue job lifecycle events for external webhooks/agents.
// Safe to commit. Exports only. NO auto-execution. NO network calls on import.
// Activation requires explicit method invocation + manual webhook config.

export type JobEventType =
  | "submitted"
  | "analysis"
  | "rendering"
  | "encoding"
  | "finished"
  | "failed"
  | "qc_passed"
  | "qc_failed";

export interface JobEvent {
  jobId: string;
  type: JobEventType;
  timestamp: string;
  metadata?: Record<string, unknown>;
}

export interface WebhookTarget {
  name: string;
  url: string;
  enabled: boolean;
}

export class EventDispatcher {
  private queue: JobEvent[] = [];
  private targets: WebhookTarget[] = [];
  private dryRun: boolean;

  constructor(dryRun: boolean = true) {
    this.dryRun = dryRun;
  }

  /** Initialise le dispatcher en mode dormant. Aucune connexion, aucun export. */
  async init(): Promise<void> {
    console.log("[EventDispatcher] Initialized (dormant mode)");
  }

  /** Enregistre un webhook cible. Aucun appel reseau. */
  registerTarget(name: string, url: string, enabled: boolean = false): void {
    this.targets.push({ name, url, enabled });
  }

  /** Ajoute un evenement en file locale. Aucun auto-dispatch. */
  enqueue(event: JobEvent): void {
    this.queue.push(event);
  }

  /** Formate un evenement pour payload webhook standard. */
  formatPayload(event: JobEvent): Record<string, unknown> {
    return {
      source: "EchoesEngine",
      event: event.type,
      job_id: event.jobId,
      ts: event.timestamp,
      data: event.metadata || {}
    };
  }

  /** Dispatch simule (dormant). Ne fait aucun fetch reel tant que non active. */
  async dispatch(event: JobEvent): Promise<{ queued: number; dispatched: number; dryRun: boolean }> {
    const activeTargets = this.targets.filter((target) => target.enabled);

    if (this.dryRun) {
      console.log(
        `[EventDispatcher] DryRun: Would dispatch "${event.type}" for ${event.jobId} to ${activeTargets.length} target(s)`
      );
      return { queued: this.queue.length, dispatched: 0, dryRun: true };
    }

    // Placeholder structure pour future integration fetch/axios.
    // Aucun appel reseau ici en mode dormant.
    return { queued: this.queue.length, dispatched: activeTargets.length, dryRun: false };
  }

  /** Retourne un instantane lecture-seule de la file & des cibles. */
  snapshot(): { queueSize: number; targets: number; dryRun: boolean } {
    return {
      queueSize: this.queue.length,
      targets: this.targets.length,
      dryRun: this.dryRun
    };
  }
}

export const Dispatcher = EventDispatcher;

// -- Execution directe uniquement via `pnpm dlx tsx agents/event-dispatcher.ts` --
const isDirectRun = process.argv[1]?.replace(/\\/g, "/").endsWith("agents/event-dispatcher.ts");
if (isDirectRun) {
  const dispatcher = new EventDispatcher(true);
  await dispatcher.init();
  // Mode dormant : sortie propre immediate. Aucune boucle, aucun reseau.
  process.exit(0);
}
