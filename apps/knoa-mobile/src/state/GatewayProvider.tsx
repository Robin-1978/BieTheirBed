import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

import { GatewayClient, GatewayError } from "@/api/gatewayClient";
import type { AndroidRelease, PrincipalTaskEvent } from "@/api/models";
import { subscribeTaskEvents, type TaskEventSubscription } from "@/api/taskEvents";
import {
  registerPush,
  type PushRegistrationResult,
} from "@/notifications";
import { authenticateDevice, pairDevice } from "@/security/pairing";
import {
  clearSession,
  clearConnectionIdentity,
  loadConnectionIdentity,
  loadCoreSession,
  loadDevice,
  loadSessionToken,
  storeCoreSession,
} from "@/security/deviceIdentity";
import { withAuthenticationRetry } from "./authenticationRecovery";
import { installedAndroidVersionCode, isAndroidUpdateAvailable } from "@/update/androidUpdater";
import { requiresAndroidUpdate } from "@/update/releasePolicy";

type GatewayConnection = { gatewayUrl: string; token: string };

type GatewayState = {
  status: "booting" | "unpaired" | "ready" | "error";
  client: GatewayClient | null;
  sessionHandle: string;
  gatewayUrl: string;
  sessionToken: string;
  latestEvent: PrincipalTaskEvent | null;
  error: string;
  deviceId: string;
  lastConnectedAt: number;
  requiredUpdate: AndroidRelease | null;
  availableUpdate: AndroidRelease | null;
  pushRegistration: PushRegistrationResult;
  pair(encoded: string, displayName: string): Promise<void>;
  publish(event: PrincipalTaskEvent): void;
  reconnect(): Promise<void>;
  reauthenticate(): Promise<void>;
  removeConnection(): Promise<void>;
  newConversation(): Promise<void>;
  ensureConversation(): Promise<string>;
  commitConversation(sessionHandle: string): Promise<void>;
  openConversation(sessionHandle: string): Promise<void>;
  connection(): GatewayConnection | null;
  runAuthenticated<T>(operation: (client: GatewayClient) => Promise<T>): Promise<T>;
  registerNotifications(requestPermission?: boolean): Promise<PushRegistrationResult>;
  testPush(): Promise<void>;
};

const Context = createContext<GatewayState | null>(null);

