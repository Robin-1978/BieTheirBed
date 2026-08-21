import { chacha20poly1305 } from "@noble/ciphers/chacha.js";
import { ed25519, x25519 } from "@noble/curves/ed25519.js";
import { sha256 } from "@noble/hashes/sha2.js";
import * as Crypto from "expo-crypto";
import { RTCPeerConnection, RTCSessionDescription } from "react-native-webrtc";

import { fromBase64Url, toBase64Url } from "./base64";
import {
  issueConnectionTicket,
  loadHubConnection,
  type HubConnection,
} from "@/hub/hubClient";
import {
  loadOrCreateInstallationId,
  loadOrCreatePrivateKey,
  publicKey,
  sign,
  type NodeDeviceBinding,
} from "@/security/deviceIdentity";
import { DirectFetchTransport, type GatewayTransport } from "./gatewayTransportBase";
import {
  canonicalString,
  deriveSessionKeys,
  hex,
  packetAad,
  packetNonce,
} from "./relayCrypto";
import { bindingUsesHubEndpoint, p2pOfferHeaders } from "./gatewayRouting";
import { relayResponseBody } from "./relayResponse";
import type { PairingPayload } from "./models";
import { discoverNodeOnLan } from "./mdnsDiscovery";

export { DirectFetchTransport, type GatewayTransport } from "./gatewayTransportBase";

export type P2PDiagnostic = {
  state: "idle" | "connecting" | "ready" | "active" | "cooldown";
  lastError: string;
  retryAt: number;
};

const encoder = new TextEncoder();
const decoder = new TextDecoder();
const REQUEST_CHUNK_BYTES = 192 * 1024;
const LAN_DISCOVERY_RETRY_DELAY_MS = 10_000;
const LAN_DISCOVERY_CONNECT_TIMEOUT_MS = 900;
const P2P_ICE_GATHERING_TIMEOUT_MS = 3_000;
const P2P_CHANNEL_OPEN_TIMEOUT_MS = 8_000;
const ICE_SERVERS = [
  { urls: "stun:stun.cloudflare.com:3478" },
  { urls: "stun:stun.l.google.com:19302" },
  { urls: "stun:stun1.l.google.com:19302" },
];

export class ConnectionResolverTransport implements GatewayTransport {
  private readonly direct = new DirectFetchTransport();
  private relay: RelayTransport | null = null;
  private p2p: WebRtcGatewayTransport | null = null;
  private upgradePromise: Promise<void> | null = null;
  private p2pRetryAfter = 0;
  private active: "direct" | "p2p" | "relay" = "direct";
  private relayPreferredUntil = 0;
  private hubEndpointBinding: Promise<boolean> | null = null;
  private lanDiscovery: Promise<void> | null = null;
  private lanGatewayUrl = "";
  private lanDiscoveryRetryAfter = 0;

  constructor(
    private readonly binding: NodeDeviceBinding,
    private readonly onModeChange?: (mode: "direct" | "p2p" | "relay") => void,
    private readonly onP2PDiagnostic?: (diagnostic: P2PDiagnostic) => void,
  ) {}

  mode(): "direct" | "p2p" | "relay" {
    return this.active;
  }

