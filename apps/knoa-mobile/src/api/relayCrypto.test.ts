import { describe, expect, it } from "vitest";

import {
  canonicalString,
  deriveSessionKeys,
  hex,
  packetNonce,
} from "./relayCrypto";

describe("Relay crypto protocol", () => {
  it("uses Python-compatible canonical JSON and HKDF vectors", () => {
    expect(canonicalString({ z: 1, a: { y: true, b: "x" } })).toBe(
      '{"a":{"b":"x","y":true},"z":1}',
    );
    const keys = deriveSessionKeys(Uint8Array.from({ length: 32 }, (_, index) => index), {
      ticketId: "tkt-vector",
      clientNonce: "client-vector",
      serverNonce: "server-vector",
    });
    expect(hex(keys.clientToNode)).toBe(
      "2f99c27a6cedb8220dadce924019d0cc2e48a63efd93fe5f19ea72b088187ac1",
    );
    expect(hex(keys.nodeToClient)).toBe(
      "9737e4cfdc6f340070ee3f9133714a1019f8c922f214cdb364818edc902d1ed6",
    );
  });

  it("encodes the direction prefix and monotonic sequence into the nonce", () => {
    expect(hex(packetNonce("C2N1", 258))).toBe("43324e310000000000000102");
    expect(() => packetNonce("N2C1", -1)).toThrow("sequence");
  });
});
