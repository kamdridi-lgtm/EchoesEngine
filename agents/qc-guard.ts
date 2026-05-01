// STATUS: DORMANT / v2.4.0-PREP / NOT ACTIVE
// Purpose: Future quality-control gate before archive/notification.
// Safe to commit. Exports only. NO auto-execution. NO intervals. NO filesystem/network writes.
// Activation requires explicit import + manual method calls.

export type QcStatus = "passed" | "warning" | "failed";

export interface QcInput {
  jobId: string;
  videoPath?: string;
  durationSeconds?: number;
  width?: number;
  height?: number;
  fps?: number;
  loudnessLufs?: number;
  peakDb?: number;
  renderErrors?: string[];
}

export interface QcResult {
  jobId: string;
  status: QcStatus;
  score: number;
  issues: string[];
  checkedAt: string;
}

export class QcGuard {
  private minimumDurationSeconds = 1;
  private minimumWidth = 320;
  private minimumHeight = 180;
  private targetLoudnessLufs = -14;
  private loudnessTolerance = 4;

  async init(): Promise<void> {
    console.log("[QcGuard] Initialized (dormant mode)");
  }

  validate(input: QcInput): QcResult {
    const issues: string[] = [];
    let score = 100;

    if (!input.jobId) {
      issues.push("missing_job_id");
      score -= 40;
    }

    if (!input.videoPath) {
      issues.push("missing_video_path");
      score -= 25;
    }

    if ((input.durationSeconds ?? 0) < this.minimumDurationSeconds) {
      issues.push("duration_too_short");
      score -= 20;
    }

    if ((input.width ?? 0) < this.minimumWidth || (input.height ?? 0) < this.minimumHeight) {
      issues.push("resolution_too_low");
      score -= 15;
    }

    if (typeof input.loudnessLufs === "number") {
      const loudnessDelta = Math.abs(input.loudnessLufs - this.targetLoudnessLufs);
      if (loudnessDelta > this.loudnessTolerance) {
        issues.push("loudness_out_of_range");
        score -= 10;
      }
    }

    if (typeof input.peakDb === "number" && input.peakDb > -0.5) {
      issues.push("peak_too_hot");
      score -= 10;
    }

    if (input.renderErrors?.length) {
      issues.push(...input.renderErrors.map((error) => `render_error:${error}`));
      score -= Math.min(40, input.renderErrors.length * 10);
    }

    score = Math.max(0, Math.min(100, score));

    return {
      jobId: input.jobId || "unknown",
      status: score >= 85 ? "passed" : score >= 60 ? "warning" : "failed",
      score,
      issues,
      checkedAt: new Date().toISOString()
    };
  }

  snapshot(): { mode: "dormant"; thresholds: Record<string, number> } {
    return {
      mode: "dormant",
      thresholds: {
        minimumDurationSeconds: this.minimumDurationSeconds,
        minimumWidth: this.minimumWidth,
        minimumHeight: this.minimumHeight,
        targetLoudnessLufs: this.targetLoudnessLufs,
        loudnessTolerance: this.loudnessTolerance
      }
    };
  }
}

const isDirectRun = process.argv[1]?.replace(/\\/g, "/").endsWith("agents/qc-guard.ts");

if (isDirectRun) {
  const guard = new QcGuard();
  guard.init().catch((error) => {
    console.error("[QcGuard] Init failed", error);
    process.exitCode = 1;
  });
}
