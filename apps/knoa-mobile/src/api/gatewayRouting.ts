export function bindingUsesHubEndpoint(
  binding: { gatewayUrl: string },
  hub: { url: string; rootUrl: string },
): boolean {
  const bindingUrl = normalizedEndpoint(binding.gatewayUrl);
  return bindingUrl === normalizedEndpoint(hub.rootUrl)
    || bindingUrl === normalizedEndpoint(hub.url);
}

function normalizedEndpoint(value: string): string {
  const url = new URL(value);
  const path = url.pathname.replace(/\/+$/, "");
  return `${url.protocol}//${url.host}${path}`;
}
