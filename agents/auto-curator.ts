// STATUS: DORMANT / v2.4.0-PREP / NOT ACTIVE
// Purpose: Curate QC-passed renders, prepare dataset JSONL for future LoRA training.
// Safe to commit. Exports only. NO auto-polling. NO file deletion. NO network calls.
// Activation requires explicit method invocation.

import { appendFile, mkdir } from "node:fs/promises";
import { join } from "node:path";

export interface CuratorEntry {
  jobId: string;
  videoPath: string;
  prompt: string;
  style: string;
  qcScore: number;
  timestamp: string;
}

export class AutoCurator {
  private dataset: CuratorEntry[] = [];
  private threshold: number;
  private datasetDir: string;

  constructor(threshold: number = 0.85, datasetDir?: string) {
    this.threshold = threshold;
    this.datasetDir = datasetDir || "training_dataset";
  }

  /** Initialise le curateur en mode dormant. Aucune I/O. */
  async init(): Promise<void> {
    console.log("[AutoCurator] Initialized (dormant mode)");
  }

  /** Evalue un resultat QC et l'ajoute en memoire si le score depasse le seuil. */
  evaluateQC(qcScore: number, jobId: string, videoPath: string, prompt: string, style: string): boolean {
    if (qcScore >= this.threshold) {
      this.dataset.push({
        jobId,
        videoPath,
        prompt,
        style,
        qcScore,
        timestamp: new Date().toISOString()
      });
      return true;
    }

    return false;
  }

  /** Ecrit le dataset en memoire vers un fichier JSONL. Aucun auto-trigger. */
  async prepareDataset(): Promise<{ count: number; path: string }> {
    if (this.dataset.length === 0) return { count: 0, path: "" };

    await mkdir(this.datasetDir, { recursive: true });
    const filePath = join(this.datasetDir, "dataset.jsonl");
    const lines = this.dataset.map((entry) => JSON.stringify({
      file_name: entry.videoPath,
      prompt: entry.prompt,
      style: entry.style,
      qc_score: entry.qcScore,
      ts: entry.timestamp
    }));

    await appendFile(filePath, `${lines.join("\n")}\n`, "utf-8");
    return { count: this.dataset.length, path: filePath };
  }

  /** Retourne un instantane lecture-seule. */
  snapshot(): { threshold: number; queued: number } {
    return { threshold: this.threshold, queued: this.dataset.length };
  }
}

export const Curator = AutoCurator;

// -- Execution directe uniquement via `pnpm dlx tsx agents/auto-curator.ts` --
const isDirectRun = process.argv[1]?.replace(/\\/g, "/").endsWith("agents/auto-curator.ts");
if (isDirectRun) {
  const curator = new AutoCurator();
  await curator.init();
  // Mode dormant : sortie propre immediate. Aucune boucle, aucun dataset genere.
  process.exit(0);
}
