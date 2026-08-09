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

export async function registerPush(client: GatewayClient): Promise<boolean> {
  const projectId = Constants.easConfig?.projectId;
  if (!projectId) return false;
  let permission = await Notifications.getPermissionsAsync();
  if (permission.status !== "granted") {
    permission = await Notifications.requestPermissionsAsync();
  }
  if (permission.status !== "granted") return false;
  const token = await Notifications.getExpoPushTokenAsync({ projectId });
  await client.registerPush(token.data);
  return true;
}

export function installNotificationNavigation(): { remove(): void } {
  return Notifications.addNotificationResponseReceivedListener((response) => {
    const data = response.notification.request.content.data ?? {};
    const taskId = typeof data.task_id === "string" ? data.task_id : "";
    if (taskId) router.push(`/tasks/${taskId}`);
  });
}
