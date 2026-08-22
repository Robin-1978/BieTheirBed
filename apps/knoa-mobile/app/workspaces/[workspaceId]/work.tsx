import { router, useFocusEffect, useLocalSearchParams } from "expo-router";
import { useCallback, useState } from "react";
import { ActivityIndicator, ScrollView, StyleSheet, Text, View } from "react-native";

import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { WorkspaceCacheBanner } from "@/components/WorkspaceCacheBanner";
import { projectionWorkStatus } from "@/components/workProjectionPresentation";
import { listHubNodes, listWorkspaceWork, type HubNode, type WorkspaceWorkProjection } from "@/hub/hubClient";
import { useI18n } from "@/i18n";
import { useGateway } from "@/state/GatewayProvider";
import { loadWorkspaceCache, mergeWorkspaceCache, type WorkspaceCacheSnapshot } from "@/storage/workspaceCache";
import { colors } from "@/theme";

export default function WorkspaceWorkScreen() {
  const params = useLocalSearchParams<{ workspaceId: string; workspaceName?: string }>();
  const gateway = useGateway();
  const { t } = useI18n();
  const [items, setItems] = useState<WorkspaceWorkProjection[]>([]);
  const [nodes, setNodes] = useState<HubNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [cacheSnapshot, setCacheSnapshot] = useState<WorkspaceCacheSnapshot | null>(null);
  const [working, setWorking] = useState("");
  const [error, setError] = useState("");

  const applyCache = useCallback((snapshot: WorkspaceCacheSnapshot) => {
    setCacheSnapshot(snapshot);
    setItems(snapshot.work);
    setNodes(snapshot.nodes);
  }, []);

  const refresh = useCallback(async (showLoading = true) => {
    if (showLoading) setLoading(true);
    setRefreshing(true);
    setError("");
    try {
      const [work, directory] = await Promise.all([listWorkspaceWork(), listHubNodes()]);
      setItems(work);
      setNodes(directory);
      await mergeWorkspaceCache(params.workspaceId, { work, nodes: directory });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("work.loadFailed"));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [params.workspaceId, t]);

  useFocusEffect(useCallback(() => {
    let active = true;
    void (async () => {
      const cached = await loadWorkspaceCache(params.workspaceId);
      if (active && cached) { applyCache(cached); setLoading(false); }
      await refresh(!cached);
    })();
    return () => { active = false; };
  }, [applyCache, params.workspaceId, refresh]));

  async function open(item: WorkspaceWorkProjection) {
    const node = nodes.find((value) => value.node_id === item.node_id);
    const bound = gateway.nodes.some((value) => value.nodeId === item.node_id);
    if (!node?.online || !bound) {
      setError(!node?.online ? t("work.offlineProjection") : t("work.pairRequired"));
      return;
    }
    setWorking(item.entity_id);
    setError("");
    try {
      await gateway.switchNode(item.node_id);
      const routeParams = {
        workspaceId: params.workspaceId,
        workspaceName: params.workspaceName ?? t("nav.workspace"),
        nodeId: item.node_id,
      };
      if (item.entity_kind === "conversation") {
        await gateway.openConversation(item.entity_id);
        router.push({ pathname: "/chat", params: routeParams });
      } else {
        router.push({ pathname: "/tasks/[id]", params: { ...routeParams, id: item.entity_id } });
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("work.connectFailed"));
    } finally {
      setWorking("");
    }
  }

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <View style={styles.header}>
        <View style={styles.headerIcon}><AppIcon name="chat" color={colors.accent} size={27} /></View>
        <View style={styles.flex}>
          <Text style={styles.title}>{t("work.title")}</Text>
          <Text style={styles.meta}>{t("work.headerDetail")}</Text>
        </View>
        <AppPressable accessibilityLabel={t("common.refresh")} onPress={() => void refresh()} style={styles.iconButton}>
          <AppIcon name="refresh" color={colors.muted} size={20} />
        </AppPressable>
      </View>
      <WorkspaceCacheBanner snapshot={cacheSnapshot} loading={refreshing} error={error} onRefresh={() => void refresh()} />
      {loading ? <ActivityIndicator color={colors.accent} /> : null}
      {!loading && !items.length ? (
        <View style={styles.empty}>
          <Text style={styles.itemTitle}>{t("work.emptyTitle")}</Text>
          <Text style={styles.meta}>{t("work.emptyDetail")}</Text>
        </View>
      ) : null}
      {items.map((item) => {
        const node = nodes.find((value) => value.node_id === item.node_id);
        const kindLabel = item.entity_kind === "conversation" ? t("work.conversation") : t("work.task");
        const status = projectionWorkStatus(item);
        const statusLabel = t(`work.status.${status}` as Parameters<typeof t>[0]);
        return (
          <AppPressable key={`${item.entity_kind}:${item.entity_id}`} style={styles.card} onPress={() => void open(item)}>
            <View style={styles.row}>
              <AppIcon name={item.entity_kind === "conversation" ? "chat" : "tasks"} color={colors.accent} size={22} />
              <View style={styles.flex}>
                <Text style={styles.itemTitle}>{item.title || item.entity_id}</Text>
                <Text style={styles.meta}>{kindLabel} · {statusLabel} · {node?.display_name ?? item.node_id}</Text>
              </View>
              {working === item.entity_id
                ? <ActivityIndicator color={colors.accent} size="small" />
                : <Text style={node?.online ? styles.online : styles.offline}>{node?.online ? t("nodes.online") : t("nodes.offline")}</Text>}
            </View>
            {item.summary ? <Text style={styles.summary} numberOfLines={3}>{item.summary}</Text> : null}
            {status === "waiting_for_you" || item.approval_summary ? <Text style={styles.approval}>{item.approval_summary || t("work.needsYourDecision")}</Text> : null}
          </AppPressable>
        );
      })}
      {error ? <Text style={styles.error}>{error}</Text> : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 17, gap: 12, paddingBottom: 48 },
  header: { flexDirection: "row", alignItems: "center", gap: 12, padding: 16, borderRadius: 18, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line },
  headerIcon: { width: 48, height: 48, borderRadius: 15, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft },
  flex: { flex: 1, minWidth: 0 },
  title: { color: colors.ink, fontSize: 19, fontWeight: "800" },
  meta: { color: colors.muted, fontSize: 12, lineHeight: 18 },
  iconButton: { width: 42, height: 42, alignItems: "center", justifyContent: "center" },
  empty: { padding: 18, gap: 7, borderRadius: 16, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.surface },
  card: { padding: 15, gap: 9, borderRadius: 17, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.surface },
  row: { flexDirection: "row", alignItems: "center", gap: 11 },
  itemTitle: { color: colors.ink, fontSize: 16, fontWeight: "800" },
  summary: { color: colors.ink, lineHeight: 20 },
  approval: { color: colors.warning, fontSize: 12, fontWeight: "700" },
  online: { color: colors.accent, fontWeight: "800", fontSize: 12 },
  offline: { color: colors.muted, fontWeight: "700", fontSize: 12 },
  error: { color: colors.danger, lineHeight: 20 },
});
