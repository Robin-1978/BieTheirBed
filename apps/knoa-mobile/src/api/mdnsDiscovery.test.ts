import { describe, expect, it } from "vitest";

import { mdnsImplementationOrder } from "./mdnsDiscoveryPolicy";

describe("mDNS discovery backend selection", () => {
  it("falls back from embedded DNSSD to Android system NSD", () => {
    expect(mdnsImplementationOrder("android")).toEqual(["DNSSD", "NSD"]);
  });

  it("uses one implementation outside Android", () => {
    expect(mdnsImplementationOrder("ios")).toEqual(["NSD"]);
  });
});
