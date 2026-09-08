export type PreferredTransport = "direct" | "p2p" | "relay";

export function preferredTransport(input: {
  lanReady: boolean;
  p2pReady: boolean;
  relayReady: boolean;
}): PreferredTransport {
  if (input.lanReady) return "direct";
  if (input.p2pReady) return "p2p";
  if (input.relayReady) return "relay";
  return "direct";
}

export function bindingUsesHubEndpoint(
  binding: { gatewayUrl: string },
  hub: { url: string; rootUrl: string },
): boolean {
  const bindingUrl = normalizedEndpoint(binding.gatewayUrl);
  return bindingUrl === normalizedEndpoint(hub.rootUrl)
    || bindingUrl === normalizedEndpoint(hub.url);
}

export function p2pOfferHeaders(input?: HeadersInit): Headers {
  const headers = new Headers(input);
  headers.set("Content-Type", "application/json");
  return headers;
}

export function isPrivateNetworkUrl(rawUrl: string): boolean {
  if (!rawUrl || typeof rawUrl !== "string") return false;
  try {
    const url = new URL(rawUrl);
    const host = url.hostname.toLowerCase();
    if (host === "localhost" || host === "127.0.0.1" || host === "::1") return true;
    if (host.startsWith("10.")) return true;
    if (host.startsWith("192.168.")) return true;
    if (/^172\.(1[6-9]|2[0-9]|3[0-1])\./.test(host)) return true;
    return false;
  } catch {
    return false;
  }
}

function normalizedEndpoint(value: string): string {
  const url = new URL(value);
  const path = url.pathname.replace(/\/+$/, "");
  return `${url.protocol}//${url.host}${path}`;
}
