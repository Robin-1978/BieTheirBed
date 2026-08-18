import { router, useFocusEffect } from "expo-router";
import { useCallback, useState } from "react";
import { ActivityIndicator, Alert, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";

import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import {
  listHostedWorkspaces,
  createHostedWorkspace,
  loadHostedAccount,
  loadHubConnection,
  logoutHostedAccount,
  selectHostedWorkspace,
  type HostedAccountProfile,
  type HostedWorkspace,
} from "@/hub/hubClient";
import {
  loadNavigationPreference,
  rememberWorkspace,
  setLandingPreference,
  type LandingPreference,
} from "@/navigation/navigationPreference";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";

export default function AccountHomeScreen() {
  const gateway = useGateway();
  const [profile, setProfile] = useState<HostedAccountProfile | null>(null);
  const [workspaces, setWorkspaces] = useState<HostedWorkspace[]>([]);
  const [landing, setLanding] = useState<LandingPreference>("last");
  const [currentWorkspaceId, setCurrentWorkspaceId] = useState("");
  const [rootUrl, setRootUrl] = useState("");
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState("");
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const [workspaceName, setWorkspaceName] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const connection = await loadHubConnection();
      if (!connection) {
        router.replace("/account/login");
        return;
      }
      setCurrentWorkspaceId(connection.workspaceId);
      setRootUrl(connection.rootUrl);
      const [account, preference, hosted] = await Promise.all([
        connection.accountId ? loadHostedAccount() : Promise.resolve(null),
        loadNavigationPreference(),
        connection.accountId ? listHostedWorkspaces() : Promise.resolve([]),
      ]);
      setProfile(account);
      setLanding(preference.landing);
      setWorkspaces(hosted.length ? hosted : [{
        workspaceId: connection.workspaceId,
        displayName: "Personal Workspace",
        kind: "personal",
        role: "owner",
        workspacePath: "",
      }]);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "帐号信息加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useFocusEffect(useCallback(() => { void refresh(); }, [refresh]));

  async function openWorkspace(workspace: HostedWorkspace) {
    setWorking(workspace.workspaceId);
    setError("");
    try {
      if (workspace.workspaceId !== currentWorkspaceId && workspace.workspacePath) {
        await gateway.disconnectNode();
        await selectHostedWorkspace(workspace);
      }
      await rememberWorkspace(workspace.workspaceId, workspace.displayName);
      router.push({
        pathname: "/workspaces/[workspaceId]",
        params: { workspaceId: workspace.workspaceId, workspaceName: workspace.displayName },
      });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Workspace 打开失败");
    } finally {
      setWorking("");
    }
  }

  async function chooseLanding(value: LandingPreference) {
    setLanding(value);
    await setLandingPreference(value);
  }

  async function createWorkspace() {
    if (!workspaceName.trim()) return;
    setWorking("create-workspace");
    setError("");
    try {
      const workspace = await createHostedWorkspace(workspaceName.trim());
      setWorkspaceName("");
      setCreating(false);
      await refresh();
      await openWorkspace(workspace);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Workspace 创建失败");
    } finally {
      setWorking("");
    }
  }

  function confirmLogout() {
    Alert.alert("退出帐号", "退出 Hub 帐号，但保留本机已建立的 Node 信任。", [
      { text: "取消", style: "cancel" },
      {
        text: "退出",
        style: "destructive",
        onPress: () => void (async () => {
          setWorking("logout");
          try {
            await gateway.disconnectNode();
            await logoutHostedAccount();
            router.replace("/account/login");
          } finally {
            setWorking("");
          }
        })(),
      },
    ]);
  }

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <View style={styles.accountCard}>
        <View style={styles.accountIcon}><AppIcon name="user" color={colors.accent} size={34} /></View>
        <View style={styles.flex}>
          <Text style={styles.accountName}>{profile?.displayName || "Knoa Owner"}</Text>
          <Text style={styles.meta}>{profile?.loginIdentity || rootUrl}</Text>
        </View>
        <AppPressable accessibilityLabel="刷新帐号" onPress={() => void refresh()} style={styles.iconButton}>
          <AppIcon name="refresh" color={colors.muted} size={20} />
        </AppPressable>
      </View>

      <View style={styles.sectionHeader}>
        <Text style={styles.sectionTitle}>Workspace</Text>
        {profile ? <AppPressable onPress={() => setCreating((value) => !value)} style={styles.addButton}>
          <AppIcon name={creating ? "x" : "plus"} color={colors.accent} size={19} />
          <Text style={styles.addText}>{creating ? "取消" : "新建"}</Text>
        </AppPressable> : <Text style={styles.meta}>{workspaces.length}</Text>}
      </View>
      {creating ? <View style={styles.card}><Text style={styles.cardTitle}>新建 Workspace</Text><TextInput value={workspaceName} onChangeText={setWorkspaceName} placeholder="Workspace 名称" placeholderTextColor={colors.muted} style={styles.input} /><AppPressable disabled={working === "create-workspace"} onPress={() => void createWorkspace()} style={styles.primary}>{working === "create-workspace" ? <ActivityIndicator color={colors.white} /> : <Text style={styles.primaryText}>创建并进入</Text>}</AppPressable></View> : null}
      {loading ? <ActivityIndicator color={colors.accent} /> : null}
      {workspaces.map((workspace) => (
        <AppPressable key={workspace.workspaceId} disabled={Boolean(working)} onPress={() => void openWorkspace(workspace)} style={styles.workspaceCard}>
          <View style={styles.workspaceIcon}><AppIcon name="workspace" color={colors.accent} size={23} /></View>
          <View style={styles.flex}>
            <Text style={styles.workspaceName}>{workspace.displayName}</Text>
            <Text style={styles.meta}>{workspace.kind} · {workspace.role}{workspace.workspaceId === currentWorkspaceId ? " · 当前" : ""}</Text>
          </View>
          {working === workspace.workspaceId ? <ActivityIndicator color={colors.accent} size="small" /> : <AppIcon name="chevron-right" color={colors.muted} size={20} />}
        </AppPressable>
      ))}

      <View style={styles.card}>
        <Text style={styles.cardTitle}>默认进入</Text>
        <Text style={styles.hint}>只决定启动落点，不改变 Account → Workspace → Node 的层级。</Text>
        <View style={styles.choiceRow}>
          {([
            ["last", "上次使用"],
            ["workspace", "Workspace"],
            ["account", "帐号首页"],
          ] as const).map(([value, label]) => (
            <AppPressable key={value} onPress={() => void chooseLanding(value)} style={[styles.choice, landing === value && styles.choiceActive]}>
              <Text style={landing === value ? styles.choiceTextActive : styles.choiceText}>{label}</Text>
            </AppPressable>
          ))}
        </View>
      </View>

      <View style={styles.card}>
        <Row icon="refresh" title="App 更新" detail="检查、断点下载并安装新版本" onPress={() => router.push("/update")} />
        <Row icon="settings" title="App 设置" detail="外观、语言、版本与更新" onPress={() => router.push("/settings/app")} />
      </View>
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <AppPressable disabled={working === "logout"} onPress={confirmLogout} style={styles.logout}>
        <Text style={styles.logoutText}>{working === "logout" ? "正在退出…" : "退出帐号"}</Text>
      </AppPressable>
    </ScrollView>
  );
}

