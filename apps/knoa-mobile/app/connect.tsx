import { CameraView, useCameraPermissions } from "expo-camera";
import { router } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";

import { AppPressable } from "@/components/AppPressable";
import {
  listHostedWorkspaces,
  listHubNodes,
  loadHubConnection,
  loginHostedAccount,
  registerHostedAccount,
  resetHostedPassword,
  selectHostedWorkspace,
  type HubConnection,
  type HubNode,
  type HostedWorkspace,
} from "@/hub/hubClient";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";

type HubState = "loading" | "disconnected" | "ready" | "error";
type AccountMode = "login" | "register" | "recover";

export default function ConnectScreen() {
  const gateway = useGateway();
  const [hubState, setHubState] = useState<HubState>("loading");
  const [connection, setConnection] = useState<HubConnection | null>(null);
  const [nodes, setNodes] = useState<HubNode[]>([]);
  const [workspaces, setWorkspaces] = useState<HostedWorkspace[]>([]);
  const [hubUrl, setHubUrl] = useState("https://knoa.tinydotdot.com");
  const [accountMode, setAccountMode] = useState<AccountMode>("login");
  const [loginIdentity, setLoginIdentity] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [setupPayload, setSetupPayload] = useState("");
  const [scanningSetup, setScanningSetup] = useState(false);
  const [cameraPermission, requestCameraPermission] = useCameraPermissions();
  const [message, setMessage] = useState("");
  const [working, setWorking] = useState(false);

  const refreshHub = useCallback(async () => {
    setHubState("loading");
    try {
      const stored = await loadHubConnection();
      setConnection(stored);
      if (!stored) {
        setNodes([]);
        setWorkspaces([]);
        setHubState("disconnected");
        return;
      }
      setHubUrl(stored.rootUrl);
      const [directory, hostedWorkspaces] = await Promise.all([
        listHubNodes(),
        stored.accountId ? listHostedWorkspaces() : Promise.resolve([]),
      ]);
      setNodes(directory);
      setWorkspaces(hostedWorkspaces);
      setHubState("ready");
    } catch (error) {
      setNodes([]);
      setWorkspaces([]);
      setHubState("error");
      setMessage(error instanceof Error ? error.message : "Hub 帐号连接失败");
    }
  }, []);

  useEffect(() => {
    void refreshHub();
  }, [refreshHub]);

  useEffect(() => {
    if (gateway.status === "ready") router.replace("/chat");
  }, [gateway.status]);

  async function login() {
    setWorking(true);
    setMessage("");
    try {
      await loginHostedAccount(hubUrl, loginIdentity, password);
      setPassword("");
      await refreshHub();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Hosted Account 登录失败");
    } finally {
      setWorking(false);
    }
  }

  async function register() {
    setWorking(true);
    setMessage("");
    try {
      await registerHostedAccount(
        setupPayload,
        loginIdentity,
        displayName || loginIdentity.split("@")[0] || "Knoa User",
        password,
      );
      setPassword("");
      setSetupPayload("");
      await refreshHub();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Hosted Account 创建失败");
    } finally {
      setWorking(false);
    }
  }

  async function recover() {
    setWorking(true);
    setMessage("");
    try {
      await resetHostedPassword(setupPayload, password);
      setPassword("");
      setSetupPayload("");
      await refreshHub();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Hosted Account 密码恢复失败");
    } finally {
      setWorking(false);
    }
  }

  async function openSetupScanner() {
    if (!cameraPermission?.granted) {
      const permission = await requestCameraPermission();
      if (!permission.granted) {
        setMessage("需要相机权限才能扫描 Hub 帐号凭据");
        return;
      }
    }
    setScanningSetup(true);
  }

  async function switchWorkspace(workspace: HostedWorkspace) {
    setWorking(true);
    setMessage("");
    try {
      await selectHostedWorkspace(workspace);
      await refreshHub();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Workspace 切换失败");
    } finally {
      setWorking(false);
    }
  }

  async function chooseNode(node: HubNode) {
    const binding = gateway.nodes.find((item) => item.nodeId === node.node_id);
    if (!binding) {
      router.push("/pair");
      return;
    }
    setWorking(true);
    setMessage("");
    try {
      await gateway.switchNode(node.node_id);
      router.replace("/");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Node 连接失败");
    } finally {
      setWorking(false);
    }
  }

  async function chooseLocalNode(nodeId: string) {
    setWorking(true);
    setMessage("");
    try {
      await gateway.switchNode(nodeId);
      router.replace("/");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Node 连接失败");
    } finally {
      setWorking(false);
    }
  }

  if (scanningSetup && cameraPermission?.granted) {
    return (
      <View style={styles.scanner}>
        <CameraView
          style={StyleSheet.absoluteFill}
          barcodeScannerSettings={{ barcodeTypes: ["qr"] }}
          onBarcodeScanned={({ data }) => {
            try {
              const version = (JSON.parse(data) as { version?: string }).version;
              setAccountMode(version === "knoa-hosted-password-reset-v1" ? "recover" : "register");
            } catch {
              setAccountMode("register");
            }
            setSetupPayload(data);
            setScanningSetup(false);
            setMessage("");
          }}
        />
        <View style={styles.scanFrame} />
        <Text style={styles.scanHint}>扫描 Hub 发出的帐号注册或密码恢复二维码</Text>
        <AppPressable style={styles.cancelScan} onPress={() => setScanningSetup(false)}>
          <Text style={styles.primaryText}>取消</Text>
        </AppPressable>
      </View>
    );
  }

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <View style={styles.hero}>
        <Text style={styles.eyebrow}>KNOA HUB</Text>
        <Text style={styles.title}>先登录 Hub，再选择 Node</Text>
        <Text style={styles.hint}>Hub 帐号与 Workspace 始终可用；单个 Node 离线只影响该 Node 的执行，不会锁住整个 App。</Text>
      </View>

      {hubState === "loading" ? <ActivityIndicator color={colors.accent} /> : null}

      {hubState !== "ready" ? (
        <View style={styles.card}>
          <Text style={styles.sectionTitle}>Hosted Account</Text>
          <Text style={styles.hint}>新用户先在 Hub 建立帐号和个人 Workspace，再安装或加入 Node。帐号流程不依赖任何 Node。</Text>
          <TextInput value={hubUrl} onChangeText={setHubUrl} placeholder="https://hub.example.com" placeholderTextColor={colors.muted} autoCapitalize="none" style={styles.input} />
          <View style={styles.modeRow}>
            {(["login", "register", "recover"] as const).map((mode) => (
              <AppPressable
                key={mode}
                style={[styles.mode, accountMode === mode && styles.modeActive]}
                disabled={working}
                onPress={() => {
                  setAccountMode(mode);
                  setMessage("");
                }}
              >
                <Text style={accountMode === mode ? styles.primaryText : styles.secondaryText}>
                  {mode === "login" ? "登录" : mode === "register" ? "创建帐号" : "恢复密码"}
                </Text>
              </AppPressable>
            ))}
          </View>
          {accountMode !== "login" ? (
            <>
              <AppPressable style={styles.secondary} disabled={working} onPress={() => void openSetupScanner()}>
                <Text style={styles.secondaryText}>扫描 Hub 一次性二维码</Text>
              </AppPressable>
              <TextInput value={setupPayload} onChangeText={setSetupPayload} placeholder="或粘贴 Hub 一次性凭据" placeholderTextColor={colors.muted} autoCapitalize="none" multiline style={[styles.input, styles.payload]} />
            </>
          ) : null}
          {accountMode !== "recover" ? (
            <TextInput value={loginIdentity} onChangeText={setLoginIdentity} placeholder="登录标识" placeholderTextColor={colors.muted} autoCapitalize="none" style={styles.input} />
          ) : null}
          {accountMode === "register" ? (
            <TextInput value={displayName} onChangeText={setDisplayName} placeholder="显示名称" placeholderTextColor={colors.muted} style={styles.input} />
          ) : null}
          <TextInput value={password} onChangeText={setPassword} placeholder={accountMode === "recover" ? "设置新密码（至少 12 位）" : "帐号密码（至少 12 位）"} placeholderTextColor={colors.muted} secureTextEntry style={styles.input} />
          <AppPressable
            style={styles.primary}
            disabled={working}
            onPress={() => void (accountMode === "login" ? login() : accountMode === "register" ? register() : recover())}
          >
            {working ? <ActivityIndicator color="#fff" /> : (
              <Text style={styles.primaryText}>{accountMode === "login" ? "登录 Hub" : accountMode === "register" ? "创建并登录" : "恢复并登录"}</Text>
            )}
          </AppPressable>
          {accountMode === "register" ? <Text style={styles.hint}>注册二维码由 Hosted Hub 或组织管理员发放，不由 Node 生成。</Text> : null}
        </View>
      ) : (
        <>
          <View style={styles.card}>
            <View style={styles.row}>
              <View style={styles.flex}>
                <Text style={styles.sectionTitle}>Hub 已连接</Text>
                <Text style={styles.meta}>{connection?.rootUrl}</Text>
              </View>
              <AppPressable style={styles.compact} onPress={() => void refreshHub()}>
                <Text style={styles.secondaryText}>刷新</Text>
              </AppPressable>
            </View>
            {workspaces.map((workspace) => (
              <AppPressable
                key={workspace.workspaceId}
                style={[styles.item, workspace.workspaceId === connection?.workspaceId && styles.active]}
                disabled={working || workspace.workspaceId === connection?.workspaceId}
                onPress={() => void switchWorkspace(workspace)}
              >
                <Text style={styles.itemTitle}>{workspace.displayName}</Text>
                <Text style={styles.meta}>{workspace.kind} · {workspace.role}</Text>
              </AppPressable>
            ))}
          </View>

          <View style={styles.card}>
            <Text style={styles.sectionTitle}>选择 Node</Text>
            {!nodes.length ? (
              <>
                <Text style={styles.hint}>帐号和个人 Workspace 已就绪。下一步在电脑安装 Knoa Node，再扫描该 Node 的配对二维码；Node 不在线时 Hub 仍然可用。</Text>
                <AppPressable style={styles.secondary} onPress={() => router.push("/pair")}>
                  <Text style={styles.secondaryText}>我已安装 Node，开始配对</Text>
                </AppPressable>
              </>
            ) : null}
            {nodes.map((node) => {
              const bound = gateway.nodes.some((item) => item.nodeId === node.node_id);
              return (
                <View key={node.node_id} style={styles.item}>
                  <View style={styles.row}>
                    <View style={styles.flex}>
                      <Text style={styles.itemTitle}>{node.display_name}</Text>
                      <Text style={styles.meta}>{node.platform} {node.version} · {bound ? "已建立本地信任" : "尚未绑定"}</Text>
                    </View>
                    <Text style={node.online ? styles.online : styles.offline}>{node.online ? "在线" : "离线"}</Text>
                  </View>
                  <AppPressable style={bound ? styles.primary : styles.secondary} disabled={working} onPress={() => void chooseNode(node)}>
                    <Text style={bound ? styles.primaryText : styles.secondaryText}>{bound ? "连接此 Node" : "扫描二维码绑定"}</Text>
                  </AppPressable>
                </View>
              );
            })}
          </View>
        </>
      )}

      <View style={styles.card}>
        <Text style={styles.sectionTitle}>No-Hub / 本地模式</Text>
        <Text style={styles.hint}>不使用 Hub 时，可以选择已建立本地信任的 Node，或直接扫描新的 Node Gateway 配对二维码。</Text>
        {gateway.nodes.map((node) => (
          <View key={node.nodeId} style={styles.item}>
            <Text style={styles.itemTitle}>{node.displayName}</Text>
            <Text style={styles.meta}>{node.gatewayUrl}</Text>
            <AppPressable
              style={styles.primary}
              disabled={working}
              onPress={() => void chooseLocalNode(node.nodeId)}
            >
              <Text style={styles.primaryText}>连接此 Node</Text>
            </AppPressable>
          </View>
        ))}
        <AppPressable style={styles.secondary} onPress={() => router.push("/pair")}>
          <Text style={styles.secondaryText}>直接绑定 Node</Text>
        </AppPressable>
      </View>

      {gateway.error ? <Text style={styles.error}>{gateway.error}</Text> : null}
      {message ? <Text style={styles.error}>{message}</Text> : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 18, gap: 16, paddingBottom: 48, backgroundColor: colors.background },
  hero: { gap: 8, paddingVertical: 8 },
  eyebrow: { color: colors.accent, fontSize: 11, letterSpacing: 2, fontWeight: "800" },
  title: { color: colors.ink, fontSize: 25, fontWeight: "800" },
  hint: { color: colors.muted, fontSize: 13, lineHeight: 19 },
  card: { backgroundColor: colors.surface, borderRadius: 18, padding: 16, gap: 12, borderWidth: 1, borderColor: colors.line },
  sectionTitle: { color: colors.ink, fontSize: 18, fontWeight: "800" },
  input: { backgroundColor: colors.background, color: colors.ink, borderRadius: 12, paddingHorizontal: 13, paddingVertical: 12, borderWidth: 1, borderColor: colors.line },
  primary: { backgroundColor: colors.accent, borderRadius: 13, padding: 13, alignItems: "center" },
  primaryText: { color: "#fff", fontWeight: "800" },
  secondary: { backgroundColor: colors.surface, borderRadius: 13, padding: 13, alignItems: "center", borderWidth: 1, borderColor: colors.accent },
  secondaryText: { color: colors.accent, fontWeight: "800" },
  compact: { borderWidth: 1, borderColor: colors.accent, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 9 },
  item: { padding: 13, gap: 9, borderRadius: 14, backgroundColor: colors.background, borderWidth: 1, borderColor: colors.line },
  active: { borderColor: colors.accent },
  itemTitle: { color: colors.ink, fontWeight: "800", fontSize: 15 },
  meta: { color: colors.muted, fontSize: 12 },
  row: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12 },
  flex: { flex: 1 },
  online: { color: colors.accent, fontWeight: "800" },
  offline: { color: colors.muted, fontWeight: "700" },
  error: { color: colors.danger, textAlign: "center" },
  modeRow: { flexDirection: "row", gap: 8 },
  mode: { flex: 1, borderWidth: 1, borderColor: colors.accent, borderRadius: 12, paddingVertical: 10, alignItems: "center" },
  modeActive: { backgroundColor: colors.accent },
  payload: { minHeight: 76, textAlignVertical: "top" },
  scanner: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: "#000" },
  scanFrame: { width: 260, height: 260, borderWidth: 3, borderColor: "#fff", borderRadius: 22 },
  scanHint: { color: "#fff", marginTop: 22, paddingHorizontal: 30, textAlign: "center", fontWeight: "700" },
  cancelScan: { position: "absolute", bottom: 48, backgroundColor: colors.accent, borderRadius: 14, paddingHorizontal: 24, paddingVertical: 13 },
});
