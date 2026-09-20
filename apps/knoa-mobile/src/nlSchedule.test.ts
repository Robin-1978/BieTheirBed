import { describe, expect, it } from "vitest";

import { extractClockTime, parseScheduleFromPrompt } from "./nlSchedule";

describe("extractClockTime", () => {
  it("parses HH:MM", () => {
    expect(extractClockTime("每天18:30汇总")).toEqual({ hour: 18, minute: 30 });
  });

  it("parses Chinese 点半 and 下午 conversion", () => {
    expect(extractClockTime("每天早上9点检查")).toEqual({ hour: 9, minute: 0 });
    expect(extractClockTime("每天下午3点半开会")).toEqual({ hour: 15, minute: 30 });
    expect(extractClockTime("晚上8点提醒我")).toEqual({ hour: 20, minute: 0 });
  });

  it("returns null without time", () => {
    expect(extractClockTime("帮我整理桌面")).toBeNull();
  });
});

describe("parseScheduleFromPrompt", () => {
  it("daily with time", () => {
    const parsed = parseScheduleFromPrompt("每天18:30汇总CI失败项");
    expect(parsed?.kind).toBe("daily");
    if (parsed?.kind === "daily") {
      expect(parsed.hour).toBe(18);
      expect(parsed.minute).toBe(30);
      expect(parsed.policy.cron).toBe("30 18 * * *");
    }
  });

  it("daily defaults to 9:00", () => {
    const parsed = parseScheduleFromPrompt("每天检查CI");
    expect(parsed?.kind).toBe("daily");
    if (parsed?.kind === "daily") expect(parsed.policy.cron).toBe("0 9 * * *");
  });

  it("weekly Monday morning", () => {
    const parsed = parseScheduleFromPrompt("每周一早上9点出周报");
    expect(parsed?.kind).toBe("weekly");
    if (parsed?.kind === "weekly") {
      expect(parsed.weekday).toBe(1);
      expect(parsed.policy.cron).toBe("0 9 * * 1");
    }
  });

  it("interval hours", () => {
    const parsed = parseScheduleFromPrompt("每3小时检查一次服务");
    expect(parsed?.kind).toBe("interval");
    if (parsed?.kind === "interval") expect(parsed.intervalSeconds).toBe(10800);
  });

  it("immediate prompt returns null", () => {
    expect(parseScheduleFromPrompt("立刻帮我整理桌面文件")).toBeNull();
  });
});
