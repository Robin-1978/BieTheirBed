import { describe, expect, it } from "vitest";

import { authenticationProof, pairingProof } from "./proof";

describe("Gateway proof payloads", () => {
  it("matches the server canonical pairing payload", () => {
    expect(pairingProof({
      challengeId: "gch-a",
      grantId: "pgr-a",
      nonce: "nonce-a",
      displayName: "Robin   Phone",
      publicKey: "key-a==",
    })).toBe("KNOA-GATEWAY-PROOF-V1\npair\ngch-a\npgr-a\nnonce-a\nRobin Phone\nkey-a");
  });

  it("matches the server canonical authentication payload", () => {
    expect(authenticationProof({
      challengeId: "gch-b",
      deviceId: "dev-a",
      nonce: "nonce-b",
    })).toBe("KNOA-GATEWAY-PROOF-V1\nauthenticate\ngch-b\ndev-a\nnonce-b");
  });
});
