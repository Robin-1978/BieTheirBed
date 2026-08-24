export type AndroidMdnsImplementation = "DNSSD" | "NSD";

export function mdnsImplementationOrder(platform: string): AndroidMdnsImplementation[] {
  // The embedded responder is normally more reliable, but it can fail
  // silently on individual Android/OEM combinations. Native NSD uses a
  // separate platform path and is therefore a useful automatic fallback.
  return platform === "android" ? ["DNSSD", "NSD"] : ["NSD"];
}
