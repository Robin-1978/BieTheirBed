export type ActiveTransportMode = "direct" | "p2p" | "relay";

export function transportLabel(mode: ActiveTransportMode): string {
  if (mode === "p2p") return "WebRTC P2P";
  if (mode === "relay") return "Hub Relay";
  return "Direct 直连";
}

export function transportCompactLabel(mode: ActiveTransportMode): string {
  if (mode === "p2p") return "P2P";
  if (mode === "relay") return "Relay";
  return "Direct";
}

export function transportDetail(mode: ActiveTransportMode): string {
  if (mode === "p2p") return "App 通过 WebRTC DataChannel 与 Node 点对点通信";
  if (mode === "relay") return "App 的请求当前由 Hub Relay 转发到 Node";
  return "App 当前直接访问 Node Gateway，不经过 Hub Relay";
}
