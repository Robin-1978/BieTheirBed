export type TransportProbeMode = "direct" | "p2p" | "relay";

export type TransportProbe = {
  at: number;
  mode: TransportProbeMode;
  path: string;
  durationMs: number;
  ok: boolean;
};

export type TransportProbeSummary = {
  total: number;
  failed: number;
  averageMs: number;
  byMode: Record<TransportProbeMode, { count: number; failed: number }>;
};

const RECENT_LIMIT = 60;
let recent: TransportProbe[] = [];

/**
 * Process-local record of which transport actually carried each business
 * request and how long it took. "Relay connected" must never be confused
 * with "Relay carried this request".
 */
export function recordTransportProbe(probe: TransportProbe): void {
  recent.push(probe);
  if (recent.length > RECENT_LIMIT) recent = recent.slice(recent.length - RECENT_LIMIT);
}

export function recentTransportProbes(): readonly TransportProbe[] {
  return recent;
}

export function clearTransportDiagnostics(): void {
  recent = [];
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
