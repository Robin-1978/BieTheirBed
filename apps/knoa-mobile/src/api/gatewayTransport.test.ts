import { describe, expect, it } from "vitest";

import { bindingUsesHubEndpoint, p2pOfferHeaders } from "./gatewayRouting";

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
});
