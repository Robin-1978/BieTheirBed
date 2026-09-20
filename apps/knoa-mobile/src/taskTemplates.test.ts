import { describe, expect, it } from "vitest";

import { shouldConfirmTemplateOverwrite } from "./taskTemplates";

describe("shouldConfirmTemplateOverwrite", () => {
  it("empty form needs no confirm", () => {
    expect(shouldConfirmTemplateOverwrite("", "")).toBe(false);
    expect(shouldConfirmTemplateOverwrite("  ", "\n ")).toBe(false);
  });

  it("any existing input requires confirm", () => {
    expect(shouldConfirmTemplateOverwrite("标题", "")).toBe(true);
    expect(shouldConfirmTemplateOverwrite("", "目标")).toBe(true);
    expect(shouldConfirmTemplateOverwrite("标题", "目标")).toBe(true);
  });
});
