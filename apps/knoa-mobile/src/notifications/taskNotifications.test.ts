import { beforeEach, describe, expect, it, vi } from "vitest";

const native = vi.hoisted(() => ({
  granted: false,
  request: vi.fn(async () => ({ granted: true })),
  schedule: vi.fn(async () => "notification-1"),
  listener: vi.fn(),
}));

vi.mock("react-native", () => ({ Platform: { OS: "android" } }));
vi.mock("expo-notifications", () => ({
  AndroidImportance: { DEFAULT: 3 },
  setNotificationHandler: vi.fn(),
  setNotificationChannelAsync: vi.fn(async () => null),
  getPermissionsAsync: vi.fn(async () => ({ granted: native.granted })),
  requestPermissionsAsync: native.request,
  scheduleNotificationAsync: native.schedule,
  addNotificationResponseReceivedListener: (callback: unknown) => {
    native.listener(callback);
    return { remove: vi.fn() };
  },
  getLastNotificationResponseAsync: vi.fn(async () => null),
}));

import {
  presentTaskReminderNotification,
  requestTaskNotificationPermission,
} from "./taskNotifications";

beforeEach(() => {
  native.granted = false;
  native.request.mockClear();
  native.schedule.mockClear();
});

describe("task notifications", () => {
  it("requests permission and schedules an actionable task notification", async () => {
    await expect(requestTaskNotificationPermission()).resolves.toBe(true);
    native.granted = true;
    await expect(presentTaskReminderNotification({
      taskId: "task-1",
      executionId: "execution-1",
      title: "整理文件",
      body: "任务已完成",
    })).resolves.toBe(true);
    expect(native.schedule).toHaveBeenCalledWith(expect.objectContaining({
      content: expect.objectContaining({
        title: "整理文件",
        data: { taskId: "task-1", executionId: "execution-1" },
      }),
      trigger: { channelId: "knoa-task-events" },
    }));
  });
});
