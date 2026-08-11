import { describe, expect, it } from "vitest";

import type { TaskReminder } from "./taskReminderModel";
import {
  markAllTaskRemindersRead,
  markTaskReminderRead,
  mergeTaskReminder,
} from "./taskReminderModel";

function reminder(feedEventId: number): TaskReminder {
  return {
    reminderId: `feed:${feedEventId}`,
    feedEventId,
    category: "completed",
    taskId: "task-a",
    executionId: `execution-${feedEventId}`,
    taskTitle: "日报",
    occurredAt: feedEventId,
    read: false,
  };
}

describe("task reminders", () => {
  it("deduplicates replayed feed events and keeps feed order", () => {
    const result = mergeTaskReminder(
      mergeTaskReminder([reminder(3)], reminder(5)),
      reminder(3),
    );
    expect(result.map((item) => item.feedEventId)).toEqual([3, 5]);
  });

  it("marks one or all reminders as read", () => {
    const source = [reminder(3), reminder(5)];
    expect(markTaskReminderRead(source, "feed:3").map((item) => item.read)).toEqual([true, false]);
    expect(markAllTaskRemindersRead(source).every((item) => item.read)).toBe(true);
  });
});
