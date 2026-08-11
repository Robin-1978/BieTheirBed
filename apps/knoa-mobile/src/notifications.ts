import Constants from "expo-constants";
import * as Notifications from "expo-notifications";
import { router } from "expo-router";

import type { GatewayClient } from "@/api/gatewayClient";

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
  }),
});

export async function registerPush(client: GatewayClient, requestPermission = false): Promise<boolean> {
  const projectId = Constants.easConfig?.projectId;
  if (!projectId) return false;
  let permission = await Notifications.getPermissionsAsync();
  if (permission.status !== "granted" && requestPermission) {
    permission = await Notifications.requestPermissionsAsync();
  }
  if (permission.status !== "granted") return false;
  const token = await Notifications.getExpoPushTokenAsync({ projectId });
  await client.registerPush(token.data);
  return true;
}

export async function sendTestNotification(title = "Knoa", body = "Notifications are working on this phone."): Promise<void> {
  await Notifications.scheduleNotificationAsync({
    content: {
      title,
      body,
    },
    trigger: null,
  });
}

export function installNotificationNavigation(): { remove(): void } {
  const navigate = (response: Notifications.NotificationResponse) => {
    const data = response.notification.request.content.data ?? {};
    const executionId = typeof data.execution_id === "string" ? data.execution_id : "";
    const taskId = typeof data.task_id === "string" ? data.task_id : "";
    if (executionId) router.push(`/task-executions/${executionId}`);
    else if (taskId) router.push(`/tasks/${taskId}`);
  };
  void Notifications.getLastNotificationResponseAsync().then((response) => {
    if (!response) return;
    navigate(response);
    return Notifications.clearLastNotificationResponseAsync();
  });
  return Notifications.addNotificationResponseReceivedListener(navigate);
}
