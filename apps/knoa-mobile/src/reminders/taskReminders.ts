import { File, Paths } from "expo-file-system";

import {
  isTaskReminder,
  TASK_REMINDER_LIMIT,
  type TaskReminder,
} from "@/reminders/taskReminderModel";

export {
  markExecutionRemindersRead,
  markAllTaskRemindersRead,
  markTaskReminderRead,
  mergeTaskReminder,
  unreadTaskReminderIndex,
  type TaskReminder,
  type TaskReminderCategory,
} from "@/reminders/taskReminderModel";

const STORE_VERSION = 1;

type StoredReminders = {
  version: typeof STORE_VERSION;
  reminders: TaskReminder[];
};

export async function loadTaskReminders(): Promise<TaskReminder[]> {
  const file = reminderFile();
  if (!file.exists) return [];
  try {
    const value = JSON.parse(await file.text()) as Partial<StoredReminders>;
    if (value.version !== STORE_VERSION || !Array.isArray(value.reminders)) return [];
    return value.reminders.filter(isTaskReminder).slice(-TASK_REMINDER_LIMIT);
  } catch {
    return [];
  }
}

export async function storeTaskReminders(reminders: TaskReminder[]): Promise<void> {
  const file = reminderFile();
  if (!file.exists) file.create({ intermediates: true, overwrite: false });
  file.write(JSON.stringify({
    version: STORE_VERSION,
    reminders: reminders.slice(-TASK_REMINDER_LIMIT),
  } satisfies StoredReminders));
}

export async function clearTaskReminders(): Promise<void> {
  const file = reminderFile();
  if (file.exists) file.delete();
}

function reminderFile(): File {
  return new File(Paths.document, "task-reminders-v1.json");
}
