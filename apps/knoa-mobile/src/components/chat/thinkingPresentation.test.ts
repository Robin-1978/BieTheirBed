import { describe, expect, it } from "vitest";
import { formatThinkingDisplay } from "./thinkingPresentation";

describe("thinkingPresentation", () => {
  it("returns null when reasoning is empty or whitespace", () => {
    expect(formatThinkingDisplay("", false)).toBeNull();
    expect(formatThinkingDisplay("   \n\t  ", false)).toBeNull();
  });

  it("formats thinking state when agent is active", () => {
    const result = formatThinkingDisplay("Analyzing input parameters", true);
    expect(result).not.toBeNull();
    expect(result?.isThinking).toBe(true);
    expect(result?.titleKey).toBe("chat.thinking");
    expect(result?.cleanedText).toBe("Analyzing input parameters");
  });

  it("formats completed state when agent has completed thought", () => {
    const result = formatThinkingDisplay("Plan established", false);
    expect(result).not.toBeNull();
    expect(result?.isThinking).toBe(false);
    expect(result?.titleKey).toBe("chat.thoughtCompleted");
    expect(result?.cleanedText).toBe("Plan established");
  });
});