  async request(baseUrl: string, path: string, init: RequestInit): Promise<Response> {
    // LAN discovery is opportunistic.  It must never sit in front of Relay
    // or P2P connection establishment.  Once a verified LAN endpoint appears,
    // the next request will prefer it.
    if (!this.binding.directGatewayUrl) this.startLanDiscovery();
    if (!this.binding.directGatewayUrl && this.lanGatewayUrl) {
      try {
        const response = await withConnectTimeout(
          (signal) => this.direct.request(this.lanGatewayUrl, path, { ...init, signal }),
          LAN_DISCOVERY_CONNECT_TIMEOUT_MS,
        );
        this.setActive("direct");
        return response;
      } catch {
        this.lanGatewayUrl = "";
        this.lanDiscoveryRetryAfter = Date.now() + LAN_DISCOVERY_RETRY_DELAY_MS;
      }
    }
    if (this.p2p?.ready()) {
      try {
        const response = await this.p2p.request(baseUrl, path, init);
        this.setActive("p2p");
        this.setP2PDiagnostic("active");
        return response;
      } catch (error) {
        this.p2p.close();
        this.p2p = null;
        this.p2pRetryAfter = Date.now() + 60_000;
        this.setP2PDiagnostic("cooldown", errorText(error), this.p2pRetryAfter);
      }
    }
    if (!this.binding.directGatewayUrl && await this.bindingPointsAtCurrentHub()) {
      // Start the P2P upgrade before waiting for this Relay request.  The
      // upgrade uses the same authenticated Relay session for signaling, but
      // runs independently; the current request must be served by Relay
      // immediately and must never wait for ICE or the data channel.
      this.startP2PUpgrade(baseUrl, init);
      try {
        const response = await this.relayRequest(baseUrl, path, init);
        if (!this.p2p?.ready()) this.setActive("relay");
        return response;
      } catch (relayError) {
        throw new Error(`Node Relay 不可用：${errorText(relayError)}`);
      }
    }
    if (Date.now() < this.relayPreferredUntil) {
      try {
        const response = await this.relayRequest(baseUrl, path, init);
        if (!this.p2p?.ready()) this.setActive("relay");
        return response;
      } catch {
        this.relayPreferredUntil = 0;
        this.relay?.close();
        this.relay = null;
      }
    }
    try {
      const response = await withConnectTimeout(
        (signal) => this.direct.request(
          this.binding.directGatewayUrl || this.lanGatewayUrl || baseUrl,
          path,
          { ...init, signal },
        ),
        4500,
      );
      this.setActive("direct");
      return response;
    } catch (directError) {
      try {
        const response = await this.relayRequest(baseUrl, path, init);
        if (!this.p2p?.ready()) this.setActive("relay");
        this.startP2PUpgrade(baseUrl, init);
        this.relayPreferredUntil = Date.now() + 10_000;
        return response;
      } catch (relayError) {
        throw new Error(
          `Node direct 与 Relay 均不可用：${errorText(directError)}；${errorText(relayError)}`,
        );
      }
    }
  }

  close(): void {
    this.p2p?.close();
    this.p2p = null;
    this.relay?.close();
    this.relay = null;
    this.setP2PDiagnostic("idle");
    this.lanDiscovery = null;
    this.lanGatewayUrl = "";
    this.lanDiscoveryRetryAfter = 0;
  }

  /** Wait for one LAN mDNS scan before the first authenticated request. */
  async prepareLanDiscovery(): Promise<void> {
    if (this.binding.directGatewayUrl || this.lanGatewayUrl) return;
    if (Date.now() < this.lanDiscoveryRetryAfter) return;
    this.startLanDiscovery();
    const pending = this.lanDiscovery;
    if (pending) await pending.catch(() => undefined);
  }

  private startLanDiscovery(): void {
    if (this.lanGatewayUrl || this.lanDiscovery || Date.now() < this.lanDiscoveryRetryAfter) return;
    if (!this.lanDiscovery) {
      this.lanDiscovery = discoverNodeOnLan(this.binding).then((url) => {
        this.lanGatewayUrl = url || "";
        if (!url) this.lanDiscoveryRetryAfter = Date.now() + LAN_DISCOVERY_RETRY_DELAY_MS;
      }).catch(() => {
        this.lanDiscoveryRetryAfter = Date.now() + LAN_DISCOVERY_RETRY_DELAY_MS;
      }).finally(() => { this.lanDiscovery = null; });
    }
  }

  private async relayRequest(baseUrl: string, path: string, init: RequestInit): Promise<Response> {
    if (!this.relay) this.relay = new RelayTransport(this.binding, "session");
    return this.relay.request(baseUrl, path, init);
  }

  private startP2PUpgrade(baseUrl: string, init: RequestInit): void {
    if (this.p2p?.ready() || this.upgradePromise || Date.now() < this.p2pRetryAfter
      || !new Headers(init.headers).get("authorization")) return;
    this.setP2PDiagnostic("connecting");
    const pending = (async () => {
      const p2p = new WebRtcGatewayTransport();
      try {
        await p2p.connect(async (offer) => {
          const response = await this.relayRequest(baseUrl, "/v1/p2p/offer", {
            method: "POST",
            headers: p2pOfferHeaders(init.headers),
            body: JSON.stringify(offer),
          });
          const payload = await response.json() as {
            answer?: { type?: string; sdp?: string };
            error?: string;
            detail?: string;
          };
          if (!response.ok) throw new Error(payload.detail || payload.error || "Node P2P signaling rejected");
          if (!payload.answer?.sdp || payload.answer.type !== "answer") throw new Error("Node P2P answer invalid");
          return { type: "answer" as const, sdp: payload.answer.sdp };
        });
        this.setP2PDiagnostic("ready");
        const probe = await p2p.request(baseUrl, "/v1/session", {
          method: "GET",
          headers: init.headers,
        });
        if (!probe.ok) throw new Error(`P2P 探测被 Node 拒绝（HTTP ${probe.status}）`);
      } catch (error) {
        p2p.close();
        throw error;
      }
      this.p2p?.close();
      this.p2p = p2p;
      this.relayPreferredUntil = 0;
      this.p2pRetryAfter = 0;
      this.setActive("p2p");
      this.setP2PDiagnostic("active");
    })().catch((error) => {
      this.p2pRetryAfter = Date.now() + 60_000;
      this.setP2PDiagnostic("cooldown", errorText(error), this.p2pRetryAfter);
    }).finally(() => {
      if (this.upgradePromise === pending) this.upgradePromise = null;
    });
    this.upgradePromise = pending;
  }

