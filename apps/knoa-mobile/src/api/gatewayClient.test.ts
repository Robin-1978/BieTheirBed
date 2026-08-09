import { describe, expect, it } from "vitest";

import { parsePairingPayload } from "./gatewayClient";

describe("parsePairingPayload", () => {
  it("accepts the canonical v1 payload", () => {
    const payload = parsePairingPayload(
      JSON.stringify({
        version: "v1",
        gateway_url: "https://knoa.example.com",
        grant_id: "pgr-a",
        grant_secret: "s".repeat(43),
        expires_at: 200,
      }),
      100,
    );
    expect(payload.grant_id).toBe("pgr-a");
  });

  it("rejects expired grants", () => {
    expect(() =>
      parsePairingPayload(
        JSON.stringify({
          version: "v1",
          gateway_url: "https://knoa.example.com",
          grant_id: "pgr-a",
          grant_secret: "s".repeat(43),
          expires_at: 99,
        }),
        100,
      ),
    ).toThrow("已过期");
  });
});
