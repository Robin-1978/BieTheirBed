import { describe, expect, it } from "vitest";
import { blockedPreflightMessages, preflightCheckMessage } from "./preflightPresentation";

describe("preflight presentation", () => {
  it("hides local paths from runtime failures", () => {
    expect(preflightCheckMessage({ check_id: "runtime", status: "blocked", detail: "No such file: /home/robin/.local/share/knoa/workspace", recommended_action: "configure" })).toContain("Node Console");
  });
  it("returns only blocked checks for execution blocking", () => {
    expect(blockedPreflightMessages([
      { check_id: "goal", status: "warning", detail: "目标较短", recommended_action: "none" },
      { check_id: "runtime", status: "blocked", detail: "Runtime 未就绪", recommended_action: "configure" },
    ])).toEqual(["Runtime 未就绪"]);
  });
});
