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

import { loadHubConnection, registerHostedAccount, resetHostedPassword } from "./hubClient";

beforeEach(() => {
  native.cache.clear();
  vi.restoreAllMocks();
});

describe("Hosted Hub account onboarding", () => {
  it("switches all subsequent Hub requests to the returned Workspace path", async () => {
    const workspacePath = "/workspaces/ws_personal_1";
    const accessToken = `khs_${"a".repeat(48)}`;
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === "https://hosted.example/v1/hosted/accounts") {
        return Response.json({
          subject_id: "subject-1",
          account_id: "account-1",
          login_identity: "owner@example.com",
          workspace_id: "ws_personal_1",
          workspace_path: workspacePath,
          access_token: accessToken,
          expires_at: 1234,
          workspaces: [{
            workspace_id: "ws_personal_1",
            display_name: "Owner Personal Workspace",
            kind: "personal",
            role: "owner",
            workspace_path: workspacePath,
          }],
        }, { status: 201 });
      }
      if (url === `https://hosted.example${workspacePath}/v1/hub`) {
        return Response.json({
          hub_id: "hub_hosted_sim",
          workspace_id: "ws_personal_1",
          identity_issuer_id: "hub_hosted_sim",
          signing_public_key: "signing-key",
          deployment_mode: "hosted_single_node",
        });
      }
      if (url === `https://hosted.example${workspacePath}/v1/installations`) {
        expect(init?.method).toBe("POST");
        return Response.json({ installation_id: "installation-1" }, { status: 201 });
      }
      throw new Error(`Unexpected URL: ${url}`);
    });

    const account = await registerHostedAccount(
      JSON.stringify({
        version: "knoa-hosted-account-v1",
        hub_url: "https://hosted.example",
        grant_id: "grant-1",
        grant_secret: `secret-${"b".repeat(40)}`,
        expires_at: Date.now() / 1000 + 300,
      }),
      "owner@example.com",
      "Owner",
      "correct horse battery staple",
    );

    expect(account.connection).toMatchObject({
      url: `https://hosted.example${workspacePath}`,
      rootUrl: "https://hosted.example",
      accountId: "account-1",
      hubId: "hub_hosted_sim",
      workspaceId: "ws_personal_1",
      deploymentMode: "hosted_single_node",
    });
    await expect(loadHubConnection()).resolves.toMatchObject(account.connection);
    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      "https://hosted.example/v1/hosted/accounts",
      `https://hosted.example${workspacePath}/v1/hub`,
      `https://hosted.example${workspacePath}/v1/installations`,
    ]);
  });

  it("consumes a one-time password recovery payload and replaces the stored session", async () => {
    const workspacePath = "/workspaces/ws_personal_2";
    const accessToken = `khs_${"c".repeat(48)}`;
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === "https://hosted.example/v1/hosted/password-reset") {
        expect(JSON.parse(String(init?.body))).toEqual({
          grant_id: "reset-1",
          grant_secret: `secret-${"d".repeat(40)}`,
          new_password: "replacement secure password",
        });
        return Response.json({
          account_id: "account-2",
          login_identity: "owner@example.com",
          workspace_id: "ws_personal_2",
          workspace_path: workspacePath,
          access_token: accessToken,
          expires_at: 4321,
          workspaces: [{
            workspace_id: "ws_personal_2",
            display_name: "Owner Personal Workspace",
            kind: "personal",
            role: "owner",
            workspace_path: workspacePath,
          }],
        }, { status: 201 });
      }
      if (url === `https://hosted.example${workspacePath}/v1/hub`) {
        return Response.json({
          hub_id: "hub_hosted",
          workspace_id: "ws_personal_2",
          identity_issuer_id: "hub_hosted",
          signing_public_key: "signing-key",
          deployment_mode: "hosted_single_node",
        });
      }
      if (url === `https://hosted.example${workspacePath}/v1/installations`) {
        return Response.json({ installation_id: "installation-1" }, { status: 201 });
      }
      throw new Error(`Unexpected URL: ${url}`);
    });

    const account = await resetHostedPassword(
      JSON.stringify({
        version: "knoa-hosted-password-reset-v1",
        hub_url: "https://hosted.example",
        grant_id: "reset-1",
        grant_secret: `secret-${"d".repeat(40)}`,
        expires_at: Date.now() / 1000 + 300,
      }),
      "replacement secure password",
    );

    expect(account.connection.token).toBe(accessToken);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    await expect(loadHubConnection()).resolves.toMatchObject({ accountId: "account-2" });
  });
});
