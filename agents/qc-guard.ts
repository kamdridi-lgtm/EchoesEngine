// STATUS: DORMANT / v2.4.0-PREP / NOT ACTIVE
// Purpose: Post-render quality control (black frames, audio clipping, corruption, duration).
// Safe to commit. Exports only. NO auto-execution. NO FFmpeg calls on import/direct init.
// Activation requires explicit method invocation + manual FFmpeg availability check.

import { execSync } from "node:child_process";

export interface QCResult {
  passed: boolean;
  issues: string[];
  duration_sec: number;
  audio_peak_db: number;
  black_frames: number;
  corruption: boolean;
}

export class QCGuard {
  private ffmpegPath: string;

  constructor(ffmpegPath?: string) {
    this.ffmpegPath = ffmpegPath || process.env.ECHOES_FFMPEG_PATH || "ffmpeg";
  }

  /** Initialise le garde QC en mode dormant. Aucune execution. */
  async init(): Promise<void> {
    console.log("[QCGuard] Initialized (dormant mode)");
  }

  /** Analyse une video uniquement si appelee explicitement. */
  runQC(videoPath: string): QCResult {
    const issues: string[] = [];
    let duration = 0;
    let peakDb = -99;
    let blackFrames = 0;
    let corruption = false;
    const normalizedVideoPath = videoPath.replace(/\\/g, "/").replace(/:/g, "\\:");

    try {
      const probe = execSync(
        `"${this.ffmpegPath}" -v error -select_streams v:0 -show_entries stream=duration -of csv=p=0 "${videoPath}"`,
        { encoding: "utf-8" }
      ).trim();

      duration = parseFloat(probe) || 0;
      if (Number.isNaN(duration) || duration < 0.5) {
        corruption = true;
        issues.push("corrupted_or_empty");
      }

      const black = execSync(
        `"${this.ffmpegPath}" -v error -f lavfi -i "movie='${normalizedVideoPath}',blackdetect=d=0.5:pix_th=0.05" -show_entries tags=lavfi.black_start -of csv=p=0`,
        { encoding: "utf-8" }
      );

      blackFrames = (black.match(/black_start/g) || []).length;
      if (blackFrames > 2) issues.push(`excessive_black_frames:${blackFrames}`);

      const audio = execSync(
        `"${this.ffmpegPath}" -v error -f lavfi -i "amovie='${normalizedVideoPath}',astats=metadata=1:reset=1" -show_entries frame_tags=lavfi.astats.Overall.Peak_level -of csv=p=0`,
        { encoding: "utf-8" }
      );

      const peaks = audio.match(/-?\d+\.\d+/g)?.map(Number) || [];
      peakDb = peaks.length ? Math.max(...peaks) : -99;
      if (peakDb > -1.0) issues.push(`audio_clipping:${peakDb.toFixed(1)}dB`);
    } catch {
      corruption = true;
      issues.push("ffprobe_failed");
    }

    return {
      passed: issues.length === 0 && !corruption,
      issues,
      duration_sec: duration,
      audio_peak_db: peakDb,
      black_frames: blackFrames,
      corruption
    };
  }

  /** Retourne un instantane lecture-seule de la configuration. */
  snapshot(): { ffmpegPath: string } {
    return { ffmpegPath: this.ffmpegPath };
  }
}

// Backward-compatible alias from previous prep.
export const QcGuard = QCGuard;

const isDirectRun = process.argv[1]?.replace(/\\/g, "/").endsWith("agents/qc-guard.ts");

if (isDirectRun) {
  const qc = new QCGuard();
  qc.init().catch((error) => {
    console.error("[QCGuard] Init failed", error);
    process.exitCode = 1;
  });
}
