export type TransportProbeMode = "direct" | "p2p" | "relay";
export type DiagnosticTransport = TransportProbeMode | "mdns";
export type TransportDiagnosticStage =
  | "dns" | "tcp" | "tls" | "mdns" | "ice"
  | "relay_ticket" | "relay_socket" | "relay_crypto"
  | "business" | "server" | "render";
export type TransportDiagnosticOutcome = "ok" | "failed" | "unavailable";

export type TransportProbe = {
  at: number;
  mode: TransportProbeMode;
  path: string;
  durationMs: number;
  ok: boolean;
  attemptId?: string;
};

export type TransportDiagnosticEvent = {
  scope: string;
  attemptId: string;
  transport: DiagnosticTransport;
  stage: TransportDiagnosticStage;
  startedAt: number;
  endedAt: number;
  outcome: TransportDiagnosticOutcome;
  reasonCode: string;
  requestId: string;
};

export type TransportSwitchRecord = {
  scope: string;
  at: number;
  from: TransportProbeMode;
  to: TransportProbeMode;
  reasonCode: string;
  attemptId: string;
  failedStage: TransportDiagnosticStage | "";
  nextRequestId: string;
};

export type TransportProbeSummary = {
  total: number;
  failed: number;
  averageMs: number;
  byMode: Record<TransportProbeMode, { count: number; failed: number }>;
};

type PersistedDiagnostics = {
  version: 1;
  scope: string;
  probes: TransportProbe[];
  stages: TransportDiagnosticEvent[];
  switches: TransportSwitchRecord[];
};

const RECENT_LIMIT = 60;
const STAGE_LIMIT = 180;
const SWITCH_LIMIT = 50;
const RETENTION_MS = 7 * 24 * 60 * 60 * 1000;
let scope = "";
let recent: TransportProbe[] = [];
let stages: TransportDiagnosticEvent[] = [];
let switches: TransportSwitchRecord[] = [];
let persistence: Promise<void> = Promise.resolve();

/** Configure bounded persistence for the active Node isolation scope. */
export async function configureTransportDiagnosticScope(nextScope: string): Promise<void> {
  const normalized = safeToken(nextScope, 128);
  if (scope === normalized) return;
  scope = normalized;
  recent = [];
  stages = [];
  switches = [];
  if (!scope) return;
  try {
    const { File, Paths } = await import("expo-file-system");
    const file = new File(Paths.document, diagnosticFilename(scope));
    if (!file.exists) return;
    const parsed = JSON.parse(await file.text()) as Partial<PersistedDiagnostics>;
    if (parsed.version !== 1 || parsed.scope !== scope) return;
    const cutoff = Date.now() - RETENTION_MS;
    recent = (Array.isArray(parsed.probes) ? parsed.probes : [])
      .filter((item): item is TransportProbe => validProbe(item) && item.at >= cutoff)
      .slice(-RECENT_LIMIT);
    stages = (Array.isArray(parsed.stages) ? parsed.stages : [])
      .filter((item): item is TransportDiagnosticEvent => validStage(item) && item.endedAt >= cutoff)
      .slice(-STAGE_LIMIT);
    switches = (Array.isArray(parsed.switches) ? parsed.switches : [])
      .filter((item): item is TransportSwitchRecord => validSwitch(item) && item.at >= cutoff)
      .slice(-SWITCH_LIMIT);
  } catch {
    // Diagnostics must never block a business request.
  }
}

/** Record which transport actually carried one business request. */
export function recordTransportProbe(probe: TransportProbe): void {
  const sanitized = { ...probe, path: safePath(probe.path), attemptId: safeToken(probe.attemptId ?? "", 128) };
  recent.push(sanitized);
  if (recent.length > RECENT_LIMIT) recent = recent.slice(-RECENT_LIMIT);
  schedulePersist();
}

export function recordTransportDiagnostic(event: Omit<TransportDiagnosticEvent, "scope"> & { scope?: string }): void {
  const normalized: TransportDiagnosticEvent = {
    ...event,
    scope: safeToken(event.scope || scope, 128),
    attemptId: safeToken(event.attemptId, 128),
    requestId: safeToken(event.requestId, 128),
    reasonCode: safeToken(event.reasonCode, 128),
    startedAt: Math.max(0, event.startedAt),
    endedAt: Math.max(event.startedAt, event.endedAt),
  };
  stages.push(normalized);
  if (stages.length > STAGE_LIMIT) stages = stages.slice(-STAGE_LIMIT);
  schedulePersist();
}