  private bindingPointsAtCurrentHub(): Promise<boolean> {
    if (!this.hubEndpointBinding) {
      this.hubEndpointBinding = loadHubConnection()
        .then((hub) => Boolean(hub && bindingUsesHubEndpoint(this.binding, hub)))
        .catch(() => false);
    }
    return this.hubEndpointBinding;
  }

  private setActive(mode: "direct" | "p2p" | "relay"): void {
    if (this.active === mode) return;
    this.active = mode;
    this.onModeChange?.(mode);
  }

  private setP2PDiagnostic(
    state: P2PDiagnostic["state"],
    lastError = "",
    retryAt = 0,
  ): void {
    this.onP2PDiagnostic?.({ state, lastError, retryAt });
  }
}

type ClientHello = {
  type: "client_hello";
  version: 1;
  ticket: string;
  installation_id: string;
  device_id: string;
  client_signing_public_key: string;
  client_ephemeral_public_key: string;
  client_nonce: string;
  transport: "relay";
  signature: string;
};

type PairingClientHello = {
  type: "pairing_client_hello";
  version: 1;
  ticket: string;
  installation_id: string;
  client_signing_public_key: string;
  client_ephemeral_public_key: string;
  client_nonce: string;
  transport: "relay";
  signature: string;
};

type RelayClientHello = ClientHello | PairingClientHello;

type RelayBinding = Pick<
  NodeDeviceBinding,
  "nodeId" | "deviceId" | "nodeSigningPublicKey" | "nodeConfigurationPublicKey"
>;

export function pairingRelayTransport(payload: PairingPayload): GatewayTransport {
  if (payload.transport !== "relay") throw new Error("二维码不是 Relay 配对信息");
  return new RelayTransport({
    nodeId: payload.node_id,
    deviceId: "",
    nodeSigningPublicKey: payload.node_signing_public_key,
    nodeConfigurationPublicKey: payload.node_configuration_public_key,
  }, "pairing");
}

type ServerHello = {
  type: "server_hello";
  version: 1;
  node_id: string;
  server_ephemeral_public_key: string;
  server_nonce: string;
  signature: string;
};

type RelayFrame = {
  version: 1;
  session_id: string;
  stream_id: number;
  frame_type: "data";
  sequence: number;
  ciphertext_length: number;
  ciphertext: string;
  window_bytes: 0;
};

type ResponseState = {
  status: number;
  headers: Record<string, string>;
  expectedLength: number;
  chunks: Uint8Array[];
  receivedLength: number;
  resolve(response: Response): void;
  reject(error: Error): void;
};

type TicketClaims = {
  ticket_id: string;
  expires_at: number;
};

type P2PResponseState = ResponseState & { requestId: string };

class WebRtcGatewayTransport implements GatewayTransport {
  private peer: RTCPeerConnection | null = null;
  private channel: ReturnType<RTCPeerConnection["createDataChannel"]> | null = null;
  private pending = new Map<string, P2PResponseState>();

  mode(): "p2p" {
    return "p2p";
  }

  ready(): boolean {
    const peer = this.peer;
    return peer?.connectionState === "connected"
      && ["connected", "completed"].includes(peer.iceConnectionState)
      && this.channel?.readyState === "open";
  }

