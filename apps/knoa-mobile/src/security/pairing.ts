import { GatewayClient, parsePairingPayload } from "@/api/gatewayClient";
import { ConnectionResolverTransport, pairingRelayTransport } from "@/api/gatewayTransport";
import type { PairingPayload } from "@/api/models";
import { loadHubConnection } from "@/hub/hubClient";
import {
  loadOrCreatePrivateKey,
  publicKey,
  sign,
  replaceConnectionIdentity,
  storeSession,
  type NodeDeviceBinding,
} from "./deviceIdentity";
import { authenticationProof, pairingProof } from "./proof";

export async function pairDevice(
  encoded: string,
  displayName: string,
): Promise<{ gatewayUrl: string; deviceId: string }> {
  const payload = parsePairingPayload(encoded);
  const hub = payload.transport === "relay" ? await loadHubConnection() : null;
  if (
    payload.transport === "relay"
    && (!hub || normalizeUrl(hub.url) !== normalizeUrl(payload.gateway_url))
  ) throw new Error("二维码不属于当前 Workspace Hub");
  const gatewayUrl = hub?.url ?? payload.gateway_url;
  const client = new GatewayClient(
    gatewayUrl,
    null,
    payload.transport === "relay" ? pairingRelayTransport(payload) : undefined,
  );
  try {
    const privateKey = await loadOrCreatePrivateKey();
    const publicKeyValue = publicKey(privateKey);
    const challenge = await client.pairChallenge(payload.grant_id);
    const signature = sign(
      privateKey,
      pairingProof({
        challengeId: challenge.challenge_id,
        grantId: payload.grant_id,
        nonce: challenge.nonce,
        displayName,
        publicKey: publicKeyValue,
      }),
    );
    const paired = await client.pairComplete({
      grant_id: payload.grant_id,
      grant_secret: payload.grant_secret,
      challenge_id: challenge.challenge_id,
      nonce: challenge.nonce,
      display_name: displayName,
      public_key: publicKeyValue,
      signature,
    });
    if (
      paired.node.node_id !== payload.node_id
      || paired.node.signing_public_key !== payload.node_signing_public_key
      || paired.node.configuration_public_key !== payload.node_configuration_public_key
    ) throw new Error("节点身份与二维码不一致，已拒绝配对");
    await replaceConnectionIdentity({
      nodeId: payload.node_id,
      deviceId: paired.device_id,
      gatewayUrl,
      nodeSigningPublicKey: payload.node_signing_public_key,
      nodeConfigurationPublicKey: payload.node_configuration_public_key,
    });
    return { gatewayUrl, deviceId: paired.device_id };
  } finally {
    client.close();
  }
}

function normalizeUrl(value: string): string {
  return value.trim().replace(/\/$/, "");
}

export async function authenticateDevice(
  payload: Pick<PairingPayload, "gateway_url"> & {
    deviceId: string;
    binding?: NodeDeviceBinding;
  },
): Promise<GatewayClient> {
  const client = new GatewayClient(
    payload.gateway_url,
    null,
    payload.binding ? new ConnectionResolverTransport(payload.binding) : undefined,
  );
  const privateKey = await loadOrCreatePrivateKey();
  const challenge = await client.authChallenge(payload.deviceId);
  const signature = sign(
    privateKey,
    authenticationProof({
      challengeId: challenge.challenge_id,
      deviceId: payload.deviceId,
      nonce: challenge.nonce,
    }),
  );
  const session = await client.authComplete({
    device_id: payload.deviceId,
    challenge_id: challenge.challenge_id,
    nonce: challenge.nonce,
    signature,
  });
  await storeSession(session.token, session.expires_at);
  return client.authenticated(session.token);
}
