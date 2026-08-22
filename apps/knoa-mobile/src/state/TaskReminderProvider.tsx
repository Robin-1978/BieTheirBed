import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type PropsWithChildren } from "react";
import { AppState, Vibration } from "react-native";

import type { PrincipalTaskEvent } from "@/api/models";
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

type ActionableEvent = {
  category: TaskReminderCategory;
  policyKey: "completed" | "failed" | "waiting_approval";
};

const ACTIONABLE_EVENTS: Record<string, ActionableEvent | undefined> = {
  completed: { category: "completed", policyKey: "completed" },
  failed: { category: "failed", policyKey: "failed" },
  approval_requested: { category: "approval", policyKey: "waiting_approval" },
  interaction_requested: { category: "approval", policyKey: "waiting_approval" },
};

const Context = createContext<TaskReminderState | null>(null);

export function TaskReminderProvider({ children }: PropsWithChildren) {
  const { latestEvent, runAuthenticated, subscribeEvents } = useGateway();
  const [reminders, setReminders] = useState<TaskReminder[]>([]);
  const [activeReminder, setActiveReminder] = useState<TaskReminder | null>(null);
  const processQueueRef = useRef<Promise<void>>(Promise.resolve());
  const appIsActiveRef = useRef(AppState.currentState === "active");
  const seenEventIdsRef = useRef(new Set<number>());
  const viewingExecutionRef = useRef("");

  const replaceAndStore = useCallback((transform: (current: TaskReminder[]) => TaskReminder[]) => {
    setReminders((current) => {
      const next = transform(current);
      if (next !== current) void storeTaskReminders(next);
      return next;
    });
  }, []);

  useEffect(() => {
    void loadTaskReminders().then((stored) => {
      setReminders((current) => stored.reduce(mergeTaskReminder, current));
      const latestUnread = [...stored].reverse().find((reminder) => !reminder.read) ?? null;
      if (latestUnread) setActiveReminder((current) => current ?? latestUnread);
    });
  }, []);

  useEffect(() => {
    const subscription = AppState.addEventListener("change", (state) => {
      appIsActiveRef.current = state === "active";
      if (state === "active") {
        setReminders((current) => {
          const latestUnread = [...current].reverse().find((reminder) => !reminder.read) ?? null;
          if (latestUnread) setActiveReminder(latestUnread);
          return current;
        });
      }
    });
    return () => subscription.remove();
  }, []);

  const processEvent = useCallback(async (feed: PrincipalTaskEvent, attempt = 0): Promise<void> => {
    const actionable = ACTIONABLE_EVENTS[feed.event.event_type];
    if (!actionable) return;
    try {
      const execution = await runAuthenticated(
        (client) => client.getTaskExecution(feed.event.task_id),
      );
      const task = await runAuthenticated(
        (client) => client.getTask(execution.task_id),
      );
      if (!(task.notification_policy[actionable.policyKey] ?? true)) return;
      const read = viewingExecutionRef.current === execution.execution_id;
      const reminder: TaskReminder = {
        reminderId: `feed:${feed.feed_event_id}`,
        feedEventId: feed.feed_event_id,
        category: actionable.category,
        taskId: task.task_id,
        executionId: execution.execution_id,
        taskTitle: task.title || task.goal,
        occurredAt: feed.event.occurred_at,
        read,
      };
      replaceAndStore((current) => mergeTaskReminder(current, reminder));
      if (!read && appIsActiveRef.current) {
        setActiveReminder(reminder);
        Vibration.vibrate(45);
      }
    } catch {
      if (attempt < 3) {
        await new Promise((resolve) => setTimeout(resolve, 300 * (attempt + 1)));
        await processEvent(feed, attempt + 1);
      }
    }
  }, [replaceAndStore, runAuthenticated]);

  const enqueueEvent = useCallback((feed: PrincipalTaskEvent) => {
    if (seenEventIdsRef.current.has(feed.feed_event_id)) return;
    seenEventIdsRef.current.add(feed.feed_event_id);
    if (seenEventIdsRef.current.size > 256) {
      const oldest = seenEventIdsRef.current.values().next().value;
      if (typeof oldest === "number") seenEventIdsRef.current.delete(oldest);
    }
    processQueueRef.current = processQueueRef.current.then(
      () => processEvent(feed),
      () => processEvent(feed),
    );
  }, [processEvent]);

  useEffect(() => subscribeEvents(enqueueEvent), [enqueueEvent, subscribeEvents]);
  useEffect(() => {
    if (latestEvent) enqueueEvent(latestEvent);
  }, [enqueueEvent, latestEvent]);

  const markRead = useCallback((reminderId: string) => {
    replaceAndStore((current) => markTaskReminderRead(current, reminderId));
  }, [replaceAndStore]);

  const markExecutionRead = useCallback((executionId: string) => {
    replaceAndStore((current) => markExecutionRemindersRead(current, executionId));
    setActiveReminder((current) => current?.executionId === executionId ? null : current);
  }, [replaceAndStore]);

  const setExecutionViewing = useCallback((executionId: string | null) => {
    viewingExecutionRef.current = executionId ?? "";
    if (!executionId) return;
    replaceAndStore((current) => markExecutionRemindersRead(current, executionId));
    setActiveReminder((current) => current?.executionId === executionId ? null : current);
  }, [replaceAndStore]);

  const markAllRead = useCallback(() => {
    replaceAndStore(markAllTaskRemindersRead);
  }, [replaceAndStore]);

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
