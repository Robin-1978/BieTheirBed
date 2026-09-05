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

  it("marks prior unread approval reminders as read when a terminal state arrives for the same execution", () => {
    const approvalReminder: TaskReminder = {
      reminderId: "intent:approval-1",
      feedEventId: 10,
      category: "approval",
      taskId: "task-a",
      executionId: "execution-1",
      taskTitle: "GitLab CI",
      occurredAt: 10,
      read: false,
    };
    const completedReminder: TaskReminder = {
      reminderId: "intent:completed-1",
      feedEventId: 20,
      category: "completed",
      taskId: "task-a",
      executionId: "execution-1",
      taskTitle: "GitLab CI",
      occurredAt: 20,
      read: false,
    };
    const result = mergeTaskReminder([approvalReminder], completedReminder);
    expect(result).toHaveLength(2);
    expect(result.find((item) => item.reminderId === "intent:approval-1")?.read).toBe(true);
    expect(result.find((item) => item.reminderId === "intent:completed-1")?.read).toBe(false);
  });

  it("supersedes prior unread approval reminders when a new approval arrives for the same execution", () => {
    const approval1: TaskReminder = {
      reminderId: "intent:approval-1",
      feedEventId: 10,
      category: "approval",
      taskId: "task-a",
      executionId: "execution-1",
      taskTitle: "GitLab CI",
      occurredAt: 10,
      read: false,
    };
    const approval2: TaskReminder = {
      reminderId: "intent:approval-2",
      feedEventId: 15,
      category: "approval",
      taskId: "task-a",
      executionId: "execution-1",
      taskTitle: "GitLab CI",
      occurredAt: 15,
      read: false,
    };
    const result = mergeTaskReminder([approval1], approval2);
    expect(result).toHaveLength(2);
    expect(result.find((item) => item.reminderId === "intent:approval-1")?.read).toBe(true);
    expect(result.find((item) => item.reminderId === "intent:approval-2")?.read).toBe(false);
  });

  it("filters unread task reminders strictly by nodeId", () => {
    const list: TaskReminder[] = [
      { ...reminder(1), nodeId: "node-a", taskId: "task-1" },
      { ...reminder(2), nodeId: "node-b", taskId: "task-2" },
      { ...reminder(3), nodeId: "node-a", taskId: "task-3" },
    ];
    const indexA = unreadTaskReminderIndex(list, "node-a");
    expect(indexA.count).toBe(2);
    expect([...indexA.taskIds]).toEqual(["task-1", "task-3"]);

    const indexB = unreadTaskReminderIndex(list, "node-b");
    expect(indexB.count).toBe(1);
    expect([...indexB.taskIds]).toEqual(["task-2"]);

    const indexC = unreadTaskReminderIndex(list, "node-c");
    expect(indexC.count).toBe(0);
  });

  it("marks unread task reminders as read selectively by nodeId", () => {
    const list: TaskReminder[] = [
      { ...reminder(1), nodeId: "node-a" },
      { ...reminder(2), nodeId: "node-b" },
    ];
    const updated = markAllTaskRemindersRead(list, "node-a");
    expect(updated[0]?.read).toBe(true);
    expect(updated[1]?.read).toBe(false);

    const allRead = markAllTaskRemindersRead(list);
    expect(allRead.every((item) => item.read)).toBe(true);
  });
});
