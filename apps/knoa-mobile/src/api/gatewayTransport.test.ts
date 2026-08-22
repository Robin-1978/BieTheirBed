import { describe, expect, it } from "vitest";

import { bindingUsesHubEndpoint, p2pOfferHeaders, preferredTransport } from "./gatewayRouting";

describe("Gateway transport routing", () => {
  it("routes a legacy Node binding through Relay when its endpoint became the Hosted Hub", () => {
    expect(bindingUsesHubEndpoint(
      { gatewayUrl: "https://knoa.tinydotdot.com/" },
      {
        rootUrl: "https://knoa.tinydotdot.com",
        url: "https://knoa.tinydotdot.com/workspaces/ws_personal",
      },
    )).toBe(true);
  });

  it("keeps a distinct Node endpoint eligible for direct transport", () => {
    expect(bindingUsesHubEndpoint(
      { gatewayUrl: "https://node.example.com" },
      {
        rootUrl: "https://hub.example.com",
        url: "https://hub.example.com/workspaces/ws_personal",
      },
    )).toBe(false);
  });

  it("sends P2P offers as JSON while preserving authentication", () => {
    const headers = p2pOfferHeaders({ Authorization: "Bearer session" });

    expect(headers.get("Authorization")).toBe("Bearer session");
    expect(headers.get("Content-Type")).toBe("application/json");
  });

  it("keeps transport priority deterministic", () => {
    expect(preferredTransport({ lanReady: true, p2pReady: true, relayReady: true })).toBe("direct");
    expect(preferredTransport({ lanReady: false, p2pReady: true, relayReady: true })).toBe("p2p");
    expect(preferredTransport({ lanReady: false, p2pReady: false, relayReady: true })).toBe("relay");
    expect(preferredTransport({ lanReady: false, p2pReady: false, relayReady: false })).toBe("direct");
  });
});
