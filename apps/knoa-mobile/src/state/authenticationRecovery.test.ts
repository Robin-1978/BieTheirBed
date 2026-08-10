import { describe, expect, it, vi } from "vitest";

import { GatewayClient, GatewayError } from "../api/gatewayClient";
import { withAuthenticationRetry } from "./authenticationRecovery";

describe("withAuthenticationRetry", () => {
  it("reauthenticates and retries once after a 401", async () => {
    const stale = new GatewayClient("https://knoa.example.com", "stale");
    const fresh = new GatewayClient("https://knoa.example.com", "fresh");
    const refresh = vi.fn(async () => fresh);
    const operation = vi.fn(async (client: GatewayClient) => {
      if (client === stale) throw new GatewayError(401, "expired_session");
      return "ok";
    });

    await expect(withAuthenticationRetry(stale, refresh, operation)).resolves.toBe("ok");
    expect(refresh).toHaveBeenCalledOnce();
    expect(operation).toHaveBeenCalledTimes(2);
  });

  it("does not loop when the retried request is still unauthorized", async () => {
    const stale = new GatewayClient("https://knoa.example.com", "stale");
    const fresh = new GatewayClient("https://knoa.example.com", "fresh");
    const refresh = vi.fn(async () => fresh);
    const operation = vi.fn(async () => {
      throw new GatewayError(401, "expired_session");
    });

    await expect(withAuthenticationRetry(stale, refresh, operation)).rejects.toMatchObject({
      status: 401,
    });
    expect(refresh).toHaveBeenCalledOnce();
    expect(operation).toHaveBeenCalledTimes(2);
  });

  it("does not reauthenticate for unrelated failures", async () => {
    const client = new GatewayClient("https://knoa.example.com", "token");
    const refresh = vi.fn(async () => client);
    const operation = vi.fn(async () => {
      throw new GatewayError(503, "unavailable");
    });

    await expect(withAuthenticationRetry(client, refresh, operation)).rejects.toMatchObject({
      status: 503,
    });
    expect(refresh).not.toHaveBeenCalled();
    expect(operation).toHaveBeenCalledOnce();
  });
});
