import { describe, expect, it } from "vitest";

import { countdownSeconds, enrollmentShortCode, formatCountdown } from "./enrollmentPresentation";

describe("enrollmentShortCode", () => {
  it("groups first 8 chars", () => {
    expect(enrollmentShortCode("abcdef123456")).toBe("ABCD-EF12");
  });

  it("handles short ids", () => {
    expect(enrollmentShortCode("ab")).toBe("AB••");
  });
});

describe("countdown", () => {
  it("clamps at zero", () => {
    expect(countdownSeconds(1000, 999.2)).toBe(0);
    expect(countdownSeconds(1000, 1200)).toBe(0);
    expect(countdownSeconds(1000, 400)).toBe(600);
  });

  it("formats mm:ss", () => {
    expect(formatCountdown(600)).toBe("10:00");
    expect(formatCountdown(65)).toBe("01:05");
    expect(formatCountdown(0)).toBe("00:00");
  });
});
