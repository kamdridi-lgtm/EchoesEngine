// STATUS: DORMANT / v2.4.0-PREP / NOT ACTIVE
// Purpose: Future telemetry & metrics bridge (OTel/Prometheus ready).
// Safe to commit. Exports only. NO auto-execution. NO intervals. NO network calls.
// Activation requires explicit import + manual wiring to OTel SDK.

import { randomUUID } from "node:crypto";

export type MetricType = "counter" | "gauge" | "histogram";

export interface TraceContext {
  id: string;
  name: string;
  startTime: number;
  endTime?: number;
  tags?: Record<string, string>;
}

export interface MetricRecord {
  name: string;
  type: MetricType;
  value: number;
  tags?: Record<string, string>;
  timestamp: number;
}

export class TelemetryBridge {
  private metrics: MetricRecord[] = [];
  private traces: Map<string, TraceContext> = new Map();

  /** Initialise le bridge en mode dormant. Aucune connexion, aucun export. */
  async init(): Promise<void> {
    console.log("[TelemetryBridge] Initialized (dormant mode)");
  }

  /** Enregistre une metrique en memoire locale. Aucun I/O. */
  recordMetric(name: string, value: number, type: MetricType = "gauge", tags?: Record<string, string>): void {
    this.metrics.push({ name, type, value, tags, timestamp: Date.now() });
  }

  /** Demarre un span de tracing local. Retourne un ID. */
  startSpan(name: string, tags?: Record<string, string>): string {
    const id = randomUUID();
    this.traces.set(id, { id, name, startTime: Date.now(), tags });
    return id;
  }

  /** Cloture un span local. */
  endSpan(spanId: string): void {
    const span = this.traces.get(spanId);
    if (span) span.endTime = Date.now();
  }

  /** Retourne un instantane lecture-seule des metriques & traces actives. */
  snapshot(): { metrics: MetricRecord[]; activeTraces: number } {
    return {
      metrics: [...this.metrics],
      activeTraces: [...this.traces.values()].filter((trace) => !trace.endTime).length
    };
  }
}

const isDirectRun = process.argv[1]?.replace(/\\/g, "/").endsWith("agents/telemetry-bridge.ts");

if (isDirectRun) {
  const bridge = new TelemetryBridge();
  bridge.init().catch((error) => {
    console.error("[TelemetryBridge] Init failed", error);
    process.exitCode = 1;
  });
}