  async connect(exchange: (offer: { type: "offer"; sdp: string }) => Promise<{ type: "answer"; sdp: string }>): Promise<void> {
    this.close();
    const peer = new RTCPeerConnection({ iceServers: ICE_SERVERS });
    const channel = peer.createDataChannel("knoa-http-v1", { ordered: true });
    this.peer = peer;
    this.channel = channel;
    channel.onmessage = (event: { data: unknown }) => this.receive(String(event.data));
    channel.onclose = () => this.fail(new Error("P2P 连接已关闭"));
    channel.onerror = () => this.fail(new Error("P2P 连接错误"));
    const opened = new Promise<void>((resolve, reject) => {
      channel.onopen = () => resolve();
      const failIfDisconnected = () => {
        if (["failed", "closed", "disconnected"].includes(peer.connectionState)
          || ["failed", "closed", "disconnected"].includes(peer.iceConnectionState)) {
          reject(new Error("P2P ICE 连接失败"));
        }
      };
      peer.onconnectionstatechange = failIfDisconnected;
      peer.oniceconnectionstatechange = failIfDisconnected;
    });
    const offer = await peer.createOffer();
    await peer.setLocalDescription(offer);
    await waitForIceGathering(peer);
    if (!peer.localDescription?.sdp) throw new Error("P2P Offer 创建失败");
    const answer = await exchange({ type: "offer", sdp: peer.localDescription.sdp });
    await peer.setRemoteDescription(new RTCSessionDescription(answer));
    await withPromiseTimeout(opened, P2P_CHANNEL_OPEN_TIMEOUT_MS, "P2P 建连超时");
  }

  async request(_baseUrl: string, path: string, init: RequestInit): Promise<Response> {
    if (!this.ready() || !this.channel) throw new Error("P2P 尚未连接");
    const body = await requestBody(init.body);
    const requestId = `p2p_${toBase64Url(await Crypto.getRandomBytesAsync(18))}`;
    const headers = Object.fromEntries(
      [...new Headers(init.headers).entries()].map(([key, value]) => [key.toLowerCase(), value]),
    );
    const response = new Promise<Response>((resolve, reject) => {
      this.pending.set(requestId, {
        requestId,
        status: 0,
        headers: {},
        expectedLength: -1,
        chunks: [],
        receivedLength: 0,
        resolve,
        reject,
      });
    });
    try {
      await this.send({
        type: "request_start",
        request_id: requestId,
        method: (init.method ?? "GET").toUpperCase(),
        path,
        headers,
        body_length: body.length,
      });
      for (let offset = 0; offset < body.length; offset += 48 * 1024) {
        await this.send({
          type: "request_body",
          request_id: requestId,
          data: toBase64Url(body.slice(offset, offset + 48 * 1024)),
        });
      }
      await this.send({ type: "request_end", request_id: requestId });
      return await withPromiseTimeout(response, 120_000, "P2P 请求超时");
    } catch (error) {
      this.pending.delete(requestId);
      throw error;
    }
  }

  close(): void {
    const peer = this.peer;
    this.peer = null;
    this.channel = null;
    this.fail(new Error("P2P 连接已关闭"));
    peer?.close();
  }

  private async send(message: Record<string, unknown>): Promise<void> {
    const channel = this.channel;
    if (!channel || channel.readyState !== "open") throw new Error("P2P 连接不可用");
    while (channel.bufferedAmount > 1024 * 1024) {
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
    channel.send(JSON.stringify(message));
  }

  private receive(raw: string): void {
    try {
      const message = JSON.parse(raw) as Record<string, unknown>;
      const requestId = String(message.request_id ?? "");
      const pending = this.pending.get(requestId);
      if (!pending) return;
      if (message.type === "response_start") {
        pending.status = Number(message.status);
        pending.headers = isStringRecord(message.headers) ? message.headers : {};
        pending.expectedLength = Number(message.body_length);
        if (!Number.isSafeInteger(pending.status) || pending.status < 100 || pending.status > 599
          || !Number.isSafeInteger(pending.expectedLength) || pending.expectedLength < 0) throw new Error("P2P 响应头无效");
        return;
      }
      if (message.type === "response_body") {
        const chunk = fromBase64Url(String(message.data ?? ""));
        pending.chunks.push(chunk);
        pending.receivedLength += chunk.length;
        if (pending.receivedLength > pending.expectedLength) throw new Error("P2P 响应体过长");
        return;
      }
      if (message.type === "response_end") {
        if (pending.receivedLength !== pending.expectedLength || pending.status === 0) throw new Error("P2P 响应体不完整");
        this.pending.delete(requestId);
        const bytes = joinBytes(pending.chunks, pending.receivedLength);
        pending.resolve(new Response(relayResponseBody(bytes, pending.headers), {
          status: pending.status,
          headers: pending.headers,
        }));
        return;
      }
      if (message.type === "reset") throw new Error("Node 重置了 P2P 请求");
      throw new Error("P2P 响应类型无效");
    } catch (error) {
      this.fail(error instanceof Error ? error : new Error("P2P 响应无效"));
    }
  }

  private fail(error: Error): void {
    for (const pending of this.pending.values()) pending.reject(error);
    this.pending.clear();
  }
}

class RelayTransport implements GatewayTransport {
  private socket: WebSocket | null = null;
  private connectPromise: Promise<void> | null = null;
  private sessionId = "";
  private encryptKey: Uint8Array | null = null;
  private decryptKey: Uint8Array | null = null;
  private sendSequence = 0;
  private receiveSequence = 0;
  private nextStreamId = 1;
  private pending = new Map<number, ResponseState>();

