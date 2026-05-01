// STATUS: DORMANT / v2.4.0-PREP / NOT ACTIVE
// Purpose: Future metric-driven job submission & monitoring.
// Safe to commit. NO auto-execution. NO intervals. NO network calls on import.
// Activation requires explicit import + manual method calls.

import { randomUUID } from "node:crypto";

type TrackedJob = {
  id: string;
  status: string;
  style: string;
  submittedAt: string;
  attempts?: number;
};

export class AdaptiveOrchestrator {
  private jobs = new Map<string, TrackedJob>();
  private maxConcurrent = 3;
  private apiBase = process.env.ECHOES_API_URL ?? "http://127.0.0.1:8080";

  /** Initialise l'orchestrateur en mode dormant. Aucune boucle, aucun I/O. */
  async init(): Promise<void> {
    console.log("[AdaptiveOrchestrator] Initialized (dormant mode)");
  }

  /** Soumet un job uniquement si la capacite le permet. Aucune execution automatique. */
  async submitIfCapacity(style: string, prompt: string, audioPath: string): Promise<string | null> {
    const active = [...this.jobs.values()].filter((job) => !["FINISHED", "FAILED"].includes(job.status)).length;
    if (active >= this.maxConcurrent) return null;

    const id = randomUUID();

    try {
      const response = await fetch(`${this.apiBase}/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ jobId: id, style, prompt, audio: audioPath })
      });

      if (!response.ok) return null;

      this.jobs.set(id, {
        id,
        status: "SUBMITTED",
        style,
        submittedAt: new Date().toISOString()
      });

      return id;
    } catch {
      return null;
    }
  }

  /** Verifie l'etat des jobs en memoire. Aucun polling automatique. */
  async monitorJobs(): Promise<void> {
    for (const [id, job] of this.jobs) {
      if (["FINISHED", "FAILED"].includes(job.status)) continue;

      try {
        const response = await fetch(`${this.apiBase}/status/${id}`);

        if (!response.ok) {
          job.attempts = (job.attempts || 0) + 1;
          if (job.attempts > 10) job.status = "FAILED";
          continue;
        }

        const data = await response.json() as { status?: string };
        job.status = data.status || job.status;
      } catch {
        // Dormant safety: explicit monitor calls should not throw on temporary network failures.
      }
    }
  }

  /** Retourne un instantane lecture-seule des jobs suivis. */
  snapshot(): Record<string, TrackedJob> {
    return Object.fromEntries(this.jobs);
  }
}

const isDirectRun = process.argv[1]?.replace(/\\/g, "/").endsWith("agents/adaptive-orchestrator.ts");

if (isDirectRun) {
  const orchestrator = new AdaptiveOrchestrator();
  orchestrator.init().catch((error) => {
    console.error("[AdaptiveOrchestrator] Init failed", error);
    process.exitCode = 1;
  });
}
