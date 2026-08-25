import { router, useFocusEffect } from "expo-router";
import { useCallback, useState } from "react";
import { ActivityIndicator, Alert, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";

import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { AsyncStateView } from "@/components/AsyncStateView";
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
import { useI18n } from "@/i18n";
import {
  loadNavigationPreference,
  rememberWorkspace,
  setLandingPreference,
  type LandingPreference,
} from "@/navigation/navigationPreference";
import { useGateway } from "@/state/GatewayProvider";
import { clearAppCache } from "@/storage/appCache";
import { clearTaskReminders } from "@/reminders/taskReminders";
import { colors, radii, shadows, spacing, typography } from "@/theme";

export default function AccountHomeScreen() {
  const gateway = useGateway();
  const { t } = useI18n();
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
        displayName: t("account.personalWorkspace"),
        kind: "personal",
        role: "owner",
        workspacePath: "",
      }]);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("account.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [t]);

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
      setError(caught instanceof Error ? caught.message : t("account.openWorkspaceFailed"));
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
      setError(caught instanceof Error ? caught.message : t("account.createWorkspaceFailed"));
    } finally {
      setWorking("");
    }
  }

  function confirmLogout() {
    Alert.alert(t("account.logoutTitle"), t("account.logoutMessage"), [
      { text: t("common.cancel"), style: "cancel" },
      {
        text: t("account.logoutConfirm"),
        style: "destructive",
        onPress: () => void (async () => {
          setWorking("logout");
          try {
            await gateway.disconnectNode();
            await logoutHostedAccount();
            clearAppCache("all");
            await clearTaskReminders();
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
          <Text style={styles.accountName}>{profile?.displayName || t("account.defaultOwner")}</Text>
          <Text style={styles.meta}>{profile?.loginIdentity || rootUrl}</Text>
        </View>
        <AppPressable accessibilityLabel={t("account.refresh")} onPress={() => void refresh()} style={styles.iconButton}>
          <AppIcon name="refresh" color={colors.muted} size={20} />
        </AppPressable>
      </View>

      <View style={styles.card}>
        <Text style={styles.cardTitle}>{t("account.appSection")}</Text>
        <Row icon="settings" title={t("nav.appSettings")} detail={t("account.appSettingsDetail")} onPress={() => router.push("/settings/app")} />
        <Row icon="refresh" title={t("nav.update")} detail={t("account.updateDetail")} onPress={() => router.push("/update")} />
      </View>

      <View style={styles.sectionHeader}>
        <Text style={styles.sectionTitle}>{t("account.workspaceSection")}</Text>
        {profile ? (
          <AppPressable onPress={() => setCreating((value) => !value)} style={styles.addButton}>
            <AppIcon name={creating ? "x" : "plus"} color={colors.accent} size={19} />
            <Text style={styles.addText}>{creating ? t("account.createCancel") : t("account.create")}</Text>
          </AppPressable>
        ) : <Text style={styles.meta}>{workspaces.length}</Text>}
      </View>

      {creating ? (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>{t("account.createWorkspaceTitle")}</Text>
          <TextInput
            value={workspaceName}
            onChangeText={setWorkspaceName}
            placeholder={t("account.workspaceNamePlaceholder")}
            placeholderTextColor={colors.muted}
            style={styles.input}
          />
          <AppPressable disabled={working === "create-workspace"} onPress={() => void createWorkspace()} style={styles.primary}>
            {working === "create-workspace"
              ? <ActivityIndicator color={colors.onAccent} />
              : <Text style={styles.primaryText}>{t("account.createAndOpen")}</Text>}
          </AppPressable>
        </View>
      ) : null}

      {loading ? <AsyncStateView state="loading" /> : null}
      {error && !loading ? (
        <AsyncStateView state="error" message={error} retryLabel={t("account.refresh")} onRetry={() => void refresh()} />
      ) : null}
      {!loading && !error ? workspaces.map((workspace) => (
        <AppPressable key={workspace.workspaceId} disabled={Boolean(working)} onPress={() => void openWorkspace(workspace)} style={styles.workspaceCard}>
          <View style={styles.workspaceIcon}><AppIcon name="workspace" color={colors.accent} size={23} /></View>
          <View style={styles.flex}>
            <Text style={styles.workspaceName}>{workspace.displayName}</Text>
            <Text style={styles.meta}>
              {workspace.kind} · {roleLabel(workspace.role, t)}
              {workspace.workspaceId === currentWorkspaceId ? t("account.current") : ""}
            </Text>
          </View>
          {working === workspace.workspaceId
            ? <ActivityIndicator color={colors.accent} size="small" />
            : <AppIcon name="chevron-right" color={colors.muted} size={20} />}
        </AppPressable>
      )) : null}

      <View style={styles.card}>
        <Text style={styles.cardTitle}>{t("account.landingTitle")}</Text>
        <Text style={styles.hint}>{t("account.landingHint")}</Text>
        <View style={styles.choiceRow}>
          {([
            ["last", t("account.landingLast")],
            ["workspace", t("account.landingWorkspace")],
            ["account", t("account.landingAccount")],
          ] as const).map(([value, label]) => (
            <AppPressable key={value} onPress={() => void chooseLanding(value)} style={[styles.choice, landing === value && styles.choiceActive]}>
              <Text style={landing === value ? styles.choiceTextActive : styles.choiceText}>{label}</Text>
            </AppPressable>
          ))}
        </View>
      </View>

      <AppPressable disabled={working === "logout"} onPress={confirmLogout} style={styles.logout}>
        <Text style={styles.logoutText}>{working === "logout" ? t("account.loggingOut") : t("account.logout")}</Text>
      </AppPressable>
    </ScrollView>
  );
}

function roleLabel(role: HostedWorkspace["role"], t: ReturnType<typeof useI18n>["t"]) {
  if (role === "admin") return t("account.roleAdmin");
  if (role === "member") return t("account.roleMember");
  return t("account.roleOwner");
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
  container: { padding: spacing.large, gap: spacing.medium, paddingBottom: spacing.xlarge * 2 },
  accountCard: { flexDirection: "row", alignItems: "center", gap: spacing.medium, padding: spacing.large, borderRadius: radii.large, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line, ...shadows.card },
  accountIcon: { width: 52, height: 52, borderRadius: radii.large, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft },
  accountName: { color: colors.ink, fontSize: 19, fontWeight: "800" },
  flex: { flex: 1, minWidth: 0 },
  meta: { color: colors.muted, ...typography.small, marginTop: 2 },
  iconButton: { width: 42, height: 42, alignItems: "center", justifyContent: "center", borderRadius: radii.medium },
  sectionHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginTop: spacing.xsmall },
  sectionTitle: { color: colors.ink, ...typography.heading },
  addButton: { minHeight: 40, flexDirection: "row", alignItems: "center", gap: spacing.xsmall, paddingHorizontal: spacing.small, borderRadius: radii.medium },
  addText: { color: colors.accent, fontWeight: "800" },
  workspaceCard: { flexDirection: "row", alignItems: "center", gap: spacing.medium, padding: spacing.medium, borderRadius: radii.large, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line, ...shadows.card },
  workspaceIcon: { width: 44, height: 44, borderRadius: radii.medium, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft },
  workspaceName: { color: colors.ink, fontSize: 16, fontWeight: "800" },
  card: { padding: spacing.large, gap: spacing.medium, borderRadius: radii.large, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line, ...shadows.card },
  cardTitle: { color: colors.ink, ...typography.subheading, fontWeight: "800" },
  input: { minHeight: 46, borderRadius: radii.medium, borderWidth: 1, borderColor: colors.line, color: colors.ink, paddingHorizontal: spacing.medium },
  primary: { minHeight: 46, alignItems: "center", justifyContent: "center", borderRadius: radii.medium, backgroundColor: colors.accent },
  primaryText: { color: colors.onAccent, fontWeight: "800" },
  hint: { color: colors.muted, ...typography.caption, lineHeight: 19 },
  choiceRow: { flexDirection: "row", gap: spacing.small },
  choice: { flex: 1, alignItems: "center", paddingVertical: spacing.small, borderRadius: radii.medium, borderWidth: 1, borderColor: colors.line },
  choiceActive: { backgroundColor: colors.accent, borderColor: colors.accent },
  choiceText: { color: colors.muted, ...typography.small, fontWeight: "700" },
  choiceTextActive: { color: colors.onAccent, ...typography.small, fontWeight: "800" },
  row: { minHeight: 58, flexDirection: "row", alignItems: "center", gap: spacing.medium, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.line },
  rowTitle: { color: colors.ink, fontWeight: "800" },
  logout: { alignItems: "center", padding: spacing.medium },
  logoutText: { color: colors.danger, fontWeight: "800" },
});
