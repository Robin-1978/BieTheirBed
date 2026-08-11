import { router } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import type { ConversationSession } from "@/api/models";
import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { removeConversationDraft } from "@/security/conversationDrafts";
import { useGateway } from "@/state/GatewayProvider";
import { removeConversationCache } from "@/storage/conversationCache";
import { colors } from "@/theme";
import { useI18n } from "@/i18n";

export default function ConversationHistoryScreen() {
  const gateway = useGateway();
  const { t, locale } = useI18n();
  const [sessions, setSessions] = useState<ConversationSession[]>([]);
  const [nextCursor, setNextCursor] = useState("");
  const [loadingMore, setLoadingMore] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState("");
  const [editing, setEditing] = useState("");
  const [title, setTitle] = useState("");
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await gateway.runAuthenticated((client) => client.listConversationSessions({
        includeArchived: showArchived,
        limit: 50,
      }));
      setSessions(result.sessions);
      setNextCursor(result.nextCursor);
    } catch {
      setError(t("conversations.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [gateway.runAuthenticated, showArchived, t]);

  useEffect(() => { void refresh(); }, [refresh]);

  async function loadMore() {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    setError("");
    try {
      const result = await gateway.runAuthenticated((client) => client.listConversationSessions({
        includeArchived: showArchived,
        limit: 50,
        cursor: nextCursor,
      }));
      setSessions((current) => {
        const existing = new Set(current.map((session) => session.session_handle));
        return [...current, ...result.sessions.filter((session) => !existing.has(session.session_handle))];
      });
      setNextCursor(result.nextCursor);
    } catch {
      setError(t("conversations.moreFailed"));
    } finally {
      setLoadingMore(false);
    }
  }

  async function update(session: ConversationSession, changes: { title?: string; state?: "active" | "archived" }) {
    if (working) return;
    setWorking(session.session_handle);
    setError("");
    try {
      await gateway.runAuthenticated((client) => client.updateConversationSession(session.session_handle, {
        ...changes,
        expectedRevision: session.revision,
      }));
      setEditing("");
      await refresh();
    } catch {
      setError(t("conversations.conflict"));
    } finally {
      setWorking("");
    }
  }

  function confirmDelete(session: ConversationSession) {
    Alert.alert(t("conversations.deleteTitle"), t("conversations.deleteBody"), [
      { text: t("common.cancel"), style: "cancel" },
      {
        text: t("common.delete"),
        style: "destructive",
        onPress: () => void (async () => {
          setWorking(session.session_handle);
          try {
            await gateway.runAuthenticated((client) => client.deleteConversationSession(session.session_handle));
            await removeConversationDraft(session.session_handle);
            removeConversationCache(session.session_handle);
            if (gateway.sessionHandle === session.session_handle) await gateway.newConversation();
            await refresh();
          } catch {
            setError(t("conversations.deleteActive"));
          } finally {
            setWorking("");
          }
        })(),
      },
    ]);
  }

  async function open(session: ConversationSession) {
    if (session.state === "archived") return;
    setWorking(session.session_handle);
    try {
      await gateway.openConversation(session.session_handle);
      router.replace("/chat");
    } catch (caught) {
      setError(t("conversations.openFailed"));
    } finally {
      setWorking("");
    }
  }

  return (
    <FlatList
      data={sessions}
      keyExtractor={(session) => session.session_handle}
      keyboardShouldPersistTaps="handled"
      contentContainerStyle={styles.container}
      ListHeaderComponent={(
        <>
          <View style={styles.headerActions}>
            <AppPressable accessibilityLabel={t("conversations.new")} style={styles.primary} onPress={() => void gateway.newConversation().then(() => router.replace("/chat"))}>
              <AppIcon name="new-topic" color={colors.white} size={21} />
            </AppPressable>
            <AppPressable style={styles.filter} onPress={() => setShowArchived((value) => !value)}>
              <Text style={styles.filterText}>{showArchived ? t("conversations.hideArchived") : t("conversations.showArchived")}</Text>
            </AppPressable>
          </View>
          {error ? <Text style={styles.error}>{error}</Text> : null}
          {loading ? <ActivityIndicator color={colors.accent} style={styles.loading} /> : null}
        </>
      )}
      ListEmptyComponent={!loading ? <Text style={styles.empty}>{t("conversations.empty")}</Text> : null}
      renderItem={({ item: session }) => {
        const isEditing = editing === session.session_handle;
        const isCurrent = gateway.sessionHandle === session.session_handle;
        return (
          <View style={[styles.card, isCurrent && styles.currentCard]}>
            {isEditing ? (
              <TextInput
                autoFocus
                maxLength={120}
                onChangeText={setTitle}
                style={styles.titleInput}
                value={title}
              />
            ) : (
              <AppPressable disabled={session.state === "archived"} onPress={() => void open(session)}>
                <Text style={styles.title}>{session.title}</Text>
                <Text style={styles.meta}>
                  {isCurrent ? `${t("conversations.current")} · ` : ""}{t("conversations.turns", { count: session.turn_count })} · {formatTime(session.last_turn_at ?? session.created_at, locale)}
                </Text>
              </AppPressable>
            )}
            <View style={styles.actions}>
              {isEditing ? (
                <>
                  <IconAction label={t("conversations.cancelEdit")} icon="x" onPress={() => setEditing("")} />
                  <IconAction label={t("conversations.saveName")} icon="check" disabled={!title.trim()} onPress={() => void update(session, { title: title.trim() })} />
                </>
              ) : (
                <>
                  <IconAction label={t("conversations.rename")} icon="edit" onPress={() => { setEditing(session.session_handle); setTitle(session.title); }} />
                  <IconAction
                    label={session.state === "archived" ? t("conversations.restore") : t("conversations.archive")}
                    icon={session.state === "archived" ? "restore" : "archive"}
                    onPress={() => void update(session, { state: session.state === "archived" ? "active" : "archived" })}
                  />
                  <IconAction label={t("common.delete")} icon="trash" danger onPress={() => confirmDelete(session)} />
                </>
              )}
              {working === session.session_handle ? <ActivityIndicator color={colors.accent} size="small" /> : null}
            </View>
          </View>
        );
      }}
      ListFooterComponent={nextCursor ? (
        <AppPressable disabled={loadingMore} onPress={() => void loadMore()} style={styles.loadMore}>
          {loadingMore
            ? <ActivityIndicator color={colors.accent} />
            : <Text style={styles.loadMoreText}>{t("conversations.loadMore")}</Text>}
        </AppPressable>
      ) : null}
    />
  );
}

function IconAction({ label, icon, danger = false, disabled = false, onPress }: { label: string; icon: "archive" | "check" | "edit" | "restore" | "trash" | "x"; danger?: boolean; disabled?: boolean; onPress(): void }) {
  return (
    <AppPressable
      accessibilityLabel={label}
      disabled={disabled}
      onPress={onPress}
      style={styles.iconAction}
    >
      <AppIcon name={icon} color={danger ? colors.danger : colors.accent} size={19} />
    </AppPressable>
  );
}

function formatTime(value: number, locale: string): string {
  return new Date(value * 1000).toLocaleString(locale, { hour12: false });
}

const styles = StyleSheet.create({
  container: { padding: 16, gap: 12, paddingBottom: 48 },
  headerActions: { flexDirection: "row", gap: 10 },
  primary: { backgroundColor: colors.accent, borderRadius: 12, width: 44, minHeight: 42, alignItems: "center", justifyContent: "center" },
  filter: { borderColor: colors.line, borderWidth: 1, borderRadius: 12, paddingHorizontal: 14, paddingVertical: 11, backgroundColor: colors.surface },
  filterText: { color: colors.accent, fontWeight: "600" },
  card: { backgroundColor: colors.surface, borderRadius: 16, borderWidth: 1, borderColor: colors.line, padding: 15, gap: 12 },
  currentCard: { borderColor: colors.accent, borderWidth: 2 },
  title: { color: colors.ink, fontSize: 17, fontWeight: "700", marginBottom: 5 },
  meta: { color: colors.muted, fontSize: 13 },
  titleInput: { color: colors.ink, borderWidth: 1, borderColor: colors.accent, borderRadius: 10, padding: 10, backgroundColor: colors.background },
  actions: { flexDirection: "row", gap: 18, alignItems: "center" },
  iconAction: { width: 44, height: 44, borderRadius: 11, alignItems: "center", justifyContent: "center", backgroundColor: colors.background },
  loading: { marginTop: 60 },
  empty: { color: colors.muted, textAlign: "center", marginTop: 60 },
  error: { color: colors.danger, lineHeight: 20 },
  loadMore: { alignItems: "center", paddingVertical: 14 },
  loadMoreText: { color: colors.accent, fontWeight: "700" },
});