export function recordTransportSwitch(record: Omit<TransportSwitchRecord, "scope"> & { scope?: string }): void {
  if (record.from === record.to) return;
  switches.push({
    ...record,
    scope: safeToken(record.scope || scope, 128),
    reasonCode: safeToken(record.reasonCode, 128),
    attemptId: safeToken(record.attemptId, 128),
    nextRequestId: safeToken(record.nextRequestId, 128),
  });
  if (switches.length > SWITCH_LIMIT) switches = switches.slice(-SWITCH_LIMIT);
  schedulePersist();
}

export function recentTransportProbes(): readonly TransportProbe[] { return recent; }
export function recentTransportStages(): readonly TransportDiagnosticEvent[] { return stages; }
export function recentTransportSwitches(): readonly TransportSwitchRecord[] { return switches; }

export function clearTransportDiagnostics(): void {
  recent = [];
  stages = [];
  switches = [];
  schedulePersist();
}

export function transportDiagnosticSummaryText(): string {
  return JSON.stringify({
    scope,
    probes: recent.slice(-20),
    stages: stages.slice(-40),
    switches: switches.slice(-20),
  }, null, 2);
}

export function summarizeTransportProbes(probes: readonly TransportProbe[] = recent): TransportProbeSummary {
  const byMode: TransportProbeSummary["byMode"] = {
    direct: { count: 0, failed: 0 },
    p2p: { count: 0, failed: 0 },
    relay: { count: 0, failed: 0 },
  };
  let totalMs = 0;
  for (const probe of probes) {
    byMode[probe.mode].count += 1;
    if (!probe.ok) byMode[probe.mode].failed += 1;
    totalMs += probe.durationMs;
  }
  return {
    total: probes.length,
    failed: probes.reduce((count, probe) => count + (probe.ok ? 0 : 1), 0),
    averageMs: probes.length ? Math.round(totalMs / probes.length) : 0,
    byMode,
  };
}

function schedulePersist(): void {
  if (!scope) return;
  const activeScope = scope;
  const snapshot: PersistedDiagnostics = {
    version: 1,
    scope: activeScope,
    probes: [...recent],
    stages: [...stages],
    switches: [...switches],
  };
  persistence = persistence.then(async () => {
    try {
      const { File, Paths } = await import("expo-file-system");
      const file = new File(Paths.document, diagnosticFilename(activeScope));
      if (!file.exists) file.create({ intermediates: true, overwrite: false });
      file.write(JSON.stringify(snapshot));
    } catch {
      // Best-effort diagnostic persistence.
    }
  });
}

function diagnosticFilename(value: string): string { return `transport-diagnostics-v1-${hash(value)}.json`; }
function safePath(value: string): string { try { return new URL(value, "https://local.invalid").pathname.slice(0, 256); } catch { return "/"; } }
function safeToken(value: string, limit: number): string { return String(value || "").replace(/[^A-Za-z0-9_.:-]/g, "_").slice(0, limit); }
function hash(value: string): string { let result = 2166136261; for (let i = 0; i < value.length; i += 1) { result ^= value.charCodeAt(i); result = Math.imul(result, 16777619); } return (result >>> 0).toString(16).padStart(8, "0"); }
function validProbe(value: unknown): value is TransportProbe { const item = value as Partial<TransportProbe>; return Boolean(item) && typeof item.at === "number" && ["direct", "p2p", "relay"].includes(String(item.mode)) && typeof item.path === "string" && typeof item.durationMs === "number" && typeof item.ok === "boolean"; }
function validStage(value: unknown): value is TransportDiagnosticEvent { const item = value as Partial<TransportDiagnosticEvent>; return Boolean(item) && typeof item.endedAt === "number" && typeof item.stage === "string" && typeof item.outcome === "string"; }
function validSwitch(value: unknown): value is TransportSwitchRecord { const item = value as Partial<TransportSwitchRecord>; return Boolean(item) && typeof item.at === "number" && typeof item.from === "string" && typeof item.to === "string"; }