  constructor(
    private readonly binding: RelayBinding,
    private readonly scope: "session" | "pairing",
  ) {}

  mode(): "relay" {
    return "relay";
  }

  async request(_baseUrl: string, path: string, init: RequestInit): Promise<Response> {
    await this.connect();
    const body = await requestBody(init.body);
    const streamId = this.nextStreamId++;
    const headers = Object.fromEntries(
      [...new Headers(init.headers).entries()].map(([key, value]) => [key.toLowerCase(), value]),
    );
    const response = new Promise<Response>((resolve, reject) => {
      this.pending.set(streamId, {
        status: 0,
        headers: {},
        expectedLength: -1,
        chunks: [],
        receivedLength: 0,
        resolve,
        reject,
      });
    });
    try {
      this.sendEncrypted(streamId, {
        type: "request_start",
        method: (init.method ?? "GET").toUpperCase(),
        path,
        headers,
        body_length: body.length,
      });
      for (let offset = 0; offset < body.length; offset += REQUEST_CHUNK_BYTES) {
        this.sendEncrypted(streamId, {
          type: "request_body",
          data: toBase64Url(body.slice(offset, offset + REQUEST_CHUNK_BYTES)),
        });
      }
      this.sendEncrypted(streamId, { type: "request_end" });
      return await withPromiseTimeout(response, 120_000, "Relay 请求超时");
    } catch (error) {
      this.pending.delete(streamId);
      throw error;
    }
  }

  close(): void {
    this.fail(new Error("Relay 连接已关闭"));
  }

  private async connect(): Promise<void> {
    if (this.socket?.readyState === WebSocket.OPEN && this.encryptKey && this.decryptKey) return;
    if (this.connectPromise) return this.connectPromise;
    const pending = this.open();
    this.connectPromise = pending;
    try {
      await pending;
    } catch (error) {
      const normalized = error instanceof Error ? error : new Error("Relay 连接失败");
      this.fail(normalized);
      throw normalized;
    } finally {
      if (this.connectPromise === pending) this.connectPromise = null;
    }
  }

  private async open(): Promise<void> {
    const hub = await requiredHubForNode(this.binding.nodeId);
    const ticket = await issueConnectionTicket(this.binding.nodeId, "relay", this.scope);
    const claims = verifyTicket(ticket, hub, this.binding.nodeId);
    const socket = new WebSocket(relayUrl(hub.url));
    this.socket = socket;
    await waitForSocketOpen(socket);
    const ready = waitForJson(socket);
    socket.send(JSON.stringify({ ticket }));
    const readyMessage = await withPromiseTimeout(ready, 15_000, "Relay 未接受连接票据");
    if (readyMessage.ready !== true || typeof readyMessage.session_id !== "string") {
      throw new Error("Relay 返回了无效会话");
    }
    this.sessionId = readyMessage.session_id;
    if (this.sessionId !== claims.ticket_id) throw new Error("Relay 会话与连接票据不一致");

    const privateKey = await loadOrCreatePrivateKey();
    const ephemeralPrivate = await Crypto.getRandomBytesAsync(32);
    const clientNonce = toBase64Url(await Crypto.getRandomBytesAsync(24));
    const installationId = await loadOrCreateInstallationId();
    const signingPublicKey = publicKey(privateKey);
    const ephemeralPublicKey = toBase64Url(x25519.getPublicKey(ephemeralPrivate));
    const hello = this.scope === "pairing"
      ? pairingClientHello({
        ticket,
        installationId,
        signingPublicKey,
        ephemeralPublicKey,
        clientNonce,
        privateKey,
      })
      : sessionClientHello({
        ticket,
        installationId,
        deviceId: this.binding.deviceId,
        signingPublicKey,
        ephemeralPublicKey,
        clientNonce,
        privateKey,
      });
    const serverHelloPromise = waitForJson(socket);
    this.sendPlaintext(0, 0, encoder.encode(canonicalString(hello)));
    const wrapped = await withPromiseTimeout(serverHelloPromise, 15_000, "Node 握手超时");
    const frame = parseRelayFrame(wrapped.frame);
    const serverHello = JSON.parse(decoder.decode(fromBase64Url(frame.ciphertext))) as ServerHello;
    verifyServerHello(serverHello, hello, this.sessionId, this.binding);

    const shared = x25519.getSharedSecret(
      ephemeralPrivate,
      fromBase64Url(serverHello.server_ephemeral_public_key),
    );
    const keys = deriveSessionKeys(
      shared,
      {
        ticketId: claims.ticket_id,
        clientNonce,
        serverNonce: serverHello.server_nonce,
      },
    );
    this.encryptKey = keys.clientToNode;
    this.decryptKey = keys.nodeToClient;
    this.sendSequence = 0;
    this.receiveSequence = 0;
    socket.addEventListener("message", this.onMessage);
    socket.addEventListener("error", this.onSocketFailure);
    socket.addEventListener("close", this.onSocketFailure);
  }

