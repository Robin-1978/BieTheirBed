import { useFocusEffect, useLocalSearchParams } from "expo-router";
import { useCallback, useState } from "react";
import { ActivityIndicator, Alert, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";

import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { AsyncStateView } from "@/components/AsyncStateView";
import {
  addHostedWorkspaceMember,
  listHostedWorkspaceMembers,
  listHostedWorkspaces,
  removeHostedWorkspaceMember,
  type HostedWorkspaceMember,
} from "@/hub/hubClient";
import { useI18n } from "@/i18n";
import { colors, radii, spacing, shadows, typography } from "@/theme";

export default function WorkspaceMembersScreen() {
  const params = useLocalSearchParams<{ workspaceId: string }>();
  const { t } = useI18n();
  const workspaceId = Array.isArray(params.workspaceId) ? params.workspaceId[0] ?? "" : params.workspaceId ?? "";
  const [members, setMembers] = useState<HostedWorkspaceMember[]>([]);
  const [login, setLogin] = useState("");
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState("");
  const [error, setError] = useState("");
  const [canManage, setCanManage] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const workspaces = await listHostedWorkspaces();
      const workspace = workspaces.find((item) => item.workspaceId === workspaceId);
      setCanManage(Boolean(workspace?.kind === "shared" && workspace.role !== "member"));
      setMembers(await listHostedWorkspaceMembers(workspaceId));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("members.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [t, workspaceId]);

  useFocusEffect(useCallback(() => { void refresh(); }, [refresh]));

  async function add() {
    if (!login.trim()) return;
    setWorking("add");
    setError("");
    try {
      await addHostedWorkspaceMember(workspaceId, login.trim(), "member");
      setLogin("");
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("members.addFailed"));
    } finally {
      setWorking("");
    }
  }

  function remove(member: HostedWorkspaceMember) {
    const name = member.displayName || member.loginIdentity;
    Alert.alert(t("members.removeTitle"), t("members.removeMessage", { name }), [
      { text: t("common.cancel"), style: "cancel" },
      {
        text: t("members.removeConfirm"),
        style: "destructive",
        onPress: () => void (async () => {
          setWorking(member.accountId);
          setError("");
          try {
            await removeHostedWorkspaceMember(workspaceId, member.accountId);
            await refresh();
          } catch (caught) {
            setError(caught instanceof Error ? caught.message : t("members.removeFailed"));
          } finally {
            setWorking("");
          }
        })(),
      },
    ]);
  }

  function roleLabel(role: HostedWorkspaceMember["role"]) {
    if (role === "owner") return t("account.roleOwner");
    if (role === "admin") return t("account.roleAdmin");
    return t("account.roleMember");
  }

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <View style={styles.header}>
        <View style={styles.icon}><AppIcon name="user" color={colors.accent} size={26} /></View>
        <View style={styles.flex}>
          <Text style={styles.title}>{t("members.title")}</Text>
          <Text style={styles.meta}>{t("members.headerDetail")}</Text>
        </View>
      </View>

      {canManage ? (
        <View style={styles.card}>
          <Text style={styles.sectionTitle}>{t("members.addSection")}</Text>
          <TextInput
            value={login}
            onChangeText={setLogin}
            placeholder={t("members.loginPlaceholder")}
            placeholderTextColor={colors.muted}
            autoCapitalize="none"
            style={styles.input}
          />
          <AppPressable disabled={working === "add"} style={styles.primary} onPress={() => void add()}>
            {working === "add"
              ? <ActivityIndicator color={colors.white} />
              : <Text style={styles.primaryText}>{t("members.add")}</Text>}
          </AppPressable>
        </View>
      ) : !loading ? (
        <View style={styles.card}>
          <Text style={styles.sectionTitle}>{t("members.manageSection")}</Text>
          <Text style={styles.meta}>{t("members.manageHint")}</Text>
        </View>
      ) : null}

      {loading ? <AsyncStateView state="loading" /> : null}
      {error && !loading ? (
        <AsyncStateView state="error" message={error} retryLabel={t("common.refresh")} onRetry={() => void refresh()} />
      ) : null}

      {!loading && !error ? members.map((member) => (
        <View key={member.accountId} style={styles.card}>
          <View style={styles.row}>
            <View style={styles.flex}>
              <Text style={styles.memberName}>{member.displayName || member.loginIdentity}</Text>
              <Text style={styles.meta}>{member.loginIdentity} · {roleLabel(member.role)}</Text>
            </View>
            {canManage && member.role !== "owner" ? (
              <AppPressable disabled={Boolean(working)} onPress={() => remove(member)}>
                <Text style={styles.remove}>{t("members.remove")}</Text>
              </AppPressable>
            ) : (
              <Text style={styles.owner}>{roleLabel(member.role)}</Text>
            )}
          </View>
        </View>
      )) : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: spacing.large, gap: spacing.medium, paddingBottom: 52 },
  header: { flexDirection: "row", alignItems: "center", gap: spacing.medium, padding: spacing.large, borderRadius: radii.large, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line , ...shadows.card },
  icon: { width: 48, height: 48, borderRadius: radii.large, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft },
  flex: { flex: 1, minWidth: 0 },
  title: { color: colors.ink, ...typography.heading },
  meta: { color: colors.muted, ...typography.small, lineHeight: 18 },
  card: { padding: spacing.large, gap: spacing.medium, borderRadius: radii.large, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line , ...shadows.card },
  sectionTitle: { color: colors.ink, ...typography.subheading, fontWeight: "800" },
  input: { minHeight: 46, borderRadius: radii.medium, borderWidth: 1, borderColor: colors.line, color: colors.ink, paddingHorizontal: spacing.medium },
  primary: { minHeight: 46, alignItems: "center", justifyContent: "center", borderRadius: radii.medium, backgroundColor: colors.accent },
  primaryText: { color: colors.white, fontWeight: "800" },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.medium },
  memberName: { color: colors.ink, ...typography.subheading, fontWeight: "800" },
  remove: { color: colors.danger, fontWeight: "800" },
  owner: { color: colors.accent, ...typography.small, fontWeight: "800" },
});
