import { describe, expect, it } from "vitest";

import type { MemoryRecord } from "@/api/models";
import {
  categoryDisplayName,
  filterMemories,
  formatConfidencePercent,
} from "./memoryPresentation";

describe("memoryPresentation", () => {
  const sampleMemories: MemoryRecord[] = [
    {
      key: "preferred_editor",
      value: "VSCode with Vim mode",
      category: "workflow",
      importance: "core",
      confidence: 0.95,
    },
    {
      key: "default_git_branch",
      value: "master",
      category: "environment",
      importance: "relevant",
      confidence: 0.8,
    },
    {
      key: "report_language",
      value: "Chinese",
      category: "preference",
      importance: "core",
      confidence: 0.9,
    },
  ];

  it("filters memories by importance", () => {
    expect(filterMemories(sampleMemories, "all")).toHaveLength(3);
    expect(filterMemories(sampleMemories, "core")).toHaveLength(2);
    expect(filterMemories(sampleMemories, "relevant")).toHaveLength(1);
  });

  it("formats confidence percentages properly", () => {
    expect(formatConfidencePercent(0.95)).toBe("95%");
    expect(formatConfidencePercent(1.0)).toBe("100%");
    expect(formatConfidencePercent(0.0)).toBe("0%");
    expect(formatConfidencePercent(-0.1)).toBe("0%");
    expect(formatConfidencePercent(1.5)).toBe("100%");
  });

  it("maps categories to friendly Chinese names with fallback", () => {
    expect(categoryDisplayName("workflow")).toBe("工作流习惯");
    expect(categoryDisplayName("environment")).toBe("环境信息");
    expect(categoryDisplayName("preference")).toBe("个人偏好");
    expect(categoryDisplayName("custom_cat")).toBe("custom_cat");
  });
});
