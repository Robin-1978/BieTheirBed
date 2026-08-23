import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type PropsWithChildren } from "react";
import { router } from "expo-router";
import { AppState, Vibration } from "react-native";

import {
  loadTaskReminders,
  markExecutionRemindersRead,
  markAllTaskRemindersRead,
  markTaskReminderRead,
  mergeTaskReminder,
  storeTaskReminders,
  unreadTaskReminderIndex,
  type TaskReminder,
  type TaskReminderCategory,
} from "@/reminders/taskReminders";
import { useGateway } from "@/state/GatewayProvider";
import {
  configureTaskNotifications,
  loadNativePushRegistration,
  loadLastTaskNotificationResponse,
  subscribeNativePushToken,
  subscribeTaskNotificationResponses,
} from "@/notifications/taskNotifications";
import { useI18n } from "@/i18n";
import {
  acknowledgeHubNotification,
  listHubNotifications,
  registerPushToken,
  unregisterPushToken,
  type HubNotificationIntent,
} from "@/hub/hubClient";

type TaskReminderState = {
  reminders: TaskReminder[];
  activeReminder: TaskReminder | null;
  unreadCount: number;
  unreadExecutionIds: ReadonlySet<string>;
  unreadTaskIds: ReadonlySet<string>;
  dismissActive(): void;
  markRead(reminderId: string): void;
  markExecutionRead(executionId: string): void;
  setExecutionViewing(executionId: string | null): void;
  markAllRead(): void;
};

const Context = createContext<TaskReminderState | null>(null);

