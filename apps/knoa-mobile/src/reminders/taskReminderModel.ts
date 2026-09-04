export type TaskReminderCategory = "completed" | "failed" | "approval";

export type TaskReminder = {
  reminderId: string;
  feedEventId: number;
  category: TaskReminderCategory;
  taskId: string;
  executionId: string;
  taskTitle: string;
  occurredAt: number;
  read: boolean;
};

const MAX_REMINDERS = 100;

export function mergeTaskReminder(
  reminders: TaskReminder[],
  incoming: TaskReminder,
): TaskReminder[] {
  if (reminders.some((reminder) => reminder.reminderId === incoming.reminderId)) {
    return reminders;
  }

  let next = reminders;
  if (incoming.category === "completed" || incoming.category === "failed" || incoming.category === "approval") {
    next = next.map((reminder) => {
      if (
        reminder.executionId === incoming.executionId &&
        reminder.category === "approval" &&
        !reminder.read
      ) {
        return { ...reminder, read: true };
      }
      return reminder;
    });
  }

  return [...next, incoming]
    .sort((left, right) => left.feedEventId - right.feedEventId)
    .slice(-MAX_REMINDERS);
}

export function markTaskReminderRead(
  reminders: TaskReminder[],
  reminderId: string,
): TaskReminder[] {
  return reminders.map((reminder) => reminder.reminderId === reminderId
    ? { ...reminder, read: true }
    : reminder);
}

export function markExecutionRemindersRead(
  reminders: TaskReminder[],
  executionId: string,
): TaskReminder[] {
  return reminders.map((reminder) => reminder.executionId === executionId && !reminder.read
    ? { ...reminder, read: true }
    : reminder);
}

export function markAllTaskRemindersRead(reminders: TaskReminder[]): TaskReminder[] {
  return reminders.map((reminder) => reminder.read ? reminder : { ...reminder, read: true });
}

export function unreadTaskReminderIndex(reminders: TaskReminder[]): {
  executionIds: ReadonlySet<string>;
  taskIds: ReadonlySet<string>;
} {
  const unread = reminders.filter((reminder) => !reminder.read);
  return {
    executionIds: new Set(unread.map((reminder) => reminder.executionId)),
    taskIds: new Set(unread.map((reminder) => reminder.taskId)),
  };
}

export function isTaskReminder(value: unknown): value is TaskReminder {
  if (!value || typeof value !== "object") return false;
  const reminder = value as Partial<TaskReminder>;
  return typeof reminder.reminderId === "string"
    && Number.isSafeInteger(reminder.feedEventId)
    && ["completed", "failed", "approval"].includes(String(reminder.category))
    && typeof reminder.taskId === "string"
    && typeof reminder.executionId === "string"
    && typeof reminder.taskTitle === "string"
    && typeof reminder.occurredAt === "number"
    && typeof reminder.read === "boolean";
}

export const TASK_REMINDER_LIMIT = MAX_REMINDERS;
