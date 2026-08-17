import { describe, expect, it } from "vitest";

import { bindingUsesHubEndpoint } from "./gatewayRouting";

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
});