export function TaskReminderProvider({ children }: PropsWithChildren) {
  const { status } = useGateway();
  const { locale } = useI18n();
  const [reminders, setReminders] = useState<TaskReminder[]>([]);
  const [activeReminder, setActiveReminder] = useState<TaskReminder | null>(null);
  const appIsActiveRef = useRef(AppState.currentState === "active");
  const inboxCursorRef = useRef(0);
  const reconcileRef = useRef<Promise<void> | null>(null);
  const viewingExecutionRef = useRef("");

  const replaceAndStore = useCallback((transform: (current: TaskReminder[]) => TaskReminder[]) => {
    setReminders((current) => {
      const next = transform(current);
      if (next !== current) void storeTaskReminders(next);
      return next;
    });
  }, []);

  useEffect(() => {
    void configureTaskNotifications();
    void loadLastTaskNotificationResponse().then((data) => {
      if (data?.executionId) router.push({ pathname: "/task-executions/[id]", params: { id: data.executionId } });
      else if (data?.taskId) router.push({ pathname: "/tasks/[id]", params: { id: data.taskId } });
    });
    const subscription = subscribeTaskNotificationResponses((data) => {
      if (data.executionId) router.push({ pathname: "/task-executions/[id]", params: { id: data.executionId } });
      else if (data.taskId) router.push({ pathname: "/tasks/[id]", params: { id: data.taskId } });
    });
    return () => subscription.remove();
  }, []);

  const registerCurrentPushToken = useCallback(async (refreshedToken = "") => {
    if (status !== "ready") return;
    const registration = await loadNativePushRegistration();
    const token = refreshedToken || registration?.token || "";
    if (!token) return;
    await registerPushToken({
      token,
      locale,
      appVersion: registration?.appVersion ?? "",
    });
  }, [locale, status]);

  useEffect(() => {
    if (status !== "ready") return;
    void registerCurrentPushToken().catch(() => undefined);
    const subscription = subscribeNativePushToken((token) => {
      void registerCurrentPushToken(token).catch(() => undefined);
    });
    return () => subscription.remove();
  }, [registerCurrentPushToken, status]);

  useEffect(() => {
    void loadTaskReminders().then((stored) => {
      setReminders((current) => stored.reduce(mergeTaskReminder, current));
      const latestUnread = [...stored].reverse().find((reminder) => !reminder.read) ?? null;
      if (latestUnread) setActiveReminder((current) => current ?? latestUnread);
    });
  }, []);

  // Unpairing wipes the account scope (see GatewayProvider.removeConnection);
  // in-memory reminders must not outlive the identity they belong to.
  useEffect(() => {
    if (status !== "unpaired") return;
    void unregisterPushToken().catch(() => undefined);
    inboxCursorRef.current = 0;
    setReminders([]);
    setActiveReminder(null);
  }, [status]);

  const reminderFromIntent = useCallback((intent: HubNotificationIntent): TaskReminder | null => {
    const category: TaskReminderCategory | null = intent.category === "completed"
      ? "completed"
      : intent.category === "failed" || intent.category === "cancelled"
        ? "failed"
        : intent.category === "approval_required" || intent.category === "interaction_required"
          ? "approval"
          : null;
    if (!category || intent.work_kind !== "task") return null;
    const taskId = String(intent.deep_link.task_id || intent.work_id || "");
    const executionId = String(intent.deep_link.execution_id || intent.execution_id || "");
    if (!taskId) return null;
    return {
      reminderId: `intent:${intent.intent_id}`,
      feedEventId: intent.inbox_cursor,
      category,
      taskId,
      executionId,
      taskTitle: typeof intent.parameters.title === "string" ? intent.parameters.title : "小诺任务",
      occurredAt: intent.received_at,
      read: intent.acknowledged_at !== null || viewingExecutionRef.current === executionId,
    };
  }, []);

  const reconcileNotifications = useCallback(async () => {
    if (status !== "ready") return;
    if (reconcileRef.current) return reconcileRef.current;
    const operation = (async () => {
      let cursor = inboxCursorRef.current;
      for (let page = 0; page < 10; page += 1) {
        const result = await listHubNotifications(cursor);
        const incoming = result.notifications
          .map(reminderFromIntent)
          .filter((item): item is TaskReminder => item !== null);
        if (incoming.length) {
          replaceAndStore((current) => incoming.reduce(mergeTaskReminder, current));
          const latestUnread = [...incoming].reverse().find((item) => !item.read);
          if (latestUnread && appIsActiveRef.current) {
            setActiveReminder(latestUnread);
            Vibration.vibrate(45);
          }
        }
        cursor = result.nextCursor;
        inboxCursorRef.current = cursor;
        if (result.notifications.length < 100) break;
      }
    })();
    reconcileRef.current = operation;
    try {
      await operation;
    } finally {
      reconcileRef.current = null;
    }
  }, [reminderFromIntent, replaceAndStore, status]);

  useEffect(() => {
    if (status === "ready") void reconcileNotifications().catch(() => undefined);
  }, [reconcileNotifications, status]);

  useEffect(() => {
    const subscription = AppState.addEventListener("change", (state) => {
      appIsActiveRef.current = state === "active";
      if (state === "active") {
        void registerCurrentPushToken().catch(() => undefined);
        void reconcileNotifications().catch(() => undefined);
        setReminders((current) => {
          const latestUnread = [...current].reverse().find((reminder) => !reminder.read) ?? null;
          if (latestUnread) setActiveReminder(latestUnread);
          return current;
        });
      }
    });
    return () => subscription.remove();
  }, [reconcileNotifications, registerCurrentPushToken]);

  const markRead = useCallback((reminderId: string) => {
    replaceAndStore((current) => markTaskReminderRead(current, reminderId));
    if (reminderId.startsWith("intent:")) {
      void acknowledgeHubNotification(reminderId.slice(7)).catch(() => undefined);
    }
  }, [replaceAndStore]);

  const markExecutionRead = useCallback((executionId: string) => {
    for (const reminder of reminders) {
      if (!reminder.read && reminder.executionId === executionId && reminder.reminderId.startsWith("intent:")) {
        void acknowledgeHubNotification(reminder.reminderId.slice(7)).catch(() => undefined);
      }
    }
    replaceAndStore((current) => markExecutionRemindersRead(current, executionId));
    setActiveReminder((current) => current?.executionId === executionId ? null : current);
  }, [reminders, replaceAndStore]);

  const setExecutionViewing = useCallback((executionId: string | null) => {
    viewingExecutionRef.current = executionId ?? "";
    if (!executionId) return;
    for (const reminder of reminders) {
      if (!reminder.read && reminder.executionId === executionId && reminder.reminderId.startsWith("intent:")) {
        void acknowledgeHubNotification(reminder.reminderId.slice(7)).catch(() => undefined);
      }
    }
    replaceAndStore((current) => markExecutionRemindersRead(current, executionId));
    setActiveReminder((current) => current?.executionId === executionId ? null : current);
  }, [reminders, replaceAndStore]);

  const markAllRead = useCallback(() => {
    for (const reminder of reminders) {
      if (!reminder.read && reminder.reminderId.startsWith("intent:")) {
        void acknowledgeHubNotification(reminder.reminderId.slice(7)).catch(() => undefined);
      }
    }
    replaceAndStore(markAllTaskRemindersRead);
  }, [reminders, replaceAndStore]);

  const dismissActive = useCallback(() => setActiveReminder(null), []);

  const value = useMemo<TaskReminderState>(() => {
    const unread = reminders.filter((reminder) => !reminder.read);
    const index = unreadTaskReminderIndex(reminders);
    return {
      reminders,
      activeReminder,
      unreadCount: unread.length,
      unreadExecutionIds: index.executionIds,
      unreadTaskIds: index.taskIds,
      dismissActive,
      markRead,
      markExecutionRead,
      setExecutionViewing,
      markAllRead,
    };
  }, [activeReminder, dismissActive, markAllRead, markExecutionRead, markRead, reminders, setExecutionViewing]);

  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useTaskReminders(): TaskReminderState {
  const value = useContext(Context);
  if (!value) throw new Error("TaskReminderProvider is missing");
  return value;
}
