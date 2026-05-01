// STATUS: DORMANT / v2.4.0-PREP / NOT ACTIVE
// Purpose: Format & queue job notifications for Discord/WhatsApp webhooks.
// Safe to commit. Exports only. NO auto-fetch. NO SDK auto-connect. Dry-run default.
// Activation requires explicit method invocation + manual webhook config.

export interface NotificationPayload {
  jobId: string;
  status: string;
  style?: string;
  message?: string;
  videoPath?: string;
  timestamp?: string;
}

export interface TargetConfig {
  name: string;
  platform: "discord" | "whatsapp";
  webhookUrl: string;
  enabled: boolean;
}

export class NotificationBridge {
  private targets: TargetConfig[] = [];
  private queue: Required<NotificationPayload>[] = [];
  private dryRun: boolean;

  constructor(dryRun: boolean = true) {
    this.dryRun = dryRun;
  }

  /** Initialise le bridge en mode dormant. Aucune connexion, aucun envoi. */
  async init(): Promise<void> {
    console.log("[NotificationBridge] Initialized (dormant mode)");
  }

  /** Enregistre une cible webhook. Aucun appel reseau. */
  registerTarget(config: TargetConfig): void {
    this.targets.push(config);
  }

  /** Ajoute une notification en file locale. Aucun auto-dispatch. */
  queueNotification(payload: NotificationPayload): void {
    this.queue.push(this.normalizePayload(payload));
  }

  /** Formate pour Discord Webhook. */
  formatDiscord(payload: NotificationPayload): Record<string, unknown> {
    const normalized = this.normalizePayload(payload);
    const color = normalized.status === "FINISHED"
      ? 0x00ff9d
      : normalized.status === "FAILED"
        ? 0xff4d4d
        : 0xffcc00;

    return {
      content: "EchoesEngine Job Update",
      embeds: [{
        title: `Job ${normalized.jobId.slice(0, 8)}...`,
        description: normalized.message
          || `Status: **${normalized.status}**${normalized.style ? `\nStyle: ${normalized.style}` : ""}`,
        color,
        footer: normalized.videoPath ? { text: `Archived: ${normalized.videoPath}` } : undefined,
        timestamp: normalized.timestamp
      }]
    };
  }

  /** Formate pour WhatsApp Business Webhook (simplifie). */
  formatWhatsApp(payload: NotificationPayload): Record<string, unknown> {
    const normalized = this.normalizePayload(payload);
    return {
      text: [
        `EchoesEngine Job ${normalized.jobId.slice(0, 8)}...`,
        `Status: ${normalized.status}`,
        normalized.style ? `Style: ${normalized.style}` : "",
        normalized.videoPath ? `File: ${normalized.videoPath}` : ""
      ].filter(Boolean).join("\n"),
      preview_url: false
    };
  }

  /** Dispatch simule (dormant). Ne fait aucun fetch reel tant que non active. */
  async dispatch(payload: NotificationPayload): Promise<{ queued: number; dispatched: number; dryRun: boolean }> {
    const activeTargets = this.targets.filter((target) => target.enabled);
    const normalized = this.normalizePayload(payload);

    if (this.dryRun) {
      console.log(
        `[NotificationBridge] DryRun: Would dispatch "${normalized.status}" for ${normalized.jobId} to ${activeTargets.length} target(s)`
      );
      return { queued: this.queue.length, dispatched: 0, dryRun: true };
    }

    // Placeholder structure pour future integration fetch/axios.
    // Aucun appel reseau ici en mode dormant.
    return { queued: this.queue.length, dispatched: activeTargets.length, dryRun: false };
  }

  /** Retourne un instantane lecture-seule. */
  snapshot(): { queueSize: number; targets: number; dryRun: boolean } {
    return { queueSize: this.queue.length, targets: this.targets.length, dryRun: this.dryRun };
  }

  private normalizePayload(payload: NotificationPayload): Required<NotificationPayload> {
    return {
      jobId: payload.jobId,
      status: payload.status,
      style: payload.style || "",
      message: payload.message || "",
      videoPath: payload.videoPath || "",
      timestamp: payload.timestamp || new Date().toISOString()
    };
  }
}

export const Notifications = NotificationBridge;

// -- Execution directe uniquement via `pnpm dlx tsx agents/notification-bridge.ts` --
const isDirectRun = process.argv[1]?.replace(/\\/g, "/").endsWith("agents/notification-bridge.ts");
if (isDirectRun) {
  const bridge = new NotificationBridge(true);
  await bridge.init();
  // Mode dormant : sortie propre immediate. Aucun SDK, aucun fetch, aucune boucle.
  process.exit(0);
}
