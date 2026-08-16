import { beforeEach, describe, expect, it, vi } from "vitest";

const native = vi.hoisted(() => ({
  cache: new Map<string, string>(),
}));

vi.mock("expo-secure-store", () => ({
  getItemAsync: vi.fn(async (key: string) => native.cache.get(key) ?? null),
  setItemAsync: vi.fn(async (key: string, value: string) => {
    native.cache.set(key, value);
  }),
}));

vi.mock("@/security/deviceIdentity", () => ({
  loadOrCreateInstallationId: vi.fn(async () => "installation-1"),
  loadOrCreatePrivateKey: vi.fn(async () => "private-key"),
  publicKey: vi.fn(() => "public-key"),
}));

import { createHostedSimulationAccount, loadHubConnection } from "./hubClient";

beforeEach(() => {
  native.cache.clear();
  vi.restoreAllMocks();
});

describe("Hosted Hub simulation account onboarding", () => {
  it("switches all subsequent Hub requests to the returned Workspace path", async () => {
    const workspacePath = "/workspaces/ws_personal_1";
    const accessToken = `khs_${"a".repeat(48)}`;
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === "https://hosted.example/v1/hosted/accounts") {
        return Response.json({
          subject_id: "subject-1",
          login_identity: "owner@example.com",
          workspace_id: "ws_personal_1",
          workspace_path: workspacePath,
          access_token: accessToken,
          expires_at: 1234,
        }, { status: 201 });
      }
      if (url === `https://hosted.example${workspacePath}/v1/hub`) {
        return Response.json({
          hub_id: "hub_hosted_sim",
          workspace_id: "ws_personal_1",
          identity_issuer_id: "hub_hosted_sim",
          signing_public_key: "signing-key",
          deployment_mode: "hosted_simulation",
        });
      }
      if (url === `https://hosted.example${workspacePath}/v1/installations`) {
        expect(init?.method).toBe("POST");
        return Response.json({ installation_id: "installation-1" }, { status: 201 });
      }
      throw new Error(`Unexpected URL: ${url}`);
    });

    const account = await createHostedSimulationAccount(
      "https://hosted.example/",
      `bootstrap-${"b".repeat(40)}`,
      "owner@example.com",
      "Owner",
    );

    expect(account.connection).toMatchObject({
      url: `https://hosted.example${workspacePath}`,
      hubId: "hub_hosted_sim",
      workspaceId: "ws_personal_1",
      deploymentMode: "hosted_simulation",
    });
    await expect(loadHubConnection()).resolves.toMatchObject(account.connection);
    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      "https://hosted.example/v1/hosted/accounts",
      `https://hosted.example${workspacePath}/v1/hub`,
      `https://hosted.example${workspacePath}/v1/installations`,
    ]);
  });
});
