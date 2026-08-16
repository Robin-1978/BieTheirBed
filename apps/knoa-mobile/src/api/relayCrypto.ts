import { hkdf } from "@noble/hashes/hkdf.js";
import { sha256 } from "@noble/hashes/sha2.js";

const encoder = new TextEncoder();

export function canonicalString(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalString).join(",")}]`;
  const item = value as Record<string, unknown>;
  return `{${Object.keys(item).sort().map((key) => `${JSON.stringify(key)}:${canonicalString(item[key])}`).join(",")}}`;
}

export function packetNonce(prefix: "C2N1" | "N2C1", sequence: number): Uint8Array {
  if (!Number.isSafeInteger(sequence) || sequence < 0) throw new Error("Relay sequence is invalid");
  const result = new Uint8Array(12);
  result.set(encoder.encode(prefix), 0);
  let remaining = sequence;
  for (let index = 11; index >= 4; index -= 1) {
    result[index] = remaining % 256;
    remaining = Math.floor(remaining / 256);
  }
  return result;
}

export function packetAad(
  sessionId: string,
  direction: "client_to_node" | "node_to_client",
  sequence: number,
): Uint8Array {
  return encoder.encode(canonicalString({
    audience: "knoa-node-packet-v1",
    session_id: sessionId,
    direction,
    sequence,
  }));
}

export function deriveSessionKeys(
  sharedSecret: Uint8Array,
  input: { ticketId: string; clientNonce: string; serverNonce: string },
): { clientToNode: Uint8Array; nodeToClient: Uint8Array } {
  const salt = sha256(encoder.encode(canonicalString({
    ticket_id: input.ticketId,
    client_nonce: input.clientNonce,
    server_nonce: input.serverNonce,
  })));
  const material = hkdf(
    sha256,
    sharedSecret,
    salt,
    encoder.encode("knoa-node-session-v1"),
    64,
  );
  return { clientToNode: material.slice(0, 32), nodeToClient: material.slice(32, 64) };
}

export function hex(value: Uint8Array): string {
  return [...value].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
