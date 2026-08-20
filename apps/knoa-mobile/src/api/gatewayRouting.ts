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

function normalizedEndpoint(value: string): string {
  const url = new URL(value);
  const path = url.pathname.replace(/\/+$/, "");
  return `${url.protocol}//${url.host}${path}`;
}
