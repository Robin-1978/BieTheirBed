import Constants from "expo-constants";
import * as Notifications from "expo-notifications";
import { router } from "expo-router";
import { Platform } from "react-native";

import type { GatewayClient } from "@/api/gatewayClient";

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
  }),
});

export type PushRegistrationStatus =
  | "checking"
  | "registered"
  | "not_configured"
  | "permission_denied"
  | "token_failed"
  | "server_failed";

export type PushRegistrationResult = {
  status: PushRegistrationStatus;
  detail: string;
};

function expoProjectId(): string {
  const extra = Constants.expoConfig?.extra as
    | { eas?: { projectId?: unknown } }
    | undefined;
  const configured = extra?.eas?.projectId ?? Constants.easConfig?.projectId;
  return typeof configured === "string" ? configured.trim() : "";
}

function errorDetail(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export async function registerPush(
  client: GatewayClient,
  requestPermission = false,
): Promise<PushRegistrationResult> {
  const projectId = expoProjectId();
  if (!projectId) {
    return { status: "not_configured", detail: "Expo Project ID is missing" };
  }
  let permission: Notifications.NotificationPermissionsStatus;
  try {
    permission = await Notifications.getPermissionsAsync();
    if (permission.status !== "granted" && requestPermission) {
      permission = await Notifications.requestPermissionsAsync();
    }
  } catch (error) {
    return { status: "token_failed", detail: errorDetail(error) };
  }
  if (permission.status !== "granted") {
    return { status: "permission_denied", detail: permission.status };
  }
  if (Platform.OS === "android") {
    try {
      await Notifications.setNotificationChannelAsync("default", {
        name: "小诺任务通知",
        importance: Notifications.AndroidImportance.HIGH,
        sound: "default",
        vibrationPattern: [0, 250, 150, 250],
      });
    } catch (error) {
      return { status: "token_failed", detail: errorDetail(error) };
    }
  }
  let token: Notifications.ExpoPushToken;
  try {
    token = await Notifications.getExpoPushTokenAsync({ projectId });
  } catch (error) {
    return { status: "token_failed", detail: errorDetail(error) };
  }
  try {
    await client.registerPush(token.data);
  } catch (error) {
    return { status: "server_failed", detail: errorDetail(error) };
  }
  return { status: "registered", detail: "" };
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
