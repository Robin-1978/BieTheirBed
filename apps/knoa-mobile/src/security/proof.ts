export function pairingProof(input: {
  challengeId: string;
  grantId: string;
  nonce: string;
  displayName: string;
  publicKey: string;
}): string {
  return proof("pair", [
    input.challengeId,
    input.grantId,
    input.nonce,
    input.displayName.trim().replace(/\s+/g, " "),
    input.publicKey.replace(/=+$/, ""),
  ]);
}

export function authenticationProof(input: {
  challengeId: string;
  deviceId: string;
  nonce: string;
}): string {
  return proof("authenticate", [input.challengeId, input.deviceId, input.nonce]);
}

function proof(purpose: string, fields: string[]): string {
  const normalized = fields.map((field) => field.trim());
  if (normalized.some((field) => !field || field.includes("\n") || field.includes("\r"))) {
    throw new Error("Gateway proof fields must be non-empty single lines");
  }
  return ["KNOA-GATEWAY-PROOF-V1", purpose, ...normalized].join("\n");
}
