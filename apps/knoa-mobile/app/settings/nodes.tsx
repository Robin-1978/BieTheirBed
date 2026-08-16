import { CameraView, useCameraPermissions } from "expo-camera";
import { router } from "expo-router";
import { useEffect, useState } from "react";
import { ScrollView, StyleSheet, Text, TextInput, View } from "react-native";

import { AppPressable } from "@/components/AppPressable";
import { useGateway } from "@/state/GatewayProvider";
import {
  addHostedWorkspaceMember,
  connectHub,
  createHostedWorkspace,
  createNodeEnrollmentGrant,
  listHostedWorkspaceMembers,
  listHostedWorkspaces,
  listHubNodes,
  loginHostedAccount,
  loadHubConnection,
  registerHostedAccount,
  removeHostedWorkspaceMember,
  resetHostedPassword,
  selectHostedWorkspace,
  type HubNode,
  type HostedWorkspace,
  type HostedWorkspaceMember,
} from "@/hub/hubClient";
import { colors } from "@/theme";

export default function NodeCenterScreen() {
  const gateway = useGateway();
  const [hubUrl, setHubUrl] = useState("");
  const [hubToken, setHubToken] = useState("");
  const [hubId, setHubId] = useState("");
  const [workspaceId, setWorkspaceId] = useState("");
  const [deploymentMode, setDeploymentMode] = useState("");
  const [hostedLogin, setHostedLogin] = useState("");
  const [hostedDisplayName, setHostedDisplayName] = useState("");
  const [hostedPassword, setHostedPassword] = useState("");
  const [hostedSetup, setHostedSetup] = useState("");
  const [hostedRecovery, setHostedRecovery] = useState("");
  const [hostedWorkspaces, setHostedWorkspaces] = useState<HostedWorkspace[]>([]);
  const [hostedMembers, setHostedMembers] = useState<HostedWorkspaceMember[]>([]);
  const [newWorkspaceName, setNewWorkspaceName] = useState("");
  const [newMemberLogin, setNewMemberLogin] = useState("");
  const [scanningHostedSetup, setScanningHostedSetup] = useState(false);
  const [cameraPermission, requestCameraPermission] = useCameraPermissions();
  const [hubNodes, setHubNodes] = useState<HubNode[]>([]);
  const [message, setMessage] = useState("");
  const [nodeHub, setNodeHub] = useState<{ enrolled: boolean; relay_connected: boolean; last_error: string } | null>(null);

  useEffect(() => {
    void loadHubConnection().then(async (connection) => {
      if (!connection) return;
      setHubUrl(connection.rootUrl);
      setHubId(connection.hubId);
      setWorkspaceId(connection.workspaceId);
      setDeploymentMode(connection.deploymentMode);
      setHubNodes(await listHubNodes());
      const workspaces = await listHostedWorkspaces();
      setHostedWorkspaces(workspaces);
      const current = workspaces.find((item) => item.workspaceId === connection.workspaceId);
      if (current && current.role !== "member") {
        setHostedMembers(await listHostedWorkspaceMembers(current.workspaceId));
      }
    }).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (gateway.status !== "ready") return;
    void gateway.runAuthenticated((client) => client.hubStatus())
      .then(setNodeHub)
      .catch(() => undefined);
  }, [gateway.nodeId, gateway.status]);

  async function saveHub() {
    try {
      const connection = await connectHub(hubUrl, hubToken, "Knoa Mobile");
      setHubId(connection.hubId);
      setWorkspaceId(connection.workspaceId);
      setDeploymentMode(connection.deploymentMode);
      setHubToken("");
      setHubNodes(await listHubNodes());
      setMessage("Hub 已连接，帐号令牌已写入安全存储");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Hub 连接失败");
    }
  }

  async function registerHosted() {
    try {
      const account = await registerHostedAccount(
        hostedSetup,
        hostedLogin,
        hostedDisplayName || hostedLogin.split("@")[0] || "Knoa User",
        hostedPassword,
      );
      applyHostedAccount(account.connection, account.workspaces);
      setHostedSetup("");
      setHostedPassword("");
      setHubNodes([]);
      setMessage(`Hosted Account 已创建：${account.loginIdentity}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Hosted Account 创建失败");
    }
  }

  async function loginHosted() {
    try {
      const account = await loginHostedAccount(hubUrl, hostedLogin, hostedPassword);
      applyHostedAccount(account.connection, account.workspaces);
      setHostedPassword("");
      setHubNodes(await listHubNodes());
      setMessage(`Hosted Account 已登录：${account.loginIdentity}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Hosted Account 登录失败");
    }
  }

  async function recoverHosted() {
    try {
      const account = await resetHostedPassword(hostedRecovery, hostedPassword);
      applyHostedAccount(account.connection, account.workspaces);
      setHostedRecovery("");
      setHostedPassword("");
      setHubNodes(await listHubNodes());
      setMessage(`Hosted Account 密码已恢复：${account.loginIdentity}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Hosted Account 密码恢复失败");
    }
  }

  function applyHostedAccount(connection: Awaited<ReturnType<typeof loadHubConnection>> & {}, workspaces: HostedWorkspace[]) {
    setHubUrl(connection.rootUrl);
    setHubId(connection.hubId);
    setWorkspaceId(connection.workspaceId);
    setDeploymentMode(connection.deploymentMode);
    setHostedWorkspaces(workspaces);
  }

  async function addWorkspace() {
    try {
      const workspace = await createHostedWorkspace(newWorkspaceName);
      setHostedWorkspaces(await listHostedWorkspaces());
      setNewWorkspaceName("");
      setMessage(`Workspace 已创建：${workspace.displayName}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Workspace 创建失败");
    }
  }

  async function switchWorkspace(workspace: HostedWorkspace) {
    try {
      const connection = await selectHostedWorkspace(workspace);
      setWorkspaceId(connection.workspaceId);
      setHubNodes(await listHubNodes());
      setHostedMembers(
        workspace.role === "member"
          ? []
          : await listHostedWorkspaceMembers(workspace.workspaceId),
      );
      setMessage(`已切换 Workspace：${workspace.displayName}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Workspace 切换失败");
    }
  }

  async function addMember() {
    try {
      const workspace = hostedWorkspaces.find((item) => item.workspaceId === workspaceId);
      if (!workspace || workspace.kind !== "shared") throw new Error("请先选择 Shared Workspace");
      await addHostedWorkspaceMember(workspace.workspaceId, newMemberLogin, "member");
      setHostedMembers(await listHostedWorkspaceMembers(workspace.workspaceId));
      setNewMemberLogin("");
      setMessage(`Workspace 成员已加入：${newMemberLogin}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Workspace 成员添加失败");
    }
  }

  async function removeMember(member: HostedWorkspaceMember) {
    try {
      await removeHostedWorkspaceMember(workspaceId, member.accountId);
      setHostedMembers(await listHostedWorkspaceMembers(workspaceId));
      setMessage(`Workspace 成员已移除：${member.loginIdentity}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Workspace 成员移除失败");
    }
  }

  async function enrollCurrentNode() {
    try {
      const connection = await loadHubConnection();
      if (!connection) throw new Error("请先连接 Personal Hub");
      if (!gateway.nodeId) throw new Error("请先连接当前 Node");
      const grant = await createNodeEnrollmentGrant();
      await gateway.runAuthenticated((client) => client.enrollHub({
        hub_url: connection.url,
        hub_id: connection.hubId,
        hub_signing_public_key: connection.signingPublicKey,
        grant_id: grant.grant_id,
        grant_secret: grant.secret,
        challenge: grant.challenge,
        display_name: gateway.nodes.find((node) => node.nodeId === gateway.nodeId)?.displayName ?? "Knoa Node",
      }));
      setNodeHub(await gateway.runAuthenticated((client) => client.hubStatus()));
      setHubNodes(await listHubNodes());
      setMessage("当前 Node 已加入 Hub，outbound Relay 正在建立连接");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Node Hub enrollment 失败");
    }
  }
  if (scanningHostedSetup && cameraPermission?.granted) {
    return (
      <View style={styles.scanner}>
        <CameraView
          style={StyleSheet.absoluteFill}
          barcodeScannerSettings={{ barcodeTypes: ["qr"] }}
          onBarcodeScanned={({ data }) => {
            try {
              const version = (JSON.parse(data) as { version?: string }).version;
              if (version === "knoa-hosted-password-reset-v1") setHostedRecovery(data);
              else setHostedSetup(data);
            } catch {
              setHostedSetup(data);
            }
            setScanningHostedSetup(false);
          }}
        />
        <View style={styles.scanFrame} />
        <Text style={styles.scanHint}>扫描 knoa-hub-admin 生成的注册或密码恢复二维码</Text>
        <AppPressable style={styles.cancelScan} onPress={() => setScanningHostedSetup(false)}>
          <Text style={styles.primaryText}>取消</Text>
        </AppPressable>
      </View>
    );
  }

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <View style={styles.section}>
        <Text style={styles.title}>Node Center</Text>
        <Text style={styles.hint}>AppInstallationIdentity 在本机唯一；每个 Node 保持独立绑定、会话、事件游标和固定公钥。</Text>
        {gateway.nodes.map((node) => {
          const active = node.nodeId === gateway.nodeId;
          return (
            <AppPressable key={node.nodeId} style={[styles.node, active && styles.active]} disabled={active} onPress={() => void gateway.switchNode(node.nodeId)}>
              <View style={styles.row}>
                <Text style={styles.nodeTitle}>{node.displayName}</Text>
                <Text style={active ? styles.activeLabel : styles.switchLabel}>{active ? "当前" : "切换"}</Text>
              </View>
              <Text style={styles.meta}>{node.nodeId}</Text>
              <Text style={styles.meta}>{node.gatewayUrl}</Text>
              <Text style={styles.key}>Pinned Ed25519 · {node.nodeSigningPublicKey.slice(0, 20)}…</Text>
            </AppPressable>
          );
        })}
        {!gateway.nodes.length ? <Text style={styles.hint}>尚未配对 Node。</Text> : null}
        <AppPressable style={styles.primary} onPress={() => router.push("/pair")}>
          <Text style={styles.primaryText}>扫描二维码添加 Node</Text>
        </AppPressable>
      </View>
      <View style={styles.section}>
        <Text style={styles.title}>Personal Workspace</Text>
        <Text style={styles.hint}>Workspace 是资源与授权边界；HubService 只提供身份、目录、密文投递与不透明 Relay，可使用 Knoa Hosted 或自托管实现。</Text>
        <TextInput value={hubUrl} onChangeText={setHubUrl} placeholder="https://hub.example.com" placeholderTextColor={colors.muted} autoCapitalize="none" style={styles.input} />
        <TextInput value={hubToken} onChangeText={setHubToken} placeholder="帐号令牌" placeholderTextColor={colors.muted} secureTextEntry autoCapitalize="none" style={styles.input} />
        <AppPressable style={styles.primary} onPress={() => void saveHub()}><Text style={styles.primaryText}>{hubId ? "更新 Hub 连接" : "连接 Hub"}</Text></AppPressable>
        <Text style={styles.title}>Hosted Account</Text>
        <Text style={styles.hint}>Bootstrap Secret 只保留在服务器。App 使用一次性二维码创建帐号或恢复密码，也可使用帐号和密码重新登录。</Text>
        <AppPressable style={styles.secondary} onPress={async () => {
          if (!cameraPermission?.granted) {
            const permission = await requestCameraPermission();
            if (!permission.granted) {
              setMessage("需要相机权限才能扫描 Hosted Account 注册二维码");
              return;
            }
          }
          setScanningHostedSetup(true);
        }}><Text style={styles.secondaryText}>扫描帐号注册 / 密码恢复二维码</Text></AppPressable>
        <TextInput value={hostedSetup} onChangeText={setHostedSetup} placeholder="或粘贴 hosted_setup_json" placeholderTextColor={colors.muted} autoCapitalize="none" multiline style={[styles.input, styles.payload]} />
        <TextInput value={hostedRecovery} onChangeText={setHostedRecovery} placeholder="或粘贴密码恢复 hosted_setup_json" placeholderTextColor={colors.muted} autoCapitalize="none" multiline style={[styles.input, styles.payload]} />
        <TextInput value={hostedLogin} onChangeText={setHostedLogin} placeholder="登录标识，例如 owner@example.com" placeholderTextColor={colors.muted} autoCapitalize="none" style={styles.input} />
        <TextInput value={hostedDisplayName} onChangeText={setHostedDisplayName} placeholder="帐号显示名称" placeholderTextColor={colors.muted} style={styles.input} />
        <TextInput value={hostedPassword} onChangeText={setHostedPassword} placeholder="帐号密码（至少 12 位）" placeholderTextColor={colors.muted} secureTextEntry autoCapitalize="none" style={styles.input} />
        <View style={styles.buttonRow}>
          <AppPressable style={[styles.secondary, styles.flex]} onPress={() => void registerHosted()}><Text style={styles.secondaryText}>创建帐号</Text></AppPressable>
          <AppPressable style={[styles.secondary, styles.flex]} onPress={() => void recoverHosted()}><Text style={styles.secondaryText}>恢复密码</Text></AppPressable>
          <AppPressable style={[styles.primary, styles.flex]} onPress={() => void loginHosted()}><Text style={styles.primaryText}>登录帐号</Text></AppPressable>
        </View>
        {hubId ? <Text style={styles.key}>Workspace · {workspaceId || hubId}{"\n"}HubService · {hubId}{"\n"}Mode · {deploymentMode || "self_hosted"}</Text> : null}
        {hostedWorkspaces.map((workspace) => (
          <AppPressable key={workspace.workspaceId} style={[styles.node, workspace.workspaceId === workspaceId && styles.active]} disabled={workspace.workspaceId === workspaceId} onPress={() => void switchWorkspace(workspace)}>
            <View style={styles.row}><Text style={styles.nodeTitle}>{workspace.displayName}</Text><Text style={styles.meta}>{workspace.kind} · {workspace.role}</Text></View>
            <Text style={styles.meta}>{workspace.workspaceId}</Text>
          </AppPressable>
        ))}
        {hostedWorkspaces.length ? (
          <View style={styles.buttonRow}>
            <TextInput value={newWorkspaceName} onChangeText={setNewWorkspaceName} placeholder="新 Workspace 名称" placeholderTextColor={colors.muted} style={[styles.input, styles.flex]} />
            <AppPressable style={styles.secondary} onPress={() => void addWorkspace()}><Text style={styles.secondaryText}>创建</Text></AppPressable>
          </View>
        ) : null}
        {hostedWorkspaces.find((item) => item.workspaceId === workspaceId)?.kind === "shared"
          && hostedWorkspaces.find((item) => item.workspaceId === workspaceId)?.role === "owner" ? (
          <View style={styles.memberSection}>
            <Text style={styles.title}>Workspace Members</Text>
            <Text style={styles.hint}>成员可读取目录、连接 Node 和使用已授权资源；只有 owner/admin 可修改 Workspace 资源与登记 Node。</Text>
            <View style={styles.buttonRow}>
              <TextInput value={newMemberLogin} onChangeText={setNewMemberLogin} placeholder="成员登录标识" placeholderTextColor={colors.muted} autoCapitalize="none" style={[styles.input, styles.flex]} />
              <AppPressable style={styles.secondary} onPress={() => void addMember()}><Text style={styles.secondaryText}>添加</Text></AppPressable>
            </View>
            {hostedMembers.map((member) => (
              <View key={member.accountId} style={styles.node}>
                <View style={styles.row}>
                  <Text style={styles.nodeTitle}>{member.displayName}</Text>
                  <Text style={styles.meta}>{member.role}</Text>
                </View>
                <Text style={styles.meta}>{member.loginIdentity}</Text>
                {member.role !== "owner" ? (
                  <AppPressable style={styles.secondary} onPress={() => void removeMember(member)}><Text style={styles.secondaryText}>移除成员</Text></AppPressable>
                ) : null}
              </View>
            ))}
          </View>
        ) : null}
        {hubId && gateway.nodeId ? (
          <AppPressable style={styles.primary} onPress={() => void enrollCurrentNode()}>
            <Text style={styles.primaryText}>{nodeHub?.enrolled ? "重新登记当前 Node" : "将当前 Node 加入 Hub"}</Text>
          </AppPressable>
        ) : null}
        {nodeHub?.enrolled ? (
          <Text style={styles.key}>Node Relay · {nodeHub.relay_connected ? "已连接" : nodeHub.last_error ? `重连中 (${nodeHub.last_error})` : "连接中"}</Text>
        ) : null}
        {message ? <Text style={styles.hint}>{message}</Text> : null}
        {hubNodes.map((node) => (
          <View key={node.node_id} style={styles.node}>
            <View style={styles.row}><Text style={styles.nodeTitle}>{node.display_name}</Text><Text style={node.online ? styles.activeLabel : styles.meta}>{node.online ? "在线" : "离线"}</Text></View>
            <Text style={styles.meta}>{node.node_id} · {node.platform} {node.version}</Text>
            <Text style={styles.key}>{gateway.nodes.some((item) => item.nodeId === node.node_id) ? "已建立本地信任绑定" : "需通过二维码或 owner 授权建立 NodeDeviceBinding"}</Text>
          </View>
        ))}
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 18, gap: 16, backgroundColor: colors.background },
  section: { backgroundColor: colors.surface, borderRadius: 18, padding: 16, gap: 12, borderWidth: 1, borderColor: colors.line },
  title: { color: colors.ink, fontSize: 19, fontWeight: "800" },
  hint: { color: colors.muted, fontSize: 13, lineHeight: 19 },
  node: { padding: 14, gap: 5, borderRadius: 14, backgroundColor: colors.background, borderWidth: 1, borderColor: colors.line },
  active: { borderColor: colors.accent },
  row: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  nodeTitle: { color: colors.ink, fontWeight: "800", fontSize: 16 },
  meta: { color: colors.muted, fontSize: 12 },
  key: { color: colors.accent, fontSize: 12 },
  activeLabel: { color: colors.accent, fontWeight: "800" },
  switchLabel: { color: colors.ink, fontWeight: "700" },
  primary: { backgroundColor: colors.accent, borderRadius: 13, padding: 14, alignItems: "center" },
  primaryText: { color: "#fff", fontWeight: "800" },
  secondary: { backgroundColor: colors.surface, borderRadius: 13, padding: 14, alignItems: "center", borderWidth: 1, borderColor: colors.accent },
  secondaryText: { color: colors.accent, fontWeight: "800" },
  input: { backgroundColor: colors.background, color: colors.ink, borderRadius: 12, paddingHorizontal: 13, paddingVertical: 12, borderWidth: 1, borderColor: colors.line },
  payload: { minHeight: 96, textAlignVertical: "top" },
  buttonRow: { flexDirection: "row", gap: 10, alignItems: "center" },
  flex: { flex: 1 },
  memberSection: { gap: 10 },
  scanner: { flex: 1, alignItems: "center", justifyContent: "center" },
  scanFrame: { width: 260, height: 260, borderWidth: 2, borderColor: "white", borderRadius: 24 },
  scanHint: { position: "absolute", bottom: 116, color: "white", backgroundColor: "rgba(0,0,0,0.58)", paddingHorizontal: 14, paddingVertical: 8, borderRadius: 16 },
  cancelScan: { position: "absolute", bottom: 56, backgroundColor: "rgba(0,0,0,0.65)", paddingHorizontal: 20, paddingVertical: 12, borderRadius: 20 },
});
