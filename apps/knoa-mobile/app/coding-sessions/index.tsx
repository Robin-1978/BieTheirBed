import { router } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";

import type { CodingSessionSummary } from "@/api/models";
import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { useI18n } from "@/i18n";
import {
  codingSessionErrorMessage,
  codingSessionStateLabel,
  loadCodingSessionList,
} from "@/state/codingSessionState";
import { useSession } from "@/state/GatewayProvider";
import { createSerialPoller } from "@/state/serialPoller";
import { colors, radii, shadows, spacing, typography } from "@/theme";
import { formatRelativeTime } from "@/ui/formatRelativeTime";

const REFRESH_INTERVAL_MS = 5000;

export default function CodingSessionsScreen() {
  const { runAuthenticated } = useSession();
  const { locale } = useI18n();
  const [sessions, setSessions] = useState<CodingSessionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [pulling, setPulling] = useState(false);
  const [loadError, setLoadError] = useState("");

  // Background polls are silent: only a pull gesture drives the RefreshControl,
  // so the spinner does not flash every few seconds while the list is read.
  const refresh = useCallback(async (pull = false) => {
    if (pull) setPulling(true);
    try {
      const snapshot = await loadCodingSessionList({
        list: () => runAuthenticated((client) => client.listCodingSessions({ limit: 100 })),
      });
      setSessions(snapshot.sessions);
      setLoadError(snapshot.error);
    } catch (cause) {
      setLoadError(codingSessionErrorMessage(cause, "open", "无法读取外部编码会话"));
    } finally {
      setLoading(false);
      setPulling(false);
    }
  }, [runAuthenticated]);

  useEffect(() => {
    const poller = createSerialPoller({ intervalMs: REFRESH_INTERVAL_MS, run: () => refresh() });
    poller.start();
    return () => poller.stop();
  }, [refresh]);

  return (
    <ScrollView
      contentContainerStyle={styles.container}
      refreshControl={<RefreshControl refreshing={pulling} onRefresh={() => void refresh(true)} />}
    >
      <View style={styles.intro}>
        <View style={styles.icon}><AppIcon name="code" color={colors.onAccent} size={24} /></View>
        <View style={styles.introText}>
          <Text style={styles.heading}>编码会话</Text>
          <Text style={styles.subheading}>点开一个会话查看内容并继续。运行中的会话只读。</Text>
        </View>
      </View>
      {loadError ? <Text style={styles.error}>{loadError}</Text> : null}
      {loading && !sessions.length ? <ActivityIndicator color={colors.accent} /> : null}
      {!loading && !sessions.length ? (
        <Text style={styles.empty}>没有发现可访问的 Codex 或 OpenCode 会话。请确认这台电脑上的 Codex 已安装、OpenCode 服务正在运行。</Text>
      ) : null}
      <View style={styles.list}>
        {sessions.map((item) => (
          <AppPressable
            key={`${item.provider}:${item.external_session_id}`}
            onPress={() =>
              router.push({
                pathname: "/coding-sessions/detail",
                params: { provider: item.provider, id: item.external_session_id },
              })
            }
            style={styles.card}
          >
            <View style={styles.cardHeader}>
              <Text style={styles.provider}>{item.provider.toUpperCase()}</Text>
              <Text style={[styles.state, item.state === "running" && styles.running]}>
                {codingSessionStateLabel(item.state)}
              </Text>
            </View>
            <Text style={styles.title} numberOfLines={1}>{item.title}</Text>
            <Text style={styles.meta} numberOfLines={1}>
              {item.directory || "未提供工作目录"}
              {item.updated_at > 0 ? ` · ${formatRelativeTime(item.updated_at * 1000, locale)}` : ""}
            </Text>
            {item.latest_assistant_message ? <Text style={styles.preview} numberOfLines={2}>{item.latest_assistant_message}</Text> : null}
          </AppPressable>
        ))}
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: spacing.large, gap: spacing.medium, paddingBottom: 48 },
  intro: { flexDirection: "row", gap: spacing.medium, alignItems: "center" },
  icon: { width: 46, height: 46, borderRadius: 23, backgroundColor: colors.accent, alignItems: "center", justifyContent: "center" },
  introText: { flex: 1, gap: spacing.xsmall },
  heading: { color: colors.ink, ...typography.heading },
  subheading: { color: colors.muted, lineHeight: 20 },
  list: { gap: spacing.medium },
  card: { backgroundColor: colors.surface, borderColor: colors.line, borderWidth: 1, borderRadius: radii.large, padding: spacing.large, gap: spacing.small, ...shadows.card },
  cardHeader: { flexDirection: "row", justifyContent: "space-between" },
  provider: { color: colors.accent, fontWeight: "800", fontSize: 12 },
  state: { color: colors.muted, fontSize: 12 },
  running: { color: colors.warning },
  title: { color: colors.ink, ...typography.subheading },
  meta: { color: colors.muted, fontSize: 12 },
  preview: { color: colors.ink, lineHeight: 20 },
  empty: { color: colors.muted, lineHeight: 22 },
  error: { color: colors.danger, lineHeight: 20 },
});
