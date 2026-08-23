import { beforeEach, describe, expect, it, vi } from "vitest";

const native = vi.hoisted(() => ({
  granted: false,
  request: vi.fn(async () => ({ granted: true })),
  schedule: vi.fn(async (_request: { content?: { data?: unknown } }) => "notification-1"),
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
  sendTestTaskNotification,
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

  it("sends a test notification without task data so taps do not deep-link", async () => {
    native.granted = true;
    await expect(sendTestTaskNotification("小诺测试通知", "在线提醒工作正常。")).resolves.toBe(true);
    expect(native.schedule).toHaveBeenCalledWith(expect.objectContaining({
      content: expect.objectContaining({ title: "小诺测试通知", body: "在线提醒工作正常。" }),
      trigger: { channelId: "knoa-task-events" },
    }));
    expect(native.schedule.mock.calls.at(-1)?.[0]?.content?.data).toBeUndefined();
  });

  it("refuses to notify without permission", async () => {
    await expect(presentTaskReminderNotification({
      taskId: "task-1",
      executionId: "execution-1",
      title: "整理文件",
      body: "任务已完成",
    })).resolves.toBe(false);
    expect(native.schedule).not.toHaveBeenCalled();
  });
});
