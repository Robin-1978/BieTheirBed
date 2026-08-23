import { describe, expect, it } from "vitest";

import { presentHubNodeName, presentNodeName } from "./nodePresentation";

describe("node presentation", () => {
  it("uses a friendly fallback when a binding has no name", () => {
    expect(presentNodeName({ nodeId: "node_123", displayName: "node_123" }, "未命名电脑")).toBe("未命名电脑");
    expect(presentNodeName(undefined, "未命名电脑")).toBe("未命名电脑");
  });

  it("does not leak hub node ids when the display name is missing", () => {
    expect(presentHubNodeName({ node_id: "node_123", display_name: "" }, "未命名电脑")).toBe("未命名电脑");
    expect(presentHubNodeName({ node_id: "node_123", display_name: "办公室电脑" }, "未命名电脑")).toBe("办公室电脑");
  });
});
