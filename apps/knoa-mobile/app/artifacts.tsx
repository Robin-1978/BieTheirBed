import { useCallback, useEffect, useRef, useState } from "react";
import { FlatList, Modal, RefreshControl, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { File, Paths } from "expo-file-system";
import * as Sharing from "expo-sharing";
import { useLocalSearchParams } from "expo-router";

import type { ConversationSession } from "@/api/models";
import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { AsyncStateView } from "@/components/AsyncStateView";
import { useI18n } from "@/i18n";
import { useGateway } from "@/state/GatewayProvider";
import { colors, radii, spacing, shadows, typography } from "@/theme";
import { saveArtifactFile } from "@/api/saveArtifactFile";
import { assistantArtifactItems, resolveAssistantArtifactFile } from "@/api/chatArtifacts";
import { formatRelativeTime } from "@/ui/formatRelativeTime";

export default function ArtifactsScreen() {
  const gateway = useGateway();
  const { t, locale } = useI18n();
  const [sessionHandle, setSessionHandle] = useState("");
  const [sessionTitle, setSessionTitle] = useState("");
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<"" | "image" | "file">("");
  const [items, setItems] = useState<Array<{ artifact_id: string; name: string; media_type: string; size: number; kind: string }>>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [opening, setOpening] = useState("");
  const [pickerVisible, setPickerVisible] = useState(false);
  const [conversations, setConversations] = useState<ConversationSession[]>([]);
  const [conversationsLoading, setConversationsLoading] = useState(false);
  const params = useLocalSearchParams<{ sessionHandle?: string }>();
  const autoLoadedSession = useRef("");

  const search = useCallback(async () => {
    if (!gateway.client) {
      setLoading(false);
      setError(t("chat.reconnecting"));
      return;
    }
    if (!sessionHandle.trim()) return;
    setLoading(true);
    setError("");
    try {
      const result = await gateway.runAuthenticated((client) => client.searchArtifacts({ sessionHandle: sessionHandle.trim(), query, kind: kind || undefined }));
      setItems(result.artifacts);
    } catch {
      setError(t("artifacts.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [gateway.client, gateway.runAuthenticated, kind, query, sessionHandle, t]);

  useEffect(() => {
    const firstSession = params.sessionHandle?.trim() || gateway.sessionHandle || "";
    if (firstSession && !sessionHandle) {
      setSessionHandle(firstSession);
      setSessionTitle(t("artifacts.currentSession"));
    }
  }, [gateway.sessionHandle, params.sessionHandle, sessionHandle, t]);

  useEffect(() => {
    const value = sessionHandle.trim();
    if (!value || !gateway.client || autoLoadedSession.current === value) return;
    autoLoadedSession.current = value;
    void search();
  }, [gateway.client, search, sessionHandle]);

  const loadConversations = useCallback(async () => {
    setConversationsLoading(true);
    try {
      const result = await gateway.runAuthenticated((client) => client.listConversationSessions({ includeArchived: true, limit: 100 }));
      setConversations(result.sessions);
    } catch {
      setConversations([]);
    } finally {
      setConversationsLoading(false);
    }
  }, [gateway.runAuthenticated]);

  function openPicker() {
    setPickerVisible(true);
    void loadConversations();
  }

  function selectConversation(session: ConversationSession) {
    setSessionHandle(session.session_handle);
    setSessionTitle(session.title);
    setPickerVisible(false);
    autoLoadedSession.current = "";
  }

  async function openArtifact(item: (typeof items)[number], save = false) {
    if (!sessionHandle.trim() || opening) return;
    setOpening(item.artifact_id);
    setError("");
    try {
      const artifactItem = assistantArtifactItems([item])[0];
      if (!artifactItem) throw new Error("artifact_not_found");
      const resolved = await resolveAssistantArtifactFile({
        artifact: item,
        key: item.artifact_id,
        displayName: item.name,
        cacheFileName: artifactItem.cacheFileName,
        isImage: item.kind === "image",
      }, {
        cachedUri: (name) => {
          const file = new File(Paths.document, `artifact-${name}`);
          return file.exists ? file.uri : null;
        },
        download: (artifactId) => gateway.runAuthenticated((client) => client.downloadArtifact(sessionHandle.trim(), artifactId)),
        write: (name, bytes) => {
          const file = new File(Paths.document, `artifact-${name}`);
          file.create({ overwrite: true, intermediates: true });
          file.write(bytes);
          return file.uri;
        },
      });
      if (save) {
        await saveArtifactFile(resolved, {
          saveDialog: t("artifact.save"),
          saveToFile: t("artifact.saveToFile"),
          cancelled: t("artifact.saveCancelled"),
          saved: t("artifact.savedFile"),
        });
      } else {
        await Sharing.shareAsync(resolved.uri, { mimeType: resolved.mediaType });
      }
    } catch {
      setError(t("artifacts.openFailed"));
    } finally {
      setOpening("");
    }
  }

  return (
    <ScrollView contentContainerStyle={styles.container} refreshControl={<RefreshControl refreshing={loading} onRefresh={() => void search()} />}>
      <View style={styles.hero}><Text style={styles.title}>{t("artifacts.title")}</Text><Text style={styles.meta}>{t("artifacts.detail")}</Text></View>

      <AppPressable style={styles.sessionPicker} onPress={openPicker}>
        <AppIcon name="chat" color={colors.accent} size={18} />
        <View style={styles.sessionPickerContent}>
          <Text style={styles.sessionPickerLabel}>{t("artifacts.selectSession")}</Text>
          {sessionTitle ? <Text style={styles.sessionPickerValue} numberOfLines={1}>{sessionTitle}</Text> : <Text style={styles.sessionPickerHint}>{t("artifacts.selectSessionHint")}</Text>}
        </View>
        <AppIcon name="chevron-down" color={colors.muted} size={16} />
      </AppPressable>

      <View style={styles.filters}>
        {(["", "image", "file"] as const).map((value) => <AppPressable key={value || "all"} style={[styles.filter, kind === value && styles.filterActive]} onPress={() => setKind(value)}><Text style={[styles.filterText, kind === value && styles.filterTextActive]}>{value === "" ? t("artifacts.all") : value === "image" ? t("artifacts.images") : t("artifacts.files")}</Text></AppPressable>)}
      </View>
      <View style={styles.searchRow}><TextInput value={query} onChangeText={setQuery} placeholder={t("artifacts.searchPlaceholder")} placeholderTextColor={colors.muted} style={[styles.input, styles.flex]} onSubmitEditing={() => void search()} /><AppPressable style={styles.button} onPress={() => void search()}><Text style={styles.buttonText}>{t("artifacts.search")}</Text></AppPressable></View>
      {loading && sessionHandle.trim() ? <AsyncStateView state="loading" /> : null}
      {error && !items.length ? (
        <AsyncStateView state="error" message={error} retryLabel={t("common.refresh")} onRetry={() => void search()} />
      ) : null}
      {!loading && !error && !items.length && sessionHandle ? (
        <AsyncStateView state="empty" message={t("artifacts.empty")} />
      ) : null}
      {items.map((item) => <View key={item.artifact_id} style={styles.card}><Text style={styles.name}>{item.name}</Text><Text style={styles.meta}>{item.kind} · {item.media_type} · {formatBytes(item.size)}</Text><View style={styles.actions}><AppPressable style={styles.action} disabled={opening === item.artifact_id} onPress={() => void openArtifact(item)}><Text style={styles.actionText}>{t("artifacts.open")}</Text></AppPressable><AppPressable style={styles.action} disabled={opening === item.artifact_id} onPress={() => void openArtifact(item, true)}><Text style={styles.actionText}>{t("artifacts.save")}</Text></AppPressable></View></View>)}
      {error && items.length ? <Text style={styles.error}>{error}</Text> : null}

      <Modal visible={pickerVisible} animationType="slide" presentationStyle="pageSheet" onRequestClose={() => setPickerVisible(false)}>
        <View style={styles.modalContainer}>
          <View style={styles.modalHeader}>
            <Text style={styles.modalTitle}>{t("artifacts.selectSession")}</Text>
            <AppPressable onPress={() => setPickerVisible(false)} style={styles.modalClose}><AppIcon name="x" color={colors.ink} size={20} /></AppPressable>
          </View>
          {conversationsLoading ? <AsyncStateView state="loading" /> : (
            <FlatList
              data={conversations}
              keyExtractor={(session) => session.session_handle}
              contentContainerStyle={styles.modalList}
              ListEmptyComponent={<AsyncStateView state="empty" message={t("conversations.empty")} />}
              renderItem={({ item: session }) => (
                <AppPressable style={[styles.sessionItem, sessionHandle === session.session_handle && styles.sessionItemActive]} onPress={() => selectConversation(session)}>
                  <Text style={styles.sessionItemTitle} numberOfLines={1}>{session.title}</Text>
                  <Text style={styles.sessionItemMeta}>{session.turn_count} {t("conversations.turns", { count: session.turn_count })} · {formatRelativeTime(session.last_turn_at ? session.last_turn_at * 1000 : session.created_at * 1000, locale)}</Text>
                </AppPressable>
              )}
            />
          )}
        </View>
      </Modal>
    </ScrollView>
  );
}

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

const styles = StyleSheet.create({
  container: { padding: spacing.xlarge, gap: spacing.medium, paddingBottom: 52 },
  hero: { padding: spacing.large, gap: spacing.xsmall, borderRadius: radii.large, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line , ...shadows.card },
  title: { color: colors.ink, ...typography.heading },
  meta: { color: colors.muted, ...typography.small, lineHeight: 18 },
  sessionPicker: { flexDirection: "row", alignItems: "center", gap: spacing.medium, minHeight: 52, paddingHorizontal: spacing.large, borderRadius: radii.medium, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.surface },
  sessionPickerContent: { flex: 1 },
  sessionPickerLabel: { color: colors.muted, ...typography.tiny, fontWeight: "700" },
  sessionPickerValue: { color: colors.ink, fontSize: 15, fontWeight: "700" },
  sessionPickerHint: { color: colors.muted, fontSize: 14 },
  input: { minHeight: 44, borderWidth: 1, borderColor: colors.line, borderRadius: radii.medium, paddingHorizontal: spacing.medium, color: colors.ink, backgroundColor: colors.surface },
  searchRow: { flexDirection: "row", gap: spacing.small, alignItems: "center" },
  flex: { flex: 1 },
  button: { minHeight: 44, paddingHorizontal: spacing.large, borderRadius: radii.medium, justifyContent: "center", backgroundColor: colors.accent },
  buttonText: { color: colors.onAccent, fontWeight: "800" },
  error: { color: colors.danger },
  card: { padding: spacing.large, borderRadius: radii.large, gap: spacing.xsmall, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line , ...shadows.card },
  name: { color: colors.ink, fontWeight: "800" },
  filters: { flexDirection: "row", gap: spacing.small },
  filter: { minHeight: 36, paddingHorizontal: spacing.medium, justifyContent: "center", borderRadius: radii.large, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.surface },
  filterActive: { borderColor: colors.accent, backgroundColor: colors.accentSoft },
  filterText: { color: colors.muted, ...typography.small, fontWeight: "700" },
  filterTextActive: { color: colors.accent },
  actions: { flexDirection: "row", gap: spacing.small, marginTop: spacing.xsmall },
  action: { minHeight: 36, paddingHorizontal: spacing.medium, justifyContent: "center", borderRadius: radii.small, borderWidth: 1, borderColor: colors.accent },
  actionText: { color: colors.accent, fontWeight: "800", fontSize: 12 },
  modalContainer: { flex: 1, backgroundColor: colors.background },
  modalHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: spacing.large, paddingVertical: spacing.medium, borderBottomWidth: 1, borderBottomColor: colors.line },
  modalTitle: { color: colors.ink, fontSize: 18, fontWeight: "800" },
  modalClose: { width: 40, height: 40, alignItems: "center", justifyContent: "center" },
  modalList: { padding: spacing.large, gap: spacing.small },
  sessionItem: { padding: spacing.large, borderRadius: radii.medium, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.surface },
  sessionItemActive: { borderColor: colors.accent, backgroundColor: colors.accentFaint },
  sessionItemTitle: { color: colors.ink, fontSize: 15, fontWeight: "700" },
  sessionItemMeta: { color: colors.muted, fontSize: 12, marginTop: spacing.xsmall },
});
