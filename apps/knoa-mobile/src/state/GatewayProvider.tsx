import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

import { GatewayClient, GatewayError } from "@/api/gatewayClient";
import { ConnectionResolverTransport, type LanDiagnostic, type P2PDiagnostic, type RelayDiagnostic } from "@/api/gatewayTransport";
import type { PrincipalTaskEvent } from "@/api/models";
import { isPresentationTaskEvent, subscribeTaskEvents, type TaskEventSubscription } from "@/api/taskEvents";
import { authenticateDevice, pairDevice } from "@/security/pairing";
import {
  clearSession,
  clearConnectionIdentity,
  deselectNode,
  loadConnectionIdentity,
  storeNodeDisplayName,
  storeNodeDisplayNames,
  loadSessionToken,
  listNodeBindings,
  selectNode,
  storeCoreSession,
} from "@/security/deviceIdentity";
import { withAuthenticationRetry } from "./authenticationRecovery";
import { listHubNodes, loadHubConnection, resolveAndroidRelease } from "@/hub/hubClient";
import { setCacheIdentity } from "@/storage/cacheScope";
import { clearAppCache } from "@/storage/appCache";
import { clearTaskReminders } from "@/reminders/taskReminders";
import { installedAndroidVersionCode, isAndroidUpdateAvailable } from "@/update/androidUpdater";
import { requiresAndroidUpdate } from "@/update/releasePolicy";
import { createProvisionalConversation, resolveNewConversationAgent } from "./conversationTransition";
import { ConnectionContext, useConnection } from "./connectionContext";
import { FleetContext, useFleet } from "./fleetContext";
import { SessionContext, useSession } from "./sessionContext";
import type { ConnectionState, FleetState, GatewayConnection, GatewayState, SessionState } from "./types";

export * from "./types";
export { useConnection } from "./connectionContext";
export { useFleet } from "./fleetContext";
export { useSession } from "./sessionContext";

const CONNECTION_TIMEOUT_MS = 20_000;

function withConnectionTimeout<T>(promise: Promise<T>, timeoutMs = CONNECTION_TIMEOUT_MS): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("连接超时，请检查 Hub 服务和手机网络")), timeoutMs);
    promise.then(
      (value) => { clearTimeout(timer); resolve(value); },
      (error) => { clearTimeout(timer); reject(error); },
    );
  });
}

const Context = createContext<GatewayState | null>(null);

