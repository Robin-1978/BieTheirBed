import type { TaskLaunchPolicy } from "./api/models";

function scheduledBase(): TaskLaunchPolicy {
  return { kind: "immediate", schedule_type: null, run_at: null, interval_seconds: null, cron: "", timezone: "Asia/Shanghai", event_source: "", source_config: {} };
}

export type ParsedSchedule =
  | { kind: "daily"; hour: number; minute: number; policy: TaskLaunchPolicy }
  | { kind: "weekly"; weekday: number; hour: number; minute: number; policy: TaskLaunchPolicy }
  | { kind: "interval"; intervalSeconds: number; policy: TaskLaunchPolicy };

const CN_NUM: Record<string, number> = {
  "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
  "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
  "十一": 11, "十二": 12,
};

const WEEKDAY: Record<string, number> = {
  "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
  "日": 0, "天": 0,
  "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6, "sun": 0,
};

function cnHourToken(token: string): number | null {
  const arabic = Number(token);
  if (Number.isFinite(arabic) && token.trim() !== "") return arabic;
  const clean = token.trim();
  if (CN_NUM[clean] !== undefined) return CN_NUM[clean];
  return null;
}

/** 从一句话中抽时间：支持 18:30 / 18点30分 / 18点半 / 18点 / 早上9点 / 下午3点 / 晚上8点半。 */
export function extractClockTime(text: string): { hour: number; minute: number } | null {
  // 18:30 / 9:05
  const hm = text.match(/(\d{1,2})\s*[:：]\s*(\d{1,2})/);
  if (hm) {
    const hour = Number(hm[1]);
    const minute = Number(hm[2]);
    if (hour >= 0 && hour <= 23 && minute >= 0 && minute <= 59) return { hour, minute };
  }
  // 中文：[早上|上午|中午|下午|晚上]? X 点 [半|MM分|MM]
  const cn = text.match(/(早上|上午|中午|下午|晚上|凌晨)?\s*([0-9一二两三四五六七八九十]{1,3})\s*点\s*(半|([0-9]{1,2})\s*分?)?/);
  if (cn) {
    const period = cn[1] ?? "";
    let hour = cnHourToken(cn[2] ?? "");
    if (hour === null) return null;
    let minute = 0;
    if (cn[3] === "半") minute = 30;
    else if (cn[4] !== undefined) minute = Number(cn[4]);
    if ((period === "下午" || period === "晚上") && hour >= 1 && hour <= 11) hour += 12;
    if (period === "中午" && hour >= 1 && hour <= 11) hour += 12;
    if (hour < 0 || hour > 23 || minute < 0 || minute > 59) return null;
    return { hour, minute };
  }
  return null;
}

/**
 * 从自然语言建任务输入中解析调度意图。
 * 支持：每天/每日(+时间)、每周X(+时间)、每N分钟/小时、每小时、every day/daily。
 * 无调度意图返回 null（保持立即执行）。
 */
export function parseScheduleFromPrompt(raw: string): ParsedSchedule | null {
  const text = raw.toLowerCase();

  // 每 N 分钟 / 每 N 小时 / 每小时
  const intervalCn = text.match(/每\s*(\d+)\s*(分钟|小时|天)/);
  if (intervalCn) {
    const n = Number(intervalCn[1]);
    const unit = intervalCn[2];
    if (n > 0) {
      const seconds = unit === "分钟" ? n * 60 : unit === "小时" ? n * 3600 : n * 86400;
      if (seconds >= 60 && seconds <= 31536000) {
        return {
          kind: "interval",
          intervalSeconds: seconds,
          policy: { ...scheduledBase(), kind: "scheduled", schedule_type: "interval", interval_seconds: seconds },
        };
      }
    }
  }
  if (/每小时|every hour/.test(text)) {
    return {
      kind: "interval",
      intervalSeconds: 3600,
      policy: { ...scheduledBase(), kind: "scheduled", schedule_type: "interval", interval_seconds: 3600 },
    };
  }

  // 每周 X
  const weekly = text.match(/每周\s*([一二三四五六日天mon tue wed thu fri sat sun]+)/i);
  if (weekly) {
    const key = (weekly[1] ?? "").trim().toLowerCase();
    const first = Object.keys(WEEKDAY).find((k) => key.startsWith(k));
    const weekday = first !== undefined ? WEEKDAY[first] : undefined;
    if (weekday !== undefined) {
      const clock = extractClockTime(raw) ?? { hour: 9, minute: 0 };
      return {
        kind: "weekly",
        weekday,
        hour: clock.hour,
        minute: clock.minute,
        policy: {
          ...scheduledBase(),
          kind: "scheduled",
          schedule_type: "cron",
          cron: `${clock.minute} ${clock.hour} * * ${weekday}`,
        },
      };
    }
  }

  // 每天 / 每日 / every day / daily
  if (/每天|每日|every day|daily/.test(text)) {
    const clock = extractClockTime(raw) ?? { hour: 9, minute: 0 };
    return {
      kind: "daily",
      hour: clock.hour,
      minute: clock.minute,
      policy: {
        ...scheduledBase(),
        kind: "scheduled",
        schedule_type: "cron",
        cron: `${clock.minute} ${clock.hour} * * *`,
      },
    };
  }

  return null;
}
