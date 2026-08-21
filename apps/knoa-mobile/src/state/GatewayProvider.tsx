import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

import { GatewayClient, GatewayError } from "@/api/gatewayClient";
import { ConnectionResolverTransport, type LanDiagnostic, type P2PDiagnostic } from "@/api/gatewayTransport";
import type { AgentSummary, AndroidRelease, PrincipalTaskEvent, UnavailableAgent } from "@/api/models";
import { isPresentationTaskEvent, subscribeTaskEvents, type TaskEventSubscription } from "@/api/taskEvents";
import { authenticateDevice, pairDevice } from "@/security/pairing";
import {
  clearSession,
  clearConnectionIdentity,
  deselectNode,
  loadConnectionIdentity,
  loadSessionToken,
  listNodeBindings,
  selectNode,
  storeCoreSession,
  type NodeDeviceBinding,
} from "@/security/deviceIdentity";
import { withAuthenticationRetry } from "./authenticationRecovery";
import { resolveAndroidRelease } from "@/hub/hubClient";
import { installedAndroidVersionCode, isAndroidUpdateAvailable } from "@/update/androidUpdater";
import { requiresAndroidUpdate } from "@/update/releasePolicy";

type GatewayConnection = { gatewayUrl: string; token: string };

type GatewayState = {
  status: "booting" | "selecting" | "unpaired" | "ready" | "error";
  client: GatewayClient | null;
  sessionHandle: string;
  gatewayUrl: string;
  sessionToken: string;
  latestEvent: PrincipalTaskEvent | null;
  error: string;
  deviceId: string;
  nodeId: string;
  nodes: NodeDeviceBinding[];
  lastConnectedAt: number;
  transportMode: "direct" | "p2p" | "relay";
  p2pState: P2PDiagnostic["state"];
  p2pLastError: string;
  p2pRetryAt: number;
  lanState: LanDiagnostic["state"];
  lanLastError: string;
  lanRetryAt: number;
  lanEndpoint: string;
  requiredUpdate: AndroidRelease | null;
  availableUpdate: AndroidRelease | null;
  agents: AgentSummary[];
  unavailableAgents: UnavailableAgent[];
  defaultAgentId: string;
  selectedAgentId: string;
  activeAgentId: string;
  selectAgent(agentId: string): void;
  pair(encoded: string, displayName: string): Promise<void>;
  reconnect(): Promise<void>;
  reauthenticate(): Promise<void>;
  removeConnection(): Promise<void>;
  disconnectNode(): Promise<void>;
  switchNode(nodeId: string): Promise<void>;
  newConversation(agentId?: string): Promise<void>;
  ensureConversation(): Promise<string>;
  commitConversation(sessionHandle: string): Promise<void>;
  openConversation(sessionHandle: string): Promise<void>;
  connection(): GatewayConnection | null;
  runAuthenticated<T>(operation: (client: GatewayClient) => Promise<T>): Promise<T>;
  subscribeEvents(listener: (event: PrincipalTaskEvent) => void): () => void;
};

const Context = createContext<GatewayState | null>(null);

