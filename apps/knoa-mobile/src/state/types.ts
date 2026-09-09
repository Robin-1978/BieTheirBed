import type { GatewayClient } from "@/api/gatewayClient";
import type { LanDiagnostic, P2PDiagnostic, RelayDiagnostic } from "@/api/gatewayTransport";
import type { AgentSummary, AndroidRelease, PrincipalTaskEvent, UnavailableAgent } from "@/api/models";
import type { NodeDeviceBinding } from "@/security/deviceIdentity";

export type GatewayConnection = { gatewayUrl: string; token: string };

export type ConnectionState = {
  status: "booting" | "selecting" | "unpaired" | "ready" | "error";
  transportMode: "direct" | "p2p" | "relay";
  p2pState: P2PDiagnostic["state"];
  p2pLastError: string;
  p2pRetryAt: number;
  p2pElapsedMs: number;
  lanState: LanDiagnostic["state"];
  lanLastError: string;
  lanRetryAt: number;
  lanEndpoint: string;
  lanElapsedMs: number;
  relayState: RelayDiagnostic["state"];
  relayLastError: string;
  relayRetryAt: number;
  relayElapsedMs: number;
  lastConnectedAt: number;
  reconnect(): Promise<void>;
  reauthenticate(): Promise<void>;
};

export type FleetState = {
  deviceId: string;
  nodeId: string;
  nodes: NodeDeviceBinding[];
  requiredUpdate: AndroidRelease | null;
  availableUpdate: AndroidRelease | null;
  agents: AgentSummary[];
  unavailableAgents: UnavailableAgent[];
  defaultAgentId: string;
  selectedAgentId: string;
  activeAgentId: string;
  selectAgent(agentId: string): void;
  pair(encoded: string, displayName: string): Promise<void>;
  renameNode(displayName: string): Promise<void>;
  removeConnection(): Promise<void>;
  disconnectNode(): Promise<void>;
  switchNode(nodeId: string): Promise<void>;
  refreshAgents(): Promise<void>;
};

export type SessionState = {
  client: GatewayClient | null;
  sessionHandle: string;
  gatewayUrl: string;
  sessionToken: string;
  latestEvent: PrincipalTaskEvent | null;
  error: string;
  newConversation(agentId?: string): Promise<void>;
  ensureConversation(): Promise<string>;
  commitConversation(sessionHandle: string): Promise<void>;
  openConversation(sessionHandle: string, metadata?: { agentId?: string; state?: string }): Promise<void>;
  connection(): GatewayConnection | null;
  runAuthenticated<T>(operation: (client: GatewayClient) => Promise<T>): Promise<T>;
  subscribeEvents(listener: (event: PrincipalTaskEvent) => void): () => void;
};

export type GatewayState = ConnectionState & FleetState & SessionState;