export function GatewayProvider({ children }: React.PropsWithChildren) {
  type StoredState = Omit<GatewayState, "pair" | "publish" | "reconnect" | "reauthenticate" | "removeConnection" | "newConversation" | "ensureConversation" | "commitConversation" | "openConversation" | "connection" | "runAuthenticated" | "registerNotifications" | "testPush">;
  const initialState: StoredState = {
    status: "booting",
    client: null,
    sessionHandle: "",
    gatewayUrl: "",
    sessionToken: "",
    latestEvent: null,
    error: "",
    deviceId: "",
    lastConnectedAt: 0,
    requiredUpdate: null,
    availableUpdate: null,
    pushRegistration: { status: "checking", detail: "" },
  };
  const [state, setState] = useState<StoredState>(initialState);
  const stateRef = useRef<StoredState>(initialState);
  const connectionRef = useRef<GatewayConnection | null>(null);
  const authenticationRef = useRef<Promise<GatewayClient> | null>(null);
  const provisionalConversationRef = useRef<Promise<string> | null>(null);

  const commit = useCallback((patch: Partial<StoredState>) => {
    const next = { ...stateRef.current, ...patch };
    stateRef.current = next;
    setState(next);
  }, []);

  const registerNotifications = useCallback(async (
    requestPermission = false,
  ): Promise<PushRegistrationResult> => {
    const client = stateRef.current.client;
    if (!client) {
      const result: PushRegistrationResult = {
        status: "server_failed",
        detail: "Knoa is not connected",
      };
      commit({ pushRegistration: result });
      return result;
    }
    commit({ pushRegistration: { status: "checking", detail: "" } });
    const result = await registerPush(client, requestPermission);
    commit({ pushRegistration: result });
    return result;
  }, [commit]);

  const connect = useCallback(async () => {
    provisionalConversationRef.current = null;
    commit({
      status: "booting",
      error: "",
      pushRegistration: { status: "checking", detail: "" },
    });
    try {
      const identity = await loadConnectionIdentity();
      if (!identity) {
        connectionRef.current = null;
        commit({ status: "unpaired", client: null, gatewayUrl: "", sessionToken: "", sessionHandle: "", deviceId: "", lastConnectedAt: 0, requiredUpdate: null, availableUpdate: null });
        return;
      }
      const device = { deviceId: identity.deviceId, gatewayUrl: identity.gatewayUrl };
      let token = await loadSessionToken();
      let client: GatewayClient;
      if (token) {
        client = new GatewayClient(device.gatewayUrl, token);
        try {
          await client.gatewaySession();
        } catch (error) {
          if (!(error instanceof GatewayError) || error.status !== 401) throw error;
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
      const sessionHandle = (await loadCoreSession()) ?? "";
      connectionRef.current = { gatewayUrl: device.gatewayUrl, token };
      commit({
        status: "ready",
        client,
        sessionHandle,
        gatewayUrl: device.gatewayUrl,
        sessionToken: token,
        deviceId: device.deviceId,
        lastConnectedAt: Date.now() / 1000,
      });
      void registerNotifications();
      void client.latestAndroidRelease()
        .then((release) => commit({
          requiredUpdate: requiresAndroidUpdate(release, installedAndroidVersionCode()) ? release : null,
          availableUpdate: isAndroidUpdateAvailable(release, installedAndroidVersionCode()) ? release : null,
        }))
        .catch(() => undefined);
    } catch (error) {
      commit({
        status: "error",
        error: error instanceof Error ? error.message : "连接失败",
      });
    }
  }, [commit, registerNotifications]);

  const refreshAuthentication = useCallback(async (): Promise<GatewayClient> => {
    if (authenticationRef.current) return authenticationRef.current;
    const pending = (async () => {
      const device = await loadDevice();
      if (!device) throw new Error("设备尚未配对");
      const client = await authenticateDevice({
        gateway_url: device.gatewayUrl,
        deviceId: device.deviceId,
      });
      const token = await loadSessionToken();
      if (!token) throw new Error("未能恢复安全会话");
      connectionRef.current = { gatewayUrl: device.gatewayUrl, token };
      commit({
        status: "ready",
        client,
        gatewayUrl: device.gatewayUrl,
        sessionToken: token,
        error: "",
      });
      void registerNotifications();
      void client.latestAndroidRelease()
        .then((release) => commit({
          requiredUpdate: requiresAndroidUpdate(release, installedAndroidVersionCode()) ? release : null,
          availableUpdate: isAndroidUpdateAvailable(release, installedAndroidVersionCode()) ? release : null,
        }))
        .catch(() => undefined);
      return client;
    })();
    authenticationRef.current = pending;
    try {
      return await pending;
    } catch (error) {
      commit({
        status: "error",
        error: error instanceof Error ? error.message : "认证失败",
      });
      throw error;
    } finally {
      authenticationRef.current = null;
    }
  }, [commit, registerNotifications]);

  const runAuthenticated = useCallback(<T,>(
    operation: (client: GatewayClient) => Promise<T>,
  ): Promise<T> => {
    const client = stateRef.current.client;
    if (!client) return Promise.reject(new Error("小诺尚未连接"));
    return withAuthenticationRetry(client, refreshAuthentication, operation);
  }, [refreshAuthentication]);

  const connection = useCallback((): GatewayConnection | null => connectionRef.current, []);

  const testPush = useCallback(async (): Promise<void> => {
    await runAuthenticated((client) => client.testPush());
  }, [runAuthenticated]);

  useEffect(() => {
    void connect();
  }, [connect]);

  useEffect(() => {
    let subscription: TaskEventSubscription | null = null;
    if (state.status === "ready") {
      void subscribeTaskEvents({
        gatewayUrl: state.gatewayUrl,
        token: state.sessionToken,
        onEvent: (event) => {
          commit({ latestEvent: event });
        },
        onError: () => {
          void runAuthenticated((client) => client.gatewaySession()).catch(() => undefined);
        },
      }).then((active) => {
        subscription = active;
      });
    }
    return () => subscription?.close();
  }, [commit, runAuthenticated, state.gatewayUrl, state.sessionToken, state.status]);

  const pair = useCallback(async (encoded: string, displayName: string) => {
    provisionalConversationRef.current = null;
    await pairDevice(encoded, displayName);
    connectionRef.current = null;
    commit({ latestEvent: null, client: null, sessionHandle: "", sessionToken: "" });
    await connect();
  }, [commit, connect]);

  const reauthenticate = useCallback(async () => {
    provisionalConversationRef.current = null;
    await clearSession();
    connectionRef.current = null;
    commit({ client: null, sessionToken: "", status: "booting", error: "" });
    await connect();
  }, [commit, connect]);

  const removeConnection = useCallback(async () => {
    provisionalConversationRef.current = null;
    const client = stateRef.current.client;
    if (client) await client.revokeCurrentDevice();
    await clearConnectionIdentity();
    connectionRef.current = null;
    commit({
      status: "unpaired",
      client: null,
      sessionHandle: "",
      gatewayUrl: "",
      sessionToken: "",
      latestEvent: null,
      error: "",
      deviceId: "",
      lastConnectedAt: 0,
      requiredUpdate: null,
      availableUpdate: null,
      pushRegistration: { status: "checking", detail: "" },
    });
  }, [commit]);

  const newConversation = useCallback(async () => {
    provisionalConversationRef.current = null;
    await storeCoreSession("");
    commit({
      sessionHandle: "",
      latestEvent: null,
    });
  }, [commit]);

  const ensureConversation = useCallback(async (): Promise<string> => {
    const current = stateRef.current.sessionHandle;
    if (current) return current;
    if (!provisionalConversationRef.current) {
      provisionalConversationRef.current = runAuthenticated(
        (client) => client.createSession(),
      ).catch((error) => {
        provisionalConversationRef.current = null;
        throw error;
      });
    }
    return provisionalConversationRef.current;
  }, [runAuthenticated]);

  const commitConversation = useCallback(async (sessionHandle: string) => {
    if (!sessionHandle) throw new Error("会话尚未创建");
    await storeCoreSession(sessionHandle);
    provisionalConversationRef.current = null;
    commit({ sessionHandle, latestEvent: null });
  }, [commit]);

  const openConversation = useCallback(async (sessionHandle: string) => {
    provisionalConversationRef.current = null;
    const session = await runAuthenticated((client) => client.getConversationSession(sessionHandle));
    if (session.state === "archived") throw new Error("请先恢复已归档的会话");
    await storeCoreSession(sessionHandle);
    commit({ sessionHandle, latestEvent: null });
  }, [commit, runAuthenticated]);

  const publish = useCallback((event: PrincipalTaskEvent) => {
    commit({ latestEvent: event });
  }, [commit]);

  const value = useMemo<GatewayState>(
    () => ({
      ...state,
      pair,
      publish,
      reconnect: connect,
      reauthenticate,
      removeConnection,
      newConversation,
      ensureConversation,
      commitConversation,
      openConversation,
      connection,
      runAuthenticated,
      registerNotifications,
      testPush,
    }),
    [commitConversation, connect, connection, ensureConversation, newConversation, openConversation, pair, publish, reauthenticate, registerNotifications, removeConnection, runAuthenticated, state, testPush],
  );
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useGateway(): GatewayState {
  const value = useContext(Context);
  if (!value) throw new Error("useGateway must be used within GatewayProvider");
  return value;
}