export function GatewayProvider({ children }: React.PropsWithChildren) {
  type StoredState = Omit<GatewayState, "pair" | "reconnect" | "reauthenticate" | "removeConnection" | "disconnectNode" | "switchNode" | "newConversation" | "ensureConversation" | "commitConversation" | "openConversation" | "connection" | "runAuthenticated" | "subscribeEvents" | "selectAgent">;
  const initialState: StoredState = {
    status: "booting",
    client: null,
    sessionHandle: "",
    gatewayUrl: "",
    sessionToken: "",
    latestEvent: null,
    error: "",
    deviceId: "",
    nodeId: "",
    nodes: [],
    lastConnectedAt: 0,
    transportMode: "direct",
    p2pState: "idle",
    p2pLastError: "",
    p2pRetryAt: 0,
    lanState: "idle",
    lanLastError: "",
    lanRetryAt: 0,
    lanEndpoint: "",
    requiredUpdate: null,
    availableUpdate: null,
    agents: [],
    unavailableAgents: [],
    defaultAgentId: "knoa",
    selectedAgentId: "knoa",
    activeAgentId: "",
  };
  const [state, setState] = useState<StoredState>(initialState);
  const stateRef = useRef<StoredState>(initialState);
  const connectionRef = useRef<GatewayConnection | null>(null);
  const connectionGenerationRef = useRef(0);
  const authenticationRef = useRef<{
    generation: number;
    promise: Promise<GatewayClient>;
  } | null>(null);
  const provisionalConversationRef = useRef<Promise<string> | null>(null);
  const eventListenersRef = useRef(new Set<(event: PrincipalTaskEvent) => void>());

  const commit = useCallback((patch: Partial<StoredState>) => {
    const next = { ...stateRef.current, ...patch };
    stateRef.current = next;
    setState(next);
  }, []);

  const connect = useCallback(async () => {
    const generation = ++connectionGenerationRef.current;
    provisionalConversationRef.current = null;
    commit({ status: "booting", error: "", p2pState: "idle", p2pLastError: "", p2pRetryAt: 0, lanState: "idle", lanLastError: "", lanRetryAt: 0, lanEndpoint: "" });
    try {
      const [identity, nodes] = await Promise.all([
        loadConnectionIdentity(),
        listNodeBindings(),
      ]);
      if (generation !== connectionGenerationRef.current) return;
      if (!identity) {
        connectionRef.current = null;
        commit({ status: nodes.length ? "selecting" : "unpaired", client: null, gatewayUrl: "", sessionToken: "", sessionHandle: "", deviceId: "", nodeId: "", nodes, lastConnectedAt: 0, requiredUpdate: null, availableUpdate: null, agents: [], unavailableAgents: [], activeAgentId: "", selectedAgentId: "knoa" });
        return;
      }
      const device = { deviceId: identity.deviceId, gatewayUrl: identity.gatewayUrl };
      const transportChanged = (transportMode: "direct" | "p2p" | "relay") => {
        if (generation === connectionGenerationRef.current) commit({ transportMode });
      };
      const p2pDiagnosticChanged = (diagnostic: P2PDiagnostic) => {
        if (generation === connectionGenerationRef.current) commit({
          p2pState: diagnostic.state,
          p2pLastError: diagnostic.lastError,
          p2pRetryAt: diagnostic.retryAt,
        });
      };
      const lanDiagnosticChanged = (diagnostic: LanDiagnostic) => {
        if (generation === connectionGenerationRef.current) commit({
          lanState: diagnostic.state,
          lanLastError: diagnostic.lastError,
          lanRetryAt: diagnostic.retryAt,
          lanEndpoint: diagnostic.endpoint ?? "",
        });
      };
      let token = identity.sessionToken
        && identity.sessionExpiresAt > Date.now() / 1000 + 30
        ? identity.sessionToken
        : null;
      const transport = new ConnectionResolverTransport(
        identity,
        transportChanged,
        p2pDiagnosticChanged,
        lanDiagnosticChanged,
      );
      await transport.prepareLanDiscovery();
      let client: GatewayClient;
      if (token) {
        client = new GatewayClient(device.gatewayUrl, token, transport);
        try {
          await client.gatewaySession();
        } catch (error) {
          if (!(error instanceof GatewayError) || error.status !== 401) throw error;
          await clearSession();
          token = null;
          client = await authenticateDevice({
            gateway_url: device.gatewayUrl,
            deviceId: device.deviceId,
            binding: identity,
          }, transportChanged, p2pDiagnosticChanged, lanDiagnosticChanged, transport);
          token = await loadSessionToken();
        }
      } else {
        client = await authenticateDevice({
          gateway_url: device.gatewayUrl,
          deviceId: device.deviceId,
          binding: identity,
        }, transportChanged, p2pDiagnosticChanged, lanDiagnosticChanged, transport);
        token = await loadSessionToken();
      }
      if (!token) throw new Error("未能建立安全会话");
      if (generation !== connectionGenerationRef.current) return;
      const sessionHandle = identity.coreSessionHandle || "";
      connectionRef.current = { gatewayUrl: identity.gatewayUrl, token };
      commit({
        status: "ready",
        client,
        sessionHandle,
        gatewayUrl: device.gatewayUrl,
        sessionToken: token,
        deviceId: device.deviceId,
        nodeId: identity.nodeId,
        nodes,
        lastConnectedAt: Date.now() / 1000,
        transportMode: client.transportMode(),
        activeAgentId: "",
      });
      if (sessionHandle) {
        void client.getConversationSession(sessionHandle).then((session) => {
          if (generation !== connectionGenerationRef.current
            || stateRef.current.sessionHandle !== sessionHandle) return;
          commit({ activeAgentId: session.agent_id, selectedAgentId: session.agent_id });
        }).catch(() => {
          if (generation !== connectionGenerationRef.current
            || stateRef.current.sessionHandle !== sessionHandle) return;
          commit({ sessionHandle: "", activeAgentId: "" });
          void storeCoreSession("").catch(() => undefined);
        });
      }
      void client.listAgents().then(({ defaultAgentId, agents }) => {
        if (generation !== connectionGenerationRef.current) return;
        const active = stateRef.current.activeAgentId;
        commit({
          agents,
          defaultAgentId,
          selectedAgentId: active || (agents.some((item) => item.agent_id === stateRef.current.selectedAgentId)
            ? stateRef.current.selectedAgentId
            : defaultAgentId),
        });
      }).catch(() => undefined);
      void client.listAgentAvailability().then((unavailableAgents) => {
        if (generation === connectionGenerationRef.current) commit({ unavailableAgents });
      }).catch(() => undefined);
      void resolveAndroidRelease(() => client.latestAndroidRelease())
        .then((release) => {
          if (generation !== connectionGenerationRef.current) return;
          commit({
            requiredUpdate: release && requiresAndroidUpdate(release, installedAndroidVersionCode()) ? release : null,
            availableUpdate: release && isAndroidUpdateAvailable(release, installedAndroidVersionCode()) ? release : null,
          });
        })
        .catch(() => undefined);
    } catch (error) {
      if (generation !== connectionGenerationRef.current) return;
      commit({
        status: "error",
        error: error instanceof Error ? error.message : "连接失败",
      });
    }
  }, [commit]);

  const refreshAuthentication = useCallback(async (): Promise<GatewayClient> => {
    const generation = connectionGenerationRef.current;
    if (authenticationRef.current?.generation === generation) {
      return authenticationRef.current.promise;
    }
    const pending = (async () => {
      const identity = await loadConnectionIdentity();
      if (!identity) throw new Error("设备尚未配对");
      const transportChanged = (transportMode: "direct" | "p2p" | "relay") => {
        if (generation === connectionGenerationRef.current) commit({ transportMode });
      };
      const p2pDiagnosticChanged = (diagnostic: P2PDiagnostic) => {
        if (generation === connectionGenerationRef.current) commit({
          p2pState: diagnostic.state,
          p2pLastError: diagnostic.lastError,
          p2pRetryAt: diagnostic.retryAt,
        });
      };
      const lanDiagnosticChanged = (diagnostic: LanDiagnostic) => {
        if (generation === connectionGenerationRef.current) commit({
          lanState: diagnostic.state,
          lanLastError: diagnostic.lastError,
          lanRetryAt: diagnostic.retryAt,
          lanEndpoint: diagnostic.endpoint ?? "",
        });
      };
      const transport = new ConnectionResolverTransport(
        identity,
        transportChanged,
        p2pDiagnosticChanged,
        lanDiagnosticChanged,
      );
      await transport.prepareLanDiscovery();
      const client = await authenticateDevice({
        gateway_url: identity.gatewayUrl,
        deviceId: identity.deviceId,
        binding: identity,
      }, transportChanged, p2pDiagnosticChanged, lanDiagnosticChanged, transport);
      const token = await loadSessionToken();
      if (!token) throw new Error("未能恢复安全会话");
      if (generation !== connectionGenerationRef.current) throw new Error("Node 连接已切换");
      connectionRef.current = { gatewayUrl: identity.gatewayUrl, token };
      commit({
        status: "ready",
        client,
        gatewayUrl: identity.gatewayUrl,
        sessionToken: token,
        error: "",
        transportMode: client.transportMode(),
      });
      void client.listAgents().then(({ defaultAgentId, agents }) => {
        if (generation !== connectionGenerationRef.current) return;
        commit({
          agents,
          defaultAgentId,
          selectedAgentId: stateRef.current.activeAgentId || stateRef.current.selectedAgentId || defaultAgentId,
        });
      }).catch(() => undefined);
      void client.listAgentAvailability().then((unavailableAgents) => {
        if (generation === connectionGenerationRef.current) commit({ unavailableAgents });
      }).catch(() => undefined);
      void resolveAndroidRelease(() => client.latestAndroidRelease())
        .then((release) => {
          if (generation !== connectionGenerationRef.current) return;
          commit({
            requiredUpdate: release && requiresAndroidUpdate(release, installedAndroidVersionCode()) ? release : null,
            availableUpdate: release && isAndroidUpdateAvailable(release, installedAndroidVersionCode()) ? release : null,
          });
        })
        .catch(() => undefined);
      return client;
    })();
    authenticationRef.current = { generation, promise: pending };
    try {
      return await pending;
    } catch (error) {
      if (generation === connectionGenerationRef.current) {
        commit({
          status: "error",
          error: error instanceof Error ? error.message : "认证失败",
        });
      }
      throw error;
    } finally {
      if (authenticationRef.current?.promise === pending) {
        authenticationRef.current = null;
      }
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
    let active = true;
    void listNodeBindings().then((nodes) => {
      if (!active) return;
      connectionRef.current = null;
      commit({
        status: "selecting",
        nodes,
        client: null,
        gatewayUrl: "",
        sessionToken: "",
        sessionHandle: "",
        deviceId: "",
        nodeId: "",
        lastConnectedAt: 0,
        error: "",
      });
    }).catch((error) => {
      if (!active) return;
      commit({
        status: "error",
        error: error instanceof Error ? error.message : "无法读取 Node 绑定",
      });
    });
    return () => {
      active = false;
    };
  }, [commit]);

  useEffect(() => {
    let subscription: TaskEventSubscription | null = null;
    let cancelled = false;
    if (state.status === "ready" && state.client) {
      void subscribeTaskEvents({
        client: state.client,
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
    connectionGenerationRef.current += 1;
    provisionalConversationRef.current = null;
    await pairDevice(encoded, displayName);
    connectionRef.current = null;
    commit({ latestEvent: null, client: null, sessionHandle: "", sessionToken: "" });
    await connect();
  }, [commit, connect]);

  const reauthenticate = useCallback(async () => {
    connectionGenerationRef.current += 1;
    provisionalConversationRef.current = null;
    await clearSession();
    connectionRef.current = null;
    commit({ client: null, sessionToken: "", status: "booting", error: "" });
    await connect();
  }, [commit, connect]);

  const removeConnection = useCallback(async () => {
    connectionGenerationRef.current += 1;
    provisionalConversationRef.current = null;
    const client = stateRef.current.client;
    if (client) await client.revokeCurrentDevice();
    await clearConnectionIdentity();
    connectionRef.current = null;
    commit({ client: null, sessionHandle: "", sessionToken: "", latestEvent: null, status: "booting" });
    await connect();
  }, [commit, connect]);

  const disconnectNode = useCallback(async () => {
    connectionGenerationRef.current += 1;
    provisionalConversationRef.current = null;
    stateRef.current.client?.close();
    await deselectNode();
    const nodes = await listNodeBindings();
    connectionRef.current = null;
    commit({
      status: nodes.length ? "selecting" : "unpaired",
      client: null,
      sessionHandle: "",
      sessionToken: "",
      latestEvent: null,
      gatewayUrl: "",
      deviceId: "",
      nodeId: "",
      nodes,
      lastConnectedAt: 0,
      error: "",
      requiredUpdate: null,
      availableUpdate: null,
      agents: [],
      unavailableAgents: [],
      activeAgentId: "",
      selectedAgentId: "knoa",
      p2pState: "idle",
      p2pLastError: "",
      p2pRetryAt: 0,
      lanState: "idle",
      lanLastError: "",
      lanRetryAt: 0,
      lanEndpoint: "",
    });
  }, [commit]);

  const switchNode = useCallback(async (nodeId: string) => {
    if (nodeId === stateRef.current.nodeId && stateRef.current.status === "ready") return;
    connectionGenerationRef.current += 1;
    provisionalConversationRef.current = null;
    stateRef.current.client?.close();
    connectionRef.current = null;
    commit({ client: null, sessionHandle: "", sessionToken: "", latestEvent: null, status: "booting", nodeId, error: "" });
    await selectNode(nodeId);
    await connect();
  }, [commit, connect]);

  const newConversation = useCallback(async (agentId?: string) => {
    provisionalConversationRef.current = null;
    await storeCoreSession("");
    const selected = agentId && stateRef.current.agents.some((agent) => agent.agent_id === agentId)
      ? agentId
      : stateRef.current.defaultAgentId;
    commit({
      sessionHandle: "",
      latestEvent: null,
      activeAgentId: "",
      selectedAgentId: selected,
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
      disconnectNode,
      switchNode,
      newConversation,
      ensureConversation,
      commitConversation,
      openConversation,
      connection,
      runAuthenticated,
      subscribeEvents,
      selectAgent,
    }),
    [commitConversation, connect, connection, disconnectNode, ensureConversation, newConversation, openConversation, pair, reauthenticate, removeConnection, runAuthenticated, selectAgent, state, subscribeEvents, switchNode],
  );
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useGateway(): GatewayState {
  const value = useContext(Context);
  if (!value) throw new Error("useGateway must be used within GatewayProvider");
  return value;
}
