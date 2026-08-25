import * as Crypto from "expo-crypto";
import * as SecureStore from "expo-secure-store";

import { immediatePolicy } from "@/components/TaskLaunchEditor";
import type { useGateway } from "@/state/GatewayProvider";

const STORAGE_KEY = "knoa.onboarding.welcome-health.v1";

type GatewayLike = Pick<
  ReturnType<typeof useGateway>,
  "status" | "defaultAgentId" | "runAuthenticated"
>;

type WelcomeHealthRecord = Record<string, true>;

async function loadRecord(): Promise<WelcomeHealthRecord> {
  const raw = await SecureStore.getItemAsync(STORAGE_KEY);
  if (!raw) return {};
  try {
    return JSON.parse(raw) as WelcomeHealthRecord;
  } catch {
    return {};
  }
}

export async function shouldTriggerWelcomeHealthCheck(workspaceId: string): Promise<boolean> {
  if (!workspaceId) return false;
  const record = await loadRecord();
  return !record[workspaceId];
}

export async function markWelcomeHealthCheckTriggered(workspaceId: string): Promise<void> {
  if (!workspaceId) return;
  const record = await loadRecord();
  record[workspaceId] = true;
  await SecureStore.setItemAsync(STORAGE_KEY, JSON.stringify(record));
}

export async function triggerWelcomeHealthCheck(
  gateway: GatewayLike,
  workspaceId: string,
  labels: { title: string; goal: string },
): Promise<void> {
  if (!workspaceId || gateway.status !== "ready") return;
  if (!(await shouldTriggerWelcomeHealthCheck(workspaceId))) return;
  await markWelcomeHealthCheckTriggered(workspaceId);
  try {
    await gateway.runAuthenticated(async (client) => {
      const result = await client.createTask({
        clientRequestId: Crypto.randomUUID(),
        title: labels.title,
        goal: labels.goal,
        launchPolicy: immediatePolicy(),
        agentId: gateway.defaultAgentId,
        notificationPolicy: { completed: true, failed: true, waiting_approval: false },
      });
      if (!result.execution) {
        await client.executeTask(result.task.task_id);
      }
    });
  } catch {
    // Onboarding should stay celebratory even if the welcome task cannot start yet.
  }
}
