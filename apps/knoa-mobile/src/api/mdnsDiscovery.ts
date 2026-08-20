import { Platform } from "react-native";
import Zeroconf from "react-native-zeroconf";

import type { NodeDeviceBinding } from "@/security/deviceIdentity";

export const KNOA_MDNS_SERVICE_TYPE = "_knoa-node";

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

export function parseMdnsService(
  service: ZeroconfService,
  expectedNodeId?: string,
): MdnsNodeEndpoint | null {
  const txt = service.txt ?? {};
  const nodeId = String(txt.node_id ?? "").trim();
  const signingPublicKey = String(txt.signing_key ?? "").trim();
  if (!nodeId || (expectedNodeId && nodeId !== expectedNodeId) || !signingPublicKey) return null;
  const host = (service.addresses ?? []).find((value) => /^\d{1,3}(?:\.\d{1,3}){3}$/.test(value))
    ?? service.host?.replace(/\.$/, "");
  const port = Number(service.port);
  if (!host || !Number.isInteger(port) || port <= 0 || port > 65535) return null;
  return { nodeId, signingPublicKey, url: `http://${host}:${port}` };
}

export async function discoverNodeOnLan(
  binding: Pick<NodeDeviceBinding, "nodeId" | "nodeSigningPublicKey">,
  timeoutMs = 1800,
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
      const endpoint = parseMdnsService(raw as ZeroconfService, binding.nodeId);
      if (!endpoint || endpoint.signingPublicKey !== binding.nodeSigningPublicKey) return;
      void (async () => {
        const controller = new AbortController();
        const abortTimer = setTimeout(() => controller.abort(), 1200);
        try {
          const response = await fetch(`${endpoint.url}/health`, {
            signal: controller.signal,
          });
          if (!response.ok) return;
          const descriptor = await response.json() as { node_id?: string };
          if (descriptor.node_id === binding.nodeId) {
            clearTimeout(timer);
            finish(endpoint.url);
          }
        } catch {
          // A stale or isolated mDNS announcement is ignored.
        } finally {
          clearTimeout(abortTimer);
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
