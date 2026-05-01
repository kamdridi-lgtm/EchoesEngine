// STATUS: DORMANT / PHASE4-PREP / NOT ACTIVE
// Purpose: LoRA version discovery and manual hot-swap signaling.
// Safe to commit. No auto-training, no polling, no network calls on import.

import { readdir, readFile, stat } from "node:fs/promises";
import { join } from "node:path";

export interface LoraVersion {
  id: string;
  path: string;
  trainedAt: string;
  datasetSize: number;
  active: boolean;
}

export class LoraManager {
  private versions: LoraVersion[] = [];

  constructor(
    private readonly options: {
      loraDir?: string;
      datasetDir?: string;
      workerUrl?: string;
      dryRun?: boolean;
    } = {}
  ) {}

  async init(): Promise<void> {
    console.log(`[LoraManager] Initialized (dryRun=${this.dryRun})`);
  }

  private get loraDir(): string {
    return this.options.loraDir ?? "models/lora";
  }

  private get datasetDir(): string {
    return this.options.datasetDir ?? "training_dataset";
  }

  private get workerUrl(): string {
    return this.options.workerUrl ?? "http://127.0.0.1:8081";
  }

  private get dryRun(): boolean {
    return this.options.dryRun ?? true;
  }

  async scanVersions(): Promise<LoraVersion[]> {
    this.versions = [];
    const files = await readdir(this.loraDir).catch(() => []);

    for (const file of files.filter((name) => name.endsWith(".safetensors"))) {
      const path = join(this.loraDir, file);
      const info = await stat(path);
      this.versions.push({
        id: file.replace(/\.safetensors$/, ""),
        path,
        trainedAt: info.mtime.toISOString(),
        datasetSize: 0,
        active: false
      });
    }

    return [...this.versions];
  }

  async checkDatasetThreshold(threshold = 50): Promise<{ ready: boolean; count: number }> {
    const files = await readdir(this.datasetDir).catch(() => []);
    let count = 0;

    for (const file of files.filter((name) => name.endsWith(".jsonl"))) {
      const content = await readFile(join(this.datasetDir, file), "utf-8").catch(() => "");
      count += content.split(/\r?\n/).filter((line) => line.trim()).length;
    }

    return { ready: count >= threshold, count };
  }

  async signalHotSwap(versionId: string): Promise<{ ok: boolean; dryRun: boolean; status?: number }> {
    const loraPath = join(this.loraDir, `${versionId}.safetensors`);

    if (this.dryRun) {
      console.log(`[LoraManager] DryRun: would signal hot-swap ${versionId} -> ${this.workerUrl}`);
      return { ok: true, dryRun: true };
    }

    try {
      const response = await fetch(`${this.workerUrl}/reload_lora`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lora_path: loraPath, version: versionId })
      });

      return { ok: response.ok, dryRun: false, status: response.status };
    } catch {
      return { ok: false, dryRun: false, status: 502 };
    }
  }

  snapshot(): { versions: number; dryRun: boolean; loraDir: string; datasetDir: string } {
    return {
      versions: this.versions.length,
      dryRun: this.dryRun,
      loraDir: this.loraDir,
      datasetDir: this.datasetDir
    };
  }
}

const isDirectRun = process.argv[1]?.replace(/\\/g, "/").endsWith("agents/lora-manager.ts");

if (isDirectRun) {
  const manager = new LoraManager();
  manager.init().catch((error) => {
    console.error("[LoraManager] Init failed", error);
    process.exitCode = 1;
  });
}
