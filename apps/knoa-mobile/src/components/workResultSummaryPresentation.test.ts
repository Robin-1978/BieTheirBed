import { describe, expect, it } from "vitest";

import { structuredWorkChanges } from "./workResultSummaryPresentation";

describe("structured work changes", () => {
  it("uses only explicit structured tool output", () => {
    const execution = {
      final_result: "I changed secret.txt", failure_code: "", trace: { entries: [
        { entry_type: "content", content: "changed guessed.txt" },
        { entry_type: "tool_result", tool_result: { output: { changes: [{ label: "Updated config", reference: "config/revision-2" }] } } },
      ] },
    } as never;
    expect(structuredWorkChanges(execution)).toEqual([{ label: "Updated config", reference: "config/revision-2" }]);
  });
});
