import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

import { GatewayClient, GatewayError } from "@/api/gatewayClient";
import type { AgentSummary, AndroidRelease, PrincipalTaskEvent } from "@/api/models";
import { isPresentationTaskEvent, subscribeTaskEvents, type TaskEventSubscription } from "@/api/taskEvents";
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
  agents: AgentSummary[];
  defaultAgentId: string;
  selectedAgentId: string;
  activeAgentId: string;
  selectAgent(agentId: string): void;
  pair(encoded: string, displayName: string): Promise<void>;
  reconnect(): Promise<void>;
  reauthenticate(): Promise<void>;
  removeConnection(): Promise<void>;
  newConversation(): Promise<void>;
  ensureConversation(): Promise<string>;
  commitConversation(sessionHandle: string): Promise<void>;
  openConversation(sessionHandle: string): Promise<void>;
  connection(): GatewayConnection | null;
  runAuthenticated<T>(operation: (client: GatewayClient) => Promise<T>): Promise<T>;
  subscribeEvents(listener: (event: PrincipalTaskEvent) => void): () => void;
};

const Context = createContext<GatewayState | null>(null);

export function GatewayProvider({ children }: React.PropsWithChildren) {
  type StoredState = Omit<GatewayState, "pair" | "reconnect" | "reauthenticate" | "removeConnection" | "newConversation" | "ensureConversation" | "commitConversation" | "openConversation" | "connection" | "runAuthenticated" | "subscribeEvents" | "selectAgent">;
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
    agents: [],
    defaultAgentId: "knoa",
    selectedAgentId: "knoa",
    activeAgentId: "",
  };
  const [state, setState] = useState<StoredState>(initialState);
  const stateRef = useRef<StoredState>(initialState);
  const connectionRef = useRef<GatewayConnection | null>(null);
  const authenticationRef = useRef<Promise<GatewayClient> | null>(null);
  const provisionalConversationRef = useRef<Promise<string> | null>(null);
  const eventListenersRef = useRef(new Set<(event: PrincipalTaskEvent) => void>());

  const commit = useCallback((patch: Partial<StoredState>) => {
    const next = { ...stateRef.current, ...patch };
    stateRef.current = next;
    setState(next);
  }, []);

  const connect = useCallback(async () => {
    provisionalConversationRef.current = null;
    commit({ status: "booting", error: "" });
    try {
      const identity = await loadConnectionIdentity();
      if (!identity) {
        connectionRef.current = null;
        commit({ status: "unpaired", client: null, gatewayUrl: "", sessionToken: "", sessionHandle: "", deviceId: "", lastConnectedAt: 0, requiredUpdate: null, availableUpdate: null, agents: [], activeAgentId: "", selectedAgentId: "knoa" });
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
      let activeAgentId = "";
      if (sessionHandle) {
        try {
          activeAgentId = (await client.getConversationSession(sessionHandle)).agent_id;
        } catch {
          activeAgentId = "";
        }
      }
      connectionRef.current = { gatewayUrl: device.gatewayUrl, token };
      commit({
        status: "ready",
        client,
        sessionHandle,
        gatewayUrl: device.gatewayUrl,
        sessionToken: token,
        deviceId: device.deviceId,
        lastConnectedAt: Date.now() / 1000,
        activeAgentId,
        selectedAgentId: activeAgentId || stateRef.current.selectedAgentId,
      });
      void client.listAgents().then(({ defaultAgentId, agents }) => {
        const active = stateRef.current.activeAgentId;
        commit({
          agents,
          defaultAgentId,
          selectedAgentId: active || (agents.some((item) => item.agent_id === stateRef.current.selectedAgentId)
            ? stateRef.current.selectedAgentId
            : defaultAgentId),
        });
      }).catch(() => undefined);
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
  }, [commit]);

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
      void client.listAgents().then(({ defaultAgentId, agents }) => commit({
        agents,
        defaultAgentId,
        selectedAgentId: stateRef.current.activeAgentId || stateRef.current.selectedAgentId || defaultAgentId,
      })).catch(() => undefined);
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
  }, [commit]);

  const runAuthenticated = useCallback(<T,>(
    operation: (client: GatewayClient) => Promise<T>,
  ): Promise<T> => {
    const client = stateRef.current.client;
    if (!client) return Promise.reject(new Error("小诺尚未连接"));
    return withAuthenticationRetry(client, refreshAuthentication, operation);
  }, [refreshAuthentication]);

  const connection = useCallback((): GatewayConnection | null => connectionRef.current, []);

  const subscribeEvents = useCallback((listener: (event: PrincipalTaskEvent) => void) => {
    eventListenersRef.current.add(listener);
    return () => eventListenersRef.current.delete(listener);
  }, []);

  useEffect(() => {
    void connect();
  }, [connect]);

  useEffect(() => {
    let subscription: TaskEventSubscription | null = null;
    let cancelled = false;
    if (state.status === "ready") {
      void subscribeTaskEvents({
        gatewayUrl: state.gatewayUrl,
        token: state.sessionToken,
        onEvent: (event) => {
          if (isPresentationTaskEvent(event.event.event_type)) {
            commit({ latestEvent: event });
          }
          for (const listener of eventListenersRef.current) {
            try {
              listener(event);
            } catch {
              // One presentation listener must not interrupt the shared event feed.
            }
          }
        },
        onError: () => {
          void runAuthenticated((client) => client.gatewaySession()).catch(() => undefined);
        },
      }).then((active) => {
        if (cancelled) active.close();
        else subscription = active;
      });
    }
    return () => {
      cancelled = true;
      subscription?.close();
    };
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
      agents: [],
      activeAgentId: "",
      selectedAgentId: "knoa",
    });
  }, [commit]);

  const newConversation = useCallback(async () => {
    provisionalConversationRef.current = null;
    await storeCoreSession("");
    commit({
      sessionHandle: "",
      latestEvent: null,
      activeAgentId: "",
      selectedAgentId: stateRef.current.defaultAgentId,
    });
  }, [commit]);

  const ensureConversation = useCallback(async (): Promise<string> => {
    const current = stateRef.current.sessionHandle;
    if (current) return current;
    if (!provisionalConversationRef.current) {
      provisionalConversationRef.current = runAuthenticated(
        (client) => client.createSession(stateRef.current.selectedAgentId),
      ).then((sessionHandle) => {
        commit({ activeAgentId: stateRef.current.selectedAgentId });
        return sessionHandle;
      }).catch((error) => {
        provisionalConversationRef.current = null;
        throw error;
      });
    }
    return provisionalConversationRef.current;
  }, [commit, runAuthenticated]);

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
    commit({ sessionHandle, latestEvent: null, activeAgentId: session.agent_id, selectedAgentId: session.agent_id });
  }, [commit, runAuthenticated]);

  const selectAgent = useCallback((agentId: string) => {
    if (stateRef.current.activeAgentId || stateRef.current.sessionHandle) return;
    if (!stateRef.current.agents.some((agent) => agent.agent_id === agentId)) return;
    commit({ selectedAgentId: agentId });
  }, [commit]);

  const value = useMemo<GatewayState>(
    () => ({
      ...state,
      pair,
      reconnect: connect,
      reauthenticate,
      removeConnection,
      newConversation,
      ensureConversation,
      commitConversation,
      openConversation,
      connection,
      runAuthenticated,
      subscribeEvents,
      selectAgent,
    }),
    [commitConversation, connect, connection, ensureConversation, newConversation, openConversation, pair, reauthenticate, removeConnection, runAuthenticated, selectAgent, state, subscribeEvents],
  );
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useGateway(): GatewayState {
  const value = useContext(Context);
  if (!value) throw new Error("useGateway must be used within GatewayProvider");
  return value;
}
