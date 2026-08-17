import * as SecureStore from "expo-secure-store";

const NAVIGATION_PREFERENCE = "knoa.navigation.preference.v1";

export type LandingPreference = "last" | "workspace" | "account";
export type NodePage = "chat" | "tasks";

export type NavigationPreference = {
  landing: LandingPreference;
  workspaceId: string;
  workspaceName: string;
  nodeId: string;
  nodePage: NodePage;
};

const fallback: NavigationPreference = {
  landing: "last",
  workspaceId: "",
  workspaceName: "",
  nodeId: "",
  nodePage: "chat",
};

export async function loadNavigationPreference(): Promise<NavigationPreference> {
  const raw = await SecureStore.getItemAsync(NAVIGATION_PREFERENCE);
  if (!raw) return fallback;
  try {
    const value = JSON.parse(raw) as Partial<NavigationPreference>;
    return {
      landing: value.landing === "account" || value.landing === "workspace" ? value.landing : "last",
      workspaceId: typeof value.workspaceId === "string" ? value.workspaceId : "",
      workspaceName: typeof value.workspaceName === "string" ? value.workspaceName : "",
      nodeId: typeof value.nodeId === "string" ? value.nodeId : "",
      nodePage: value.nodePage === "tasks" ? "tasks" : "chat",
    };
  } catch {
    return fallback;
  }
}

export async function setLandingPreference(landing: LandingPreference): Promise<void> {
  const current = await loadNavigationPreference();
  await save({ ...current, landing });
}

export async function rememberWorkspace(workspaceId: string, workspaceName: string): Promise<void> {
  const current = await loadNavigationPreference();
  await save({ ...current, workspaceId, workspaceName, nodeId: "" });
}

export async function rememberNodePage(input: {
  workspaceId: string;
  workspaceName: string;
  nodeId: string;
  nodePage: NodePage;
}): Promise<void> {
  const current = await loadNavigationPreference();
  await save({ ...current, ...input });
}

async function save(value: NavigationPreference): Promise<void> {
  await SecureStore.setItemAsync(NAVIGATION_PREFERENCE, JSON.stringify(value));
}
