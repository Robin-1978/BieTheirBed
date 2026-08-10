import { afterEach, describe, expect, it, vi } from "vitest";

import { GatewayClient, GatewayError, parsePairingPayload } from "./gatewayClient";

afterEach(() => vi.unstubAllGlobals());

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

describe("GatewayClient conversation requests", () => {
  it("keeps the business request id separate from transport retries", async () => {
    const fetch = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({ turn: { turn_id: "turn-a" } }), {
      status: 202,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetch);
    const client = new GatewayClient("https://knoa.example.com", "token-a");

    await client.createChatTurn({
      clientRequestId: "message-request-a",
      sessionHandle: "session-a",
      text: "hello",
    });

    const init = fetch.mock.calls[0]![1] as RequestInit;
    expect(JSON.parse(String(init.body))).toMatchObject({
      client_request_id: "message-request-a",
      input: "hello",
    });
  });

  it("passes and returns conversation pagination cursors", async () => {
    const fetch = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({
      sessions: [],
      next_cursor: "next-page",
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetch);
    const client = new GatewayClient("https://knoa.example.com", "token-a");

    const result = await client.listConversationSessions({
      includeArchived: true,
      limit: 50,
      cursor: "current-page",
    });

    expect(fetch.mock.calls[0]![0]).toContain("cursor=current-page");
    expect(result.nextCursor).toBe("next-page");
  });

  it("turns truncated JSON into a controlled retryable gateway error", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response('{"turn":', {
      status: 202,
      headers: { "Content-Type": "application/json" },
    })));
    const client = new GatewayClient("https://knoa.example.com", "token-a");

    await expect(client.createChatTurn({
      clientRequestId: "message-request-a",
      sessionHandle: "session-a",
      text: "hello",
    })).rejects.toMatchObject({
      status: 502,
      code: "invalid_response",
      retryable: true,
    } satisfies Partial<GatewayError>);
  });
});
