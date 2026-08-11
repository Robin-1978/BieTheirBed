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
  return [...reminders, incoming]
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

export function markAllTaskRemindersRead(reminders: TaskReminder[]): TaskReminder[] {
  return reminders.map((reminder) => reminder.read ? reminder : { ...reminder, read: true });
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
