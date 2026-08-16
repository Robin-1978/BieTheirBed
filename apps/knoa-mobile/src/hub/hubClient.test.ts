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

import {
  loadHubConnection,
  registerHostedAccount,
  resetHostedPassword,
  resolveAndroidRelease,
} from "./hubClient";

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

describe("Android release ownership", () => {
  it("uses the Node channel when there is no Hosted Account", async () => {
    const nodeRelease = vi.fn(async () => ({
      platform: "android" as const,
      channel: "personal" as const,
      version_name: "0.2.45",
      version_code: 56,
      min_supported_version_code: 1,
      size_bytes: 100,
      sha256: "a".repeat(64),
      published_at: 1,
      release_notes: "",
      download_path: "/node.apk",
    }));

    await expect(resolveAndroidRelease(nodeRelease)).resolves.toMatchObject({
      channel: "personal",
      version_code: 56,
    });
    expect(nodeRelease).toHaveBeenCalledOnce();
  });

  it("uses Hosted Hub exclusively and resolves its download URL", async () => {
    native.cache.set("knoa.hub.connection.v1", JSON.stringify({
      url: "https://hosted.example/workspaces/ws_personal_1",
      rootUrl: "https://hosted.example",
      token: `khs_${"a".repeat(48)}`,
      accountId: "account-1",
      hubId: "hub-hosted",
      workspaceId: "ws_personal_1",
      identityIssuerId: "hub-hosted",
      signingPublicKey: "signing-key",
      deploymentMode: "hosted_single_node",
    }));
    vi.spyOn(globalThis, "fetch").mockResolvedValue(Response.json({
      platform: "android",
      channel: "hosted",
      version_name: "0.2.46",
      version_code: 57,
      min_supported_version_code: 1,
      size_bytes: 100,
      sha256: "b".repeat(64),
      published_at: 1,
      release_notes: "Hosted",
      download_path: `/releases/android/57/${"b".repeat(64)}/knoa.apk`,
    }));
    const nodeRelease = vi.fn();

    await expect(resolveAndroidRelease(nodeRelease)).resolves.toMatchObject({
      channel: "hosted",
      download_path: `https://hosted.example/releases/android/57/${"b".repeat(64)}/knoa.apk`,
    });
    expect(nodeRelease).not.toHaveBeenCalled();
  });
});
