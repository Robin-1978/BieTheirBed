import { describe, expect, it } from "vitest";

import type { TaskReminder } from "./taskReminderModel";
import {
  markExecutionRemindersRead,
  markAllTaskRemindersRead,
  markTaskReminderRead,
  mergeTaskReminder,
  unreadTaskReminderIndex,
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

  it("marks only reminders belonging to the opened execution", () => {
    const source = [reminder(3), reminder(5)];
    expect(markExecutionRemindersRead(source, "execution-5").map((item) => item.read)).toEqual([false, true]);
  });

  it("indexes unread executions and their owning tasks without duplicates", () => {
    const source = [
      reminder(3),
      { ...reminder(4), taskId: "task-b", executionId: "execution-3" },
      { ...reminder(5), taskId: "task-c", read: true },
    ];
    const index = unreadTaskReminderIndex(source);
    expect([...index.executionIds]).toEqual(["execution-3"]);
    expect([...index.taskIds]).toEqual(["task-a", "task-b"]);
  });
});
