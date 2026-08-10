import { GatewayClient, parsePairingPayload } from "@/api/gatewayClient";
import type { PairingPayload } from "@/api/models";
import {
  loadOrCreatePrivateKey,
  publicKey,
  sign,
  replaceConnectionIdentity,
  storeSession,
} from "./deviceIdentity";
import { authenticationProof, pairingProof } from "./proof";

export async function pairDevice(
  encoded: string,
  displayName: string,
): Promise<{ gatewayUrl: string; deviceId: string }> {
  const payload = parsePairingPayload(encoded);
  const client = new GatewayClient(payload.gateway_url);
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
  await replaceConnectionIdentity({ deviceId: paired.device_id, gatewayUrl: payload.gateway_url });
  return { gatewayUrl: payload.gateway_url, deviceId: paired.device_id };
}

export async function authenticateDevice(
  payload: Pick<PairingPayload, "gateway_url"> & { deviceId: string },
): Promise<GatewayClient> {
  const client = new GatewayClient(payload.gateway_url);
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