  private readonly onMessage = (event: MessageEvent) => {
    try {
      const wrapped = JSON.parse(String(event.data)) as { frame?: unknown };
      const frame = parseRelayFrame(wrapped.frame);
      if (frame.session_id !== this.sessionId || frame.sequence !== this.receiveSequence) {
        throw new Error("Relay 响应序列无效");
      }
      if (!this.decryptKey) throw new Error("Relay 会话尚未建立");
      const plaintext = chacha20poly1305(
        this.decryptKey,
        packetNonce("N2C1", frame.sequence),
        packetAad(this.sessionId, "node_to_client", frame.sequence),
      ).decrypt(fromBase64Url(frame.ciphertext));
      this.receiveSequence += 1;
      this.receiveMessage(frame.stream_id, JSON.parse(decoder.decode(plaintext)) as Record<string, unknown>);
    } catch (error) {
      this.fail(error instanceof Error ? error : new Error("Relay 响应无效"));
    }
  };

  private readonly onSocketFailure = () => {
    this.fail(new Error("Relay 连接中断"));
  };

  private receiveMessage(streamId: number, message: Record<string, unknown>): void {
    const pending = this.pending.get(streamId);
    if (!pending) return;
    if (message.type === "response_start") {
      pending.status = Number(message.status);
      pending.headers = isStringRecord(message.headers) ? message.headers : {};
      pending.expectedLength = Number(message.body_length);
      if (!Number.isSafeInteger(pending.status) || pending.status < 100 || pending.status > 599
        || !Number.isSafeInteger(pending.expectedLength) || pending.expectedLength < 0) {
        throw new Error("Relay 响应头无效");
      }
      return;
    }
    if (message.type === "response_body") {
      const chunk = fromBase64Url(String(message.data ?? ""));
      pending.chunks.push(chunk);
      pending.receivedLength += chunk.length;
      if (pending.receivedLength > pending.expectedLength) throw new Error("Relay 响应体过长");
      return;
    }
    if (message.type === "response_end") {
      if (pending.receivedLength !== pending.expectedLength || pending.status === 0) {
        throw new Error("Relay 响应体不完整");
      }
      this.pending.delete(streamId);
      const bytes = joinBytes(pending.chunks, pending.receivedLength);
      pending.resolve(new Response(relayResponseBody(bytes, pending.headers), {
        status: pending.status,
        headers: pending.headers,
      }));
      return;
    }
    if (message.type === "reset") {
      this.pending.delete(streamId);
      pending.reject(new Error("Node 无法完成 Relay 请求"));
      return;
    }
    throw new Error("Relay 响应类型无效");
  }

  private sendEncrypted(streamId: number, message: Record<string, unknown>): void {
    if (!this.encryptKey) throw new Error("Relay 会话尚未建立");
    const sequence = this.sendSequence++;
    const ciphertext = chacha20poly1305(
      this.encryptKey,
      packetNonce("C2N1", sequence),
      packetAad(this.sessionId, "client_to_node", sequence),
    ).encrypt(encoder.encode(canonicalString(message)));
    this.sendPlaintext(streamId, sequence, ciphertext);
  }

  private sendPlaintext(streamId: number, sequence: number, payload: Uint8Array): void {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) throw new Error("Relay 未连接");
    const frame: RelayFrame = {
      version: 1,
      session_id: this.sessionId,
      stream_id: streamId,
      frame_type: "data",
      sequence,
      ciphertext_length: payload.length,
      ciphertext: toBase64Url(payload),
      window_bytes: 0,
    };
    this.socket.send(JSON.stringify({ frame }));
  }