export function GatewayProvider({ children }: React.PropsWithChildren) {
  type StoredState = Omit<GatewayState, "pair" | "renameNode" | "reconnect" | "reauthenticate" | "removeConnection" | "disconnectNode" | "switchNode" | "newConversation" | "ensureConversation" | "commitConversation" | "openConversation" | "connection" | "runAuthenticated" | "refreshAgents" | "subscribeEvents" | "selectAgent">;
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
    p2pElapsedMs: 0,
    lanState: "idle",
    lanLastError: "",
    lanRetryAt: 0,
    lanEndpoint: "",
    lanElapsedMs: 0,
    relayState: "idle",
    relayLastError: "",
    relayRetryAt: 0,
    relayElapsedMs: 0,
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
    commit({ status: "booting", error: "", p2pState: "idle", p2pLastError: "", p2pRetryAt: 0, p2pElapsedMs: 0, lanState: "idle", lanLastError: "", lanRetryAt: 0, lanEndpoint: "", lanElapsedMs: 0, relayState: "idle", relayLastError: "", relayRetryAt: 0, relayElapsedMs: 0 });
    try {
      const [identity, storedNodes, hubConnection, hubNodes] = await Promise.all([
        loadConnectionIdentity(),
        listNodeBindings(),
        loadHubConnection(),
        listHubNodes().catch(() => []),
      ]);
      if (hubNodes.length) await storeNodeDisplayNames(hubNodes);
      const nodes = hubNodes.length ? await listNodeBindings() : storedNodes;
      if (generation !== connectionGenerationRef.current) return;
      if (!identity) {
        setCacheIdentity("");
        connectionRef.current = null;
        commit({ status: nodes.length ? "selecting" : "unpaired", client: null, gatewayUrl: "", sessionToken: "", sessionHandle: "", deviceId: "", nodeId: "", nodes, lastConnectedAt: 0, requiredUpdate: null, availableUpdate: null, agents: [], unavailableAgents: [], activeAgentId: "", selectedAgentId: "knoa" });
        return;
      }
      // Include the hosted account when available and always include the
      // Node identity.  Cache stores use this boundary for every resource,
      // preventing stale data from another account or paired Node from being
      // rendered after a switch.
      setCacheIdentity(`${hubConnection?.accountId || identity.deviceId}:${identity.nodeId}`);
      const device = { deviceId: identity.deviceId, gatewayUrl: identity.gatewayUrl };
      const transportChanged = (transportMode: "direct" | "p2p" | "relay") => {
        if (generation === connectionGenerationRef.current) commit({ transportMode });
      };
      const p2pDiagnosticChanged = (diagnostic: P2PDiagnostic) => {
        if (generation === connectionGenerationRef.current) commit({
          p2pState: diagnostic.state,
          p2pLastError: diagnostic.lastError,
          p2pRetryAt: diagnostic.retryAt,
          p2pElapsedMs: diagnostic.elapsedMs,
        });
      };
      const lanDiagnosticChanged = (diagnostic: LanDiagnostic) => {
        if (generation === connectionGenerationRef.current) commit({
          lanState: diagnostic.state,
          lanLastError: diagnostic.lastError,
          lanRetryAt: diagnostic.retryAt,
          lanEndpoint: diagnostic.endpoint ?? "",
          lanElapsedMs: diagnostic.elapsedMs,
        });
      };
      const relayDiagnosticChanged = (diagnostic: RelayDiagnostic) => {
        if (generation !== connectionGenerationRef.current) return;
        commit({
          relayState: diagnostic.state,
          relayLastError: diagnostic.lastError,
          relayRetryAt: diagnostic.retryAt,
          relayElapsedMs: diagnostic.elapsedMs,
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
        relayDiagnosticChanged,
      );
      await withConnectionTimeout(transport.prepareLanDiscovery());
      let client: GatewayClient;
      if (token) {
        // Do not spend a separate transport round trip validating a cached
        // token.  The node descriptor request below is authenticated and
        // serves as the validation probe for this connection, avoiding a
        // duplicate Relay/P2P startup on every Node switch.
        client = new GatewayClient(device.gatewayUrl, token, transport);
      } else {
        client = await withConnectionTimeout(authenticateDevice({
          gateway_url: device.gatewayUrl,
          deviceId: device.deviceId,
          binding: identity,
        }, transportChanged, p2pDiagnosticChanged, lanDiagnosticChanged, relayDiagnosticChanged, transport));
        token = await loadSessionToken();
      }
      if (!token) throw new Error("未能建立安全会话");
      if (generation !== connectionGenerationRef.current) return;
      let descriptor: Awaited<ReturnType<GatewayClient["nodeDescriptor"]>>;
      try {
        descriptor = await withConnectionTimeout(client.nodeDescriptor(), 8_000);
      } catch (error) {
        if (!token || !(error instanceof GatewayError) || error.status !== 401) throw error;
        await clearSession();
        client = await withConnectionTimeout(authenticateDevice({
          gateway_url: device.gatewayUrl,
          deviceId: device.deviceId,
          binding: identity,
        }, transportChanged, p2pDiagnosticChanged, lanDiagnosticChanged, relayDiagnosticChanged, transport));
        token = await loadSessionToken();
        if (!token) throw new Error("未能建立安全会话");
        descriptor = await withConnectionTimeout(client.nodeDescriptor(), 8_000);
      }
      if (descriptor.display_name) await storeNodeDisplayName(identity.nodeId, descriptor.display_name);
      const refreshedNodes = descriptor.display_name ? await listNodeBindings() : nodes;
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
        nodes: refreshedNodes,
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
      const hubConnection = await loadHubConnection();
      setCacheIdentity(`${hubConnection?.accountId || identity.deviceId}:${identity.nodeId}`);
      const transportChanged = (transportMode: "direct" | "p2p" | "relay") => {
        if (generation === connectionGenerationRef.current) commit({ transportMode });
      };
      const p2pDiagnosticChanged = (diagnostic: P2PDiagnostic) => {
        if (generation === connectionGenerationRef.current) commit({
          p2pState: diagnostic.state,
          p2pLastError: diagnostic.lastError,
          p2pRetryAt: diagnostic.retryAt,
          p2pElapsedMs: diagnostic.elapsedMs,
        });
      };
      const lanDiagnosticChanged = (diagnostic: LanDiagnostic) => {
        if (generation === connectionGenerationRef.current) commit({
          lanState: diagnostic.state,
          lanLastError: diagnostic.lastError,
          lanRetryAt: diagnostic.retryAt,
          lanEndpoint: diagnostic.endpoint ?? "",
          lanElapsedMs: diagnostic.elapsedMs,
        });
      };
      const relayDiagnosticChanged = (diagnostic: RelayDiagnostic) => {
        if (generation !== connectionGenerationRef.current) return;
        commit({
          relayState: diagnostic.state,
          relayLastError: diagnostic.lastError,
          relayRetryAt: diagnostic.retryAt,
          relayElapsedMs: diagnostic.elapsedMs,
        });
      };
      const transport = new ConnectionResolverTransport(
        identity,
        transportChanged,
        p2pDiagnosticChanged,
        lanDiagnosticChanged,
        relayDiagnosticChanged,
      );
      await withConnectionTimeout(transport.prepareLanDiscovery());
      const client = await withConnectionTimeout(authenticateDevice({
        gateway_url: identity.gatewayUrl,
        deviceId: identity.deviceId,
        binding: identity,
      }, transportChanged, p2pDiagnosticChanged, lanDiagnosticChanged, relayDiagnosticChanged, transport));
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
    if (client) return withAuthenticationRetry(client, refreshAuthentication, operation);
    // Transport diagnostics can report a live Relay/P2P link before the
    // connect sequence committed a client.  Recover in place instead of
    // failing the call with "尚未连接".
    return refreshAuthentication().then((recovered) => operation(recovered));
  }, [refreshAuthentication]);

  const refreshAgents = useCallback(async () => {
    const client = stateRef.current.client;
    if (!client) return;
    const [{ defaultAgentId, agents }, unavailableAgents] = await Promise.all([
      client.listAgents(),
      client.listAgentAvailability(),
    ]);
    commit({
      agents,
      defaultAgentId,
      unavailableAgents,
      selectedAgentId: stateRef.current.activeAgentId || (
        agents.some((agent) => agent.agent_id === stateRef.current.selectedAgentId)
          ? stateRef.current.selectedAgentId
          : defaultAgentId
      ),
    });
  }, [commit]);

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

  const renameNode = useCallback(async (displayName: string) => {
    const normalized = displayName.trim();
    if (!normalized) throw new Error("电脑名称不能为空");
    const descriptor = await runAuthenticated((client) => client.updateNodeProfile(normalized));
    await storeNodeDisplayName(descriptor.node_id, descriptor.display_name);
    commit({ nodes: await listNodeBindings() });
  }, [commit, runAuthenticated]);

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
    // Unbinding drops the whole account scope: cached snapshots and unread
    // reminders must not survive into the next pairing.  Drafts and the
    // offline queue live outside these stores and are preserved.
    clearAppCache("all");
    await clearTaskReminders();
    setCacheIdentity("");
    connectionRef.current = null;
    commit({ client: null, sessionHandle: "", sessionToken: "", latestEvent: null, status: "booting" });
    await connect();
  }, [commit, connect]);

  const disconnectNode = useCallback(async () => {
    connectionGenerationRef.current += 1;
    provisionalConversationRef.current = null;
    stateRef.current.client?.close();
    await deselectNode();
    setCacheIdentity("");
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
      relayState: "idle",
      relayLastError: "",
      relayRetryAt: 0,
      relayElapsedMs: 0,
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
    const selected = resolveNewConversationAgent(
      agentId,
      stateRef.current.agents.map((agent) => agent.agent_id),
      stateRef.current.defaultAgentId,
    );
    provisionalConversationRef.current = null;
    await storeCoreSession("");
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
      const requestedAgentId = stateRef.current.selectedAgentId;
      provisionalConversationRef.current = runAuthenticated(
        (client) => createProvisionalConversation(client, requestedAgentId),
      ).then((sessionHandle) => {
        commit({ activeAgentId: requestedAgentId, selectedAgentId: requestedAgentId });
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

  const openConversation = useCallback(async (sessionHandle: string, metadata?: { agentId?: string; state?: string }) => {
    provisionalConversationRef.current = null;
    if (metadata?.state === "archived") throw new Error("请先恢复已归档的会话");
    // Commit the target immediately so navigation and cached transcript
    // rendering never wait for a remote session detail round trip.
    const knownAgent = metadata?.agentId || stateRef.current.selectedAgentId;
    commit({
      sessionHandle,
      latestEvent: null,
      activeAgentId: knownAgent,
      selectedAgentId: knownAgent,
    });
    const session = await runAuthenticated((client) => client.getConversationSession(sessionHandle));
    if (session.state === "archived") throw new Error("请先恢复已归档的会话");
    await storeCoreSession(sessionHandle);
    if (stateRef.current.sessionHandle === sessionHandle) {
      commit({ activeAgentId: session.agent_id, selectedAgentId: session.agent_id });
    }
  }, [commit, runAuthenticated]);

  const selectAgent = useCallback((agentId: string) => {
    if (stateRef.current.activeAgentId || stateRef.current.sessionHandle) return;
    if (!stateRef.current.agents.some((agent) => agent.agent_id === agentId)) return;
    commit({ selectedAgentId: agentId });
  }, [commit]);

  const connectionValue = useMemo<ConnectionState>(
    () => ({
      status: state.status,
      transportMode: state.transportMode,
      p2pState: state.p2pState,
      p2pLastError: state.p2pLastError,
      p2pRetryAt: state.p2pRetryAt,
      p2pElapsedMs: state.p2pElapsedMs,
      lanState: state.lanState,
      lanLastError: state.lanLastError,
      lanRetryAt: state.lanRetryAt,
      lanEndpoint: state.lanEndpoint,
      lanElapsedMs: state.lanElapsedMs,
      relayState: state.relayState,
      relayLastError: state.relayLastError,
      relayRetryAt: state.relayRetryAt,
      relayElapsedMs: state.relayElapsedMs,
      lastConnectedAt: state.lastConnectedAt,
      reconnect: connect,
      reauthenticate,
    }),
    [connect, reauthenticate, state.lanElapsedMs, state.lanEndpoint, state.lanLastError, state.lanRetryAt, state.lanState, state.lastConnectedAt, state.p2pElapsedMs, state.p2pLastError, state.p2pRetryAt, state.p2pState, state.relayElapsedMs, state.relayLastError, state.relayRetryAt, state.relayState, state.status, state.transportMode],
  );

  const fleetValue = useMemo<FleetState>(
    () => ({
      deviceId: state.deviceId,
      nodeId: state.nodeId,
      nodes: state.nodes,
      requiredUpdate: state.requiredUpdate,
      availableUpdate: state.availableUpdate,
      agents: state.agents,
      unavailableAgents: state.unavailableAgents,
      defaultAgentId: state.defaultAgentId,
      selectedAgentId: state.selectedAgentId,
      activeAgentId: state.activeAgentId,
      selectAgent,
      pair,
      renameNode,
      removeConnection,
      disconnectNode,
      switchNode,
      refreshAgents,
    }),
    [disconnectNode, pair, refreshAgents, removeConnection, renameNode, selectAgent, state.activeAgentId, state.agents, state.availableUpdate, state.defaultAgentId, state.deviceId, state.nodeId, state.nodes, state.requiredUpdate, state.selectedAgentId, state.unavailableAgents, switchNode],
  );

  const sessionValue = useMemo<SessionState>(
    () => ({
      client: state.client,
      sessionHandle: state.sessionHandle,
      gatewayUrl: state.gatewayUrl,
      sessionToken: state.sessionToken,
      latestEvent: state.latestEvent,
      error: state.error,
      newConversation,
      ensureConversation,
      commitConversation,
      openConversation,
      connection,
      runAuthenticated,
      subscribeEvents,
    }),
    [commitConversation, connection, ensureConversation, newConversation, openConversation, runAuthenticated, state.client, state.error, state.gatewayUrl, state.latestEvent, state.sessionHandle, state.sessionToken, subscribeEvents],
  );

  const gatewayValue = useMemo<GatewayState>(
    () => ({
      ...connectionValue,
      ...fleetValue,
      ...sessionValue,
    }),
    [connectionValue, fleetValue, sessionValue],
  );

  return (
    <ConnectionContext.Provider value={connectionValue}>
      <FleetContext.Provider value={fleetValue}>
        <SessionContext.Provider value={sessionValue}>
          <Context.Provider value={gatewayValue}>{children}</Context.Provider>
        </SessionContext.Provider>
      </FleetContext.Provider>
    </ConnectionContext.Provider>
  );
}

export function useGateway(): GatewayState {
  const value = useContext(Context);
  if (!value) throw new Error("useGateway must be used within GatewayProvider");
  return value;
}
