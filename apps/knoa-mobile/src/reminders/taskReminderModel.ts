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
  nodeId?: string;
  nodeName?: string;
};

const MAX_REMINDERS = 100;

export function mergeTaskReminder(
  reminders: TaskReminder[],
  incoming: TaskReminder,
): TaskReminder[] {
  let found = false;
  let next = reminders.map((reminder) => {
    if (reminder.reminderId === incoming.reminderId) {
      found = true;
      // 服务端或远端多机已读状态同步覆盖（只增不退：未读可变已读，已读不回退为未读）
      if (!reminder.read && incoming.read) {
        return { ...reminder, read: true };
      }
      return reminder;
    }
    return reminder;
  });

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

  // 节点已读广播 / 全局已读标记同步：
  // 若传入的通知在服务端/Hub端已经被确认已读（incoming.read === true），
  // 必须确保本地已有相同 reminderId 或相同 executionId 的待办也被对齐更新为已读
  if (incoming.read) {
    next = next.map((reminder) => {
      if (
        (reminder.reminderId === incoming.reminderId ||
         (incoming.executionId && reminder.executionId === incoming.executionId)) &&
        !reminder.read
      ) {
        return { ...reminder, read: true };
      }
      return reminder;
    });
  }

  if (found) {
    return next;
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

export function markAllTaskRemindersRead(
  reminders: TaskReminder[],
  nodeId?: string,
): TaskReminder[] {
  return reminders.map((reminder) => {
    if (reminder.read) return reminder;
    if (!nodeId || !reminder.nodeId || reminder.nodeId === nodeId) {
      return { ...reminder, read: true };
    }
    return reminder;
  });
}

export function unreadTaskReminderIndex(
  reminders: TaskReminder[],
  nodeId?: string,
): {
  executionIds: ReadonlySet<string>;
  taskIds: ReadonlySet<string>;
  count: number;
} {
  const unread = reminders.filter((reminder) => {
    if (reminder.read) return false;
    if (nodeId && reminder.nodeId && reminder.nodeId !== nodeId) return false;
    return true;
  });
  return {
    executionIds: new Set(unread.map((reminder) => reminder.executionId)),
    taskIds: new Set(unread.map((reminder) => reminder.taskId)),
    count: unread.length,
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
    && typeof reminder.read === "boolean"
    && (reminder.nodeId === undefined || typeof reminder.nodeId === "string")
    && (reminder.nodeName === undefined || typeof reminder.nodeName === "string");
}

export const TASK_REMINDER_LIMIT = MAX_REMINDERS;