function Row({ icon, title, detail, onPress }: { icon: "refresh" | "settings"; title: string; detail: string; onPress(): void }) {
  return (
    <AppPressable onPress={onPress} style={styles.row}>
      <AppIcon name={icon} color={colors.accent} size={22} />
      <View style={styles.flex}><Text style={styles.rowTitle}>{title}</Text><Text style={styles.meta}>{detail}</Text></View>
      <AppIcon name="chevron-right" color={colors.muted} size={19} />
    </AppPressable>
  );
}

const styles = StyleSheet.create({
  container: { padding: 17, gap: 13, paddingBottom: 48 },
  accountCard: { flexDirection: "row", alignItems: "center", gap: 12, padding: 16, borderRadius: 18, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line },
  accountIcon: { width: 52, height: 52, borderRadius: 17, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft },
  accountName: { color: colors.ink, fontSize: 19, fontWeight: "800" },
  flex: { flex: 1, minWidth: 0 },
  meta: { color: colors.muted, fontSize: 12, marginTop: 2 },
  iconButton: { width: 42, height: 42, alignItems: "center", justifyContent: "center", borderRadius: 13 },
  sectionHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginTop: 5 },
  sectionTitle: { color: colors.ink, fontSize: 20, fontWeight: "800" },
  addButton: { minHeight: 40, flexDirection: "row", alignItems: "center", gap: 5, paddingHorizontal: 10, borderRadius: 12 },
  addText: { color: colors.accent, fontWeight: "800" },
  workspaceCard: { flexDirection: "row", alignItems: "center", gap: 12, padding: 15, borderRadius: 17, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line },
  workspaceIcon: { width: 44, height: 44, borderRadius: 14, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft },
  workspaceName: { color: colors.ink, fontSize: 16, fontWeight: "800" },
  card: { padding: 16, gap: 11, borderRadius: 18, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line },
  cardTitle: { color: colors.ink, fontSize: 17, fontWeight: "800" },
  input: { minHeight: 46, borderRadius: 12, borderWidth: 1, borderColor: colors.line, color: colors.ink, paddingHorizontal: 12 },
  primary: { minHeight: 46, alignItems: "center", justifyContent: "center", borderRadius: 13, backgroundColor: colors.accent },
  primaryText: { color: colors.white, fontWeight: "800" },
  hint: { color: colors.muted, fontSize: 13, lineHeight: 19 },
  choiceRow: { flexDirection: "row", gap: 7 },
  choice: { flex: 1, alignItems: "center", paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: colors.line },
  choiceActive: { backgroundColor: colors.accent, borderColor: colors.accent },
  choiceText: { color: colors.muted, fontSize: 12, fontWeight: "700" },
  choiceTextActive: { color: "white", fontSize: 12, fontWeight: "800" },
  row: { minHeight: 58, flexDirection: "row", alignItems: "center", gap: 11, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.line },
  rowTitle: { color: colors.ink, fontWeight: "800" },
  error: { color: colors.danger, textAlign: "center", lineHeight: 20 },
  logout: { alignItems: "center", padding: 14 },
  logoutText: { color: colors.danger, fontWeight: "800" },
});
