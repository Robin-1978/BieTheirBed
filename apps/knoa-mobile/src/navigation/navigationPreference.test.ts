import { beforeEach, describe, expect, it, vi } from "vitest";

const native = vi.hoisted(() => ({ cache: new Map<string, string>() }));

vi.mock("expo-secure-store", () => ({
  getItemAsync: vi.fn(async (key: string) => native.cache.get(key) ?? null),
  setItemAsync: vi.fn(async (key: string, value: string) => { native.cache.set(key, value); }),
}));

import {
  loadNavigationPreference,
  rememberNodePage,
  rememberWorkspace,
  setLandingPreference,
} from "./navigationPreference";

beforeEach(() => native.cache.clear());

describe("navigation preference", () => {
  it("keeps hierarchy context while changing the default landing page", async () => {
    await rememberNodePage({ workspaceId: "ws_1", workspaceName: "Personal", nodeId: "node_1", nodePage: "tasks" });
    await setLandingPreference("account");
    await expect(loadNavigationPreference()).resolves.toEqual({
      landing: "account",
      workspaceId: "ws_1",
      workspaceName: "Personal",
      nodeId: "node_1",
      nodePage: "tasks",
    });
  });

  it("clears the remembered Node when the user explicitly enters a Workspace", async () => {
    await rememberNodePage({ workspaceId: "ws_1", workspaceName: "Personal", nodeId: "node_1", nodePage: "chat" });
    await rememberWorkspace("ws_2", "Team");
    await expect(loadNavigationPreference()).resolves.toMatchObject({
      workspaceId: "ws_2",
      workspaceName: "Team",
      nodeId: "",
    });
  });
});