  private fail(error: Error): void {
    const socket = this.socket;
    this.socket = null;
    this.encryptKey = null;
    this.decryptKey = null;
    this.sessionId = "";
    if (socket && socket.readyState < WebSocket.CLOSING) socket.close();
    for (const response of this.pending.values()) response.reject(error);
    this.pending.clear();
  }
}

async function requiredHubForNode(nodeId: string): Promise<HubConnection> {
  const hub = await loadHubConnection();
  if (!hub) throw new Error("尚未连接 Personal Hub");
  const ticketNodeId = nodeId.trim();
  if (!ticketNodeId) throw new Error("Node 绑定无效");
  return hub;
}

function verifyTicket(ticket: string, hub: HubConnection, nodeId: string): TicketClaims {
  const [encoded, signature, extra] = ticket.split(".");
  if (!encoded || !signature || extra) throw new Error("Hub 连接票据无效");
  if (!ed25519.verify(fromBase64Url(signature), encoder.encode(encoded), fromBase64Url(hub.signingPublicKey))) {
    throw new Error("Hub 连接票据签名无效");
  }
  const claims = JSON.parse(decoder.decode(fromBase64Url(encoded))) as Record<string, unknown>;
  if (claims.aud !== "knoa-node-session-v1" || claims.hub_id !== hub.hubId
    || claims.node_id !== nodeId || claims.transport !== "relay" || claims.protocol_version !== 1
    || Number(claims.expires_at) <= Date.now() / 1000 || typeof claims.ticket_id !== "string") {
    throw new Error("Hub 连接票据范围无效");
  }
  return { ticket_id: claims.ticket_id, expires_at: Number(claims.expires_at) };
}

function verifyServerHello(
  hello: ServerHello,
  clientHello: RelayClientHello,
  sessionId: string,
  binding: RelayBinding,
): void {
  if (hello.type !== "server_hello" || hello.version !== 1 || hello.node_id !== binding.nodeId) {
    throw new Error("Node 握手身份无效");
  }
  const transcript = {
    audience: "knoa-node-server-hello-v1",
    session_id: sessionId,
    client_hello_digest: hex(sha256(encoder.encode(canonicalString(clientHello)))),
    node_id: hello.node_id,
    server_ephemeral_public_key: hello.server_ephemeral_public_key,
    server_nonce: hello.server_nonce,
  };
  if (!ed25519.verify(
    fromBase64Url(hello.signature),
    encoder.encode(canonicalString(transcript)),
    fromBase64Url(binding.nodeSigningPublicKey),
  )) throw new Error("Node 固定身份签名无效");
}

function sessionClientHello(input: {
  ticket: string;
  installationId: string;
  deviceId: string;
  signingPublicKey: string;
  ephemeralPublicKey: string;
  clientNonce: string;
  privateKey: Uint8Array;
}): ClientHello {
  const unsigned = {
    audience: "knoa-node-client-hello-v1",
    version: 1,
    ticket: input.ticket,
    installation_id: input.installationId,
    device_id: input.deviceId,
    client_signing_public_key: input.signingPublicKey,
    client_ephemeral_public_key: input.ephemeralPublicKey,
    client_nonce: input.clientNonce,
    transport: "relay",
  } as const;
  return {
    type: "client_hello",
    version: 1,
    ticket: input.ticket,
    installation_id: input.installationId,
    device_id: input.deviceId,
    client_signing_public_key: input.signingPublicKey,
    client_ephemeral_public_key: input.ephemeralPublicKey,
    client_nonce: input.clientNonce,
    transport: "relay",
    signature: sign(input.privateKey, canonicalString(unsigned)),
  };
}

function pairingClientHello(input: {
  ticket: string;
  installationId: string;
  signingPublicKey: string;
  ephemeralPublicKey: string;
  clientNonce: string;
  privateKey: Uint8Array;
}): PairingClientHello {
  const unsigned = {
    audience: "knoa-node-pairing-client-hello-v1",
    version: 1,
    ticket: input.ticket,
    installation_id: input.installationId,
    client_signing_public_key: input.signingPublicKey,
    client_ephemeral_public_key: input.ephemeralPublicKey,
    client_nonce: input.clientNonce,
    transport: "relay",
  } as const;
  return {
    type: "pairing_client_hello",
    version: 1,
    ticket: input.ticket,
    installation_id: input.installationId,
    client_signing_public_key: input.signingPublicKey,
    client_ephemeral_public_key: input.ephemeralPublicKey,
    client_nonce: input.clientNonce,
    transport: "relay",
    signature: sign(input.privateKey, canonicalString(unsigned)),
  };
}

