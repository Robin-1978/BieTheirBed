import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { GatewayClient } from "@/api/gatewayClient";
import type { PrincipalTaskEvent } from "@/api/models";
import { authenticateDevice, pairDevice } from "@/security/pairing";
import {
  clearSession,
  loadCoreSession,
  loadDevice,
  loadSessionToken,
  storeCoreSession,
} from "@/security/deviceIdentity";

type GatewayState = {
  status: "booting" | "unpaired" | "ready" | "error";
  client: GatewayClient | null;
  sessionHandle: string;
  gatewayUrl: string;
  sessionToken: string;
  latestEvent: PrincipalTaskEvent | null;
  error: string;
  pair(encoded: string, displayName: string): Promise<void>;
  publish(event: PrincipalTaskEvent): void;
  reconnect(): Promise<void>;
};

const Context = createContext<GatewayState | null>(null);

export function GatewayProvider({ children }: React.PropsWithChildren) {
  const [state, setState] = useState<Omit<GatewayState, "pair" | "publish" | "reconnect">>({
    status: "booting",
    client: null,
    sessionHandle: "",
    gatewayUrl: "",
    sessionToken: "",
    latestEvent: null,
    error: "",
  });

  const connect = useCallback(async () => {
    setState((current) => ({ ...current, status: "booting", error: "" }));
    try {
      const device = await loadDevice();
      if (!device) {
        setState((current) => ({ ...current, status: "unpaired" }));
        return;
      }
      let token = await loadSessionToken();
      let client: GatewayClient;
      if (token) {
        client = new GatewayClient(device.gatewayUrl, token);
        try {
          await client.gatewaySession();
        } catch {
          await clearSession();
          token = null;
          client = await authenticateDevice({
            gateway_url: device.gatewayUrl,
            deviceId: device.deviceId,
          });
          token = await loadSessionToken();
        }
      } else {
        client = await authenticateDevice({
          gateway_url: device.gatewayUrl,
          deviceId: device.deviceId,
        });
        token = await loadSessionToken();
      }
      if (!token) throw new Error("未能建立安全会话");
      let sessionHandle = await loadCoreSession();
      if (!sessionHandle) {
        sessionHandle = await client.createSession();
        await storeCoreSession(sessionHandle);
      }
      setState((current) => ({
        ...current,
        status: "ready",
        client,
        sessionHandle,
        gatewayUrl: device.gatewayUrl,
        sessionToken: token,
      }));
    } catch (error) {
      setState((current) => ({
        ...current,
        status: "error",
        error: error instanceof Error ? error.message : "连接失败",
      }));
    }
  }, []);

  useEffect(() => {
    void connect();
  }, [connect]);

  const pair = useCallback(async (encoded: string, displayName: string) => {
    await pairDevice(encoded, displayName);
    await connect();
  }, [connect]);

  const publish = useCallback((event: PrincipalTaskEvent) => {
    setState((current) => ({ ...current, latestEvent: event }));
  }, []);

  const value = useMemo<GatewayState>(
    () => ({ ...state, pair, publish, reconnect: connect }),
    [connect, pair, publish, state],
  );
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useGateway(): GatewayState {
  const value = useContext(Context);
  if (!value) throw new Error("useGateway must be used within GatewayProvider");
  return value;
}
