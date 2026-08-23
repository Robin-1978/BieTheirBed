import { Platform } from "react-native";
import * as Notifications from "expo-notifications";

export const TASK_NOTIFICATION_CHANNEL = "knoa-task-events";

let configured = false;

export async function configureTaskNotifications(): Promise<void> {
  if (configured) return;
  try {
    Notifications.setNotificationHandler({
      handleNotification: async () => ({
        shouldShowBanner: true,
        shouldShowList: true,
        shouldPlaySound: false,
        shouldSetBadge: true,
      }),
    });
    if (Platform.OS === "android") {
      await Notifications.setNotificationChannelAsync(TASK_NOTIFICATION_CHANNEL, {
        name: "小诺任务",
        importance: Notifications.AndroidImportance.DEFAULT,
        vibrationPattern: [0, 180],
        sound: "default",
      });
    }
  } catch {
    // Native notification support is optional in development/web builds.
  } finally {
    // Avoid retry loops on platforms where native notifications are not
    // available, while keeping all public helpers best-effort.
    configured = true;
  }
}

export async function requestTaskNotificationPermission(): Promise<boolean> {
  try {
    await configureTaskNotifications();
    const current = await Notifications.getPermissionsAsync();
    if (current.granted) return true;
    const requested = await Notifications.requestPermissionsAsync();
    return requested.granted;
  } catch {
    return false;
  }
}

export async function hasTaskNotificationPermission(): Promise<boolean> {
  try {
    await configureTaskNotifications();
    return (await Notifications.getPermissionsAsync()).granted;
  } catch {
    return false;
  }
}

export async function presentTaskReminderNotification(input: {
  taskId: string;
  executionId: string;
  title: string;
  body: string;
}): Promise<boolean> {
  try {
    await configureTaskNotifications();
    const permissions = await Notifications.getPermissionsAsync();
    if (!permissions.granted) return false;
    await Notifications.scheduleNotificationAsync({
      content: {
        title: input.title,
        body: input.body,
        data: { taskId: input.taskId, executionId: input.executionId },
        sound: "default",
      },
      trigger: Platform.OS === "android" ? { channelId: TASK_NOTIFICATION_CHANNEL } : null,
    });
    return true;
  } catch {
    return false;
  }
}

/** Test notification carries no task payload so tapping it never deep-links. */
export async function sendTestTaskNotification(title: string, body: string): Promise<boolean> {
  try {
    await configureTaskNotifications();
    const permissions = await Notifications.getPermissionsAsync();
    if (!permissions.granted) return false;
    await Notifications.scheduleNotificationAsync({
      content: { title, body, sound: "default" },
      trigger: Platform.OS === "android" ? { channelId: TASK_NOTIFICATION_CHANNEL } : null,
    });
    return true;
  } catch {
    return false;
  }
}

export function subscribeTaskNotificationResponses(
  listener: (data: { taskId?: string; executionId?: string }) => void,
): { remove(): void } {
  return Notifications.addNotificationResponseReceivedListener((response) => {
    const data = response.notification.request.content.data;
    if (!data || typeof data !== "object") return;
    listener({
      taskId: typeof data.taskId === "string" ? data.taskId : undefined,
      executionId: typeof data.executionId === "string" ? data.executionId : undefined,
    });
  });
}

export async function loadLastTaskNotificationResponse(): Promise<{ taskId?: string; executionId?: string } | null> {
  try {
    const response = await Notifications.getLastNotificationResponseAsync();
    const data = response?.notification.request.content.data;
    if (!data || typeof data !== "object") return null;
    return {
      taskId: typeof data.taskId === "string" ? data.taskId : undefined,
      executionId: typeof data.executionId === "string" ? data.executionId : undefined,
    };
  } catch {
    return null;
  }
}
