export type ActiveTransportMode = "direct" | "p2p" | "relay";

export function transportLabelKey(mode: ActiveTransportMode) {
  if (mode === "p2p") return "transport.p2p" as const;
  if (mode === "relay") return "transport.relay" as const;
  return "transport.direct" as const;
}

export function transportCompactLabelKey(mode: ActiveTransportMode) {
  if (mode === "p2p") return "transport.compact.p2p" as const;
  if (mode === "relay") return "transport.compact.relay" as const;
  return "transport.compact.direct" as const;
}

export function transportDetailKey(mode: ActiveTransportMode) {
  if (mode === "p2p") return "transport.detail.p2p" as const;
  if (mode === "relay") return "transport.detail.relay" as const;
  return "transport.detail.direct" as const;
}
