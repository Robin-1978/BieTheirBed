import { Platform } from "react-native";
import Zeroconf from "react-native-zeroconf";

import type { NodeDeviceBinding } from "@/security/deviceIdentity";

// react-native-zeroconf expects the bare service name and adds the leading
// underscore plus `._tcp` itself on Android and iOS.
export const KNOA_MDNS_SERVICE_TYPE = "knoa-node";
export const MDNS_DISCOVERY_TIMEOUT_MS = 2_500;
export const MDNS_HEALTH_TIMEOUT_MS = 1_200;

export type MdnsNodeEndpoint = {
  nodeId: string;
  url: string;
  signingPublicKey: string;
};

type ZeroconfService = {
  name?: string;
  host?: string;
  port?: number;
  addresses?: string[];
  txt?: Record<string, unknown>;
};

const IPV4_PATTERN = /^\d{1,3}(?:\.\d{1,3}){3}$/;

export function listMdnsHostAddresses(service: ZeroconfService): string[] {
  const fromAddresses = (service.addresses ?? []).filter((value) => IPV4_PATTERN.test(value));
  if (fromAddresses.length) return fromAddresses;
  const host = service.host?.replace(/\.$/, "") ?? "";
  return IPV4_PATTERN.test(host) ? [host] : [];
}

export function parseMdnsService(
  service: ZeroconfService,
  expectedNodeId?: string,
): MdnsNodeEndpoint | null {
  const txt = service.txt ?? {};
  const nodeId = String(txt.node_id ?? "").trim();
  const signingPublicKey = String(txt.signing_key ?? "").trim();
  if (!nodeId || (expectedNodeId && nodeId !== expectedNodeId) || !signingPublicKey) return null;
  const hosts = listMdnsHostAddresses(service);
  const port = Number(service.port);
  if (!hosts.length || !Number.isInteger(port) || port <= 0 || port > 65535) return null;
  return { nodeId, signingPublicKey, url: `http://${hosts[0]}:${port}` };
}

async function verifyLanEndpoint(
  url: string,
  nodeId: string,
): Promise<boolean> {
  const controller = new AbortController();
  const abortTimer = setTimeout(() => controller.abort(), MDNS_HEALTH_TIMEOUT_MS);
  try {
    const response = await fetch(`${url}/health`, { signal: controller.signal });
    if (!response.ok) return false;
    const descriptor = await response.json() as { node_id?: string };
    return descriptor.node_id === nodeId;
  } catch {
    return false;
  } finally {
    clearTimeout(abortTimer);
  }
}

export async function discoverNodeOnLan(
  binding: Pick<NodeDeviceBinding, "nodeId" | "nodeSigningPublicKey">,
  timeoutMs = MDNS_DISCOVERY_TIMEOUT_MS,
): Promise<string | null> {
  if (Platform.OS === "web") return null;
  const zeroconf = new Zeroconf();
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value: string | null) => {
      if (settled) return;
      settled = true;
      try { zeroconf.stop("DNSSD"); } catch { /* optional native implementation */ }
      resolve(value);
    };
    const timer = setTimeout(() => finish(null), timeoutMs);
    zeroconf.on("resolved", (raw) => {
      const service = raw as ZeroconfService;
      const txt = service.txt ?? {};
      const nodeId = String(txt.node_id ?? "").trim();
      const signingPublicKey = String(txt.signing_key ?? "").trim();
      if (nodeId !== binding.nodeId || signingPublicKey !== binding.nodeSigningPublicKey) return;
      const hosts = listMdnsHostAddresses(service);
      const port = Number(service.port);
      if (!hosts.length || !Number.isInteger(port) || port <= 0 || port > 65535) return;
      void (async () => {
        for (const host of hosts) {
          const url = `http://${host}:${port}`;
          if (await verifyLanEndpoint(url, binding.nodeId)) {
            clearTimeout(timer);
            finish(url);
            return;
          }
        }
      })();
    });
    zeroconf.on("error", () => { clearTimeout(timer); finish(null); });
    try {
      zeroconf.scan(KNOA_MDNS_SERVICE_TYPE, "tcp", "local.", "DNSSD");
    } catch {
      clearTimeout(timer);
      finish(null);
    }
  });
}