function parseRelayFrame(value: unknown): RelayFrame {
  if (!value || typeof value !== "object") throw new Error("Relay 帧无效");
  const frame = value as Partial<RelayFrame>;
  if (frame.version !== 1 || frame.frame_type !== "data" || typeof frame.session_id !== "string"
    || !Number.isSafeInteger(frame.stream_id) || !Number.isSafeInteger(frame.sequence)
    || typeof frame.ciphertext !== "string" || frame.ciphertext_length !== fromBase64Url(frame.ciphertext).length) {
    throw new Error("Relay 帧无效");
  }
  return frame as RelayFrame;
}

function relayUrl(baseUrl: string): string {
  const url = new URL(baseUrl);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = `${url.pathname.replace(/\/$/, "")}/v1/relay/client`;
  url.search = "";
  url.hash = "";
  return url.toString();
}

async function requestBody(body: BodyInit | null | undefined): Promise<Uint8Array> {
  if (body === undefined || body === null) return new Uint8Array();
  if (typeof body === "string") return encoder.encode(body);
  if (body instanceof ArrayBuffer) return new Uint8Array(body);
  if (ArrayBuffer.isView(body)) return new Uint8Array(body.buffer, body.byteOffset, body.byteLength);
  if (typeof Blob !== "undefined" && body instanceof Blob) return new Uint8Array(await body.arrayBuffer());
  throw new Error("Relay 暂不支持这个请求体类型");
}

function joinBytes(chunks: Uint8Array[], length: number): Uint8Array {
  const result = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.length;
  }
  return result;
}

function isStringRecord(value: unknown): value is Record<string, string> {
  return Boolean(value) && typeof value === "object"
    && Object.values(value as Record<string, unknown>).every((item) => typeof item === "string");
}

function waitForSocketOpen(socket: WebSocket): Promise<void> {
  return withPromiseTimeout(new Promise((resolve, reject) => {
    const open = () => { cleanup(); resolve(); };
    const fail = () => { cleanup(); reject(new Error("无法连接 Relay")); };
    const cleanup = () => {
      socket.removeEventListener("open", open);
      socket.removeEventListener("error", fail);
      socket.removeEventListener("close", fail);
    };
    socket.addEventListener("open", open);
    socket.addEventListener("error", fail);
    socket.addEventListener("close", fail);
  }), 15_000, "Relay 连接超时");
}

function waitForIceGathering(peer: RTCPeerConnection): Promise<void> {
  if (peer.iceGatheringState === "complete") return Promise.resolve();
  const complete = new Promise<void>((resolve) => {
    const check = () => {
      if (peer.iceGatheringState !== "complete") return;
      peer.onicegatheringstatechange = null;
      resolve();
    };
    peer.onicegatheringstatechange = check;
    check();
  });
  // This flow does not implement trickle ICE, so the SDP must contain the
  // gathered server-reflexive candidates before it is sent through Relay.
  // Signaling is non-trickle, so wait briefly for STUN candidates.  A long
  // wait here delays the useful Relay path and makes the transport look stuck.
  return Promise.race([
    complete,
    new Promise<void>((resolve) => setTimeout(resolve, P2P_ICE_GATHERING_TIMEOUT_MS)),
  ]);
}

function waitForJson(socket: WebSocket): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    const message = (event: MessageEvent) => {
      cleanup();
      try { resolve(JSON.parse(String(event.data)) as Record<string, unknown>); }
      catch { reject(new Error("Relay 返回了无效消息")); }
    };
    const fail = () => { cleanup(); reject(new Error("Relay 连接中断")); };
    const cleanup = () => {
      socket.removeEventListener("message", message);
      socket.removeEventListener("error", fail);
      socket.removeEventListener("close", fail);
    };
    socket.addEventListener("message", message);
    socket.addEventListener("error", fail);
    socket.addEventListener("close", fail);
  });
}

async function withConnectTimeout<T>(
  operation: (signal: AbortSignal) => Promise<T>,
  milliseconds: number,
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), milliseconds);
  try { return await operation(controller.signal); }
  finally { clearTimeout(timer); }
}

function withPromiseTimeout<T>(promise: Promise<T>, milliseconds: number, message: string): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(message)), milliseconds);
    promise.then(
      (value) => { clearTimeout(timer); resolve(value); },
      (error) => { clearTimeout(timer); reject(error); },
    );
  });
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "连接失败";
}
