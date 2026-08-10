import { router } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import type { ConversationSession } from "@/api/models";
import { AppPressable } from "@/components/AppPressable";
import { removeConversationDraft } from "@/security/conversationDrafts";
import { useGateway } from "@/state/GatewayProvider";
import { removeConversationCache } from "@/storage/conversationCache";
import { colors } from "@/theme";

export default function ConversationHistoryScreen() {
  const gateway = useGateway();
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
      setError("会话记录暂时无法加载，请检查连接后重试");
    } finally {
      setLoading(false);
    }
  }, [gateway.runAuthenticated, showArchived]);

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
      setError("更多会话暂时无法加载，请稍后重试");
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
      setError("会话已发生变化，请刷新后再试");
    } finally {
      setWorking("");
    }
  }

  function confirmDelete(session: ConversationSession) {
    Alert.alert("删除会话？", "会话内容和附件关联将被永久删除，无法恢复。", [
      { text: "取消", style: "cancel" },
      {
        text: "删除",
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
            setError("运行中的会话不能删除，请先停止当前回复");
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
      setError(caught instanceof Error ? caught.message : "会话无法打开");
    } finally {
      setWorking("");
    }
  }

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <View style={styles.headerActions}>
        <AppPressable
          style={styles.primary}
          onPress={() => void gateway.newConversation().then(() => router.replace("/chat"))}
        >
          <Text style={styles.primaryText}>新建会话</Text>
        </AppPressable>
        <AppPressable style={styles.filter} onPress={() => setShowArchived((value) => !value)}>
          <Text style={styles.filterText}>{showArchived ? "隐藏已归档" : "显示已归档"}</Text>
        </AppPressable>
      </View>
      {error ? <Text style={styles.error}>{error}</Text> : null}
      {loading ? <ActivityIndicator color={colors.accent} style={styles.loading} /> : null}
      {!loading && !sessions.length ? <Text style={styles.empty}>还没有会话记录</Text> : null}
      {sessions.map((session) => {
        const isEditing = editing === session.session_handle;
        const isCurrent = gateway.sessionHandle === session.session_handle;
        return (
          <View key={session.session_handle} style={[styles.card, isCurrent && styles.currentCard]}>
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
                  {isCurrent ? "当前会话 · " : ""}{session.turn_count} 轮 · {formatTime(session.last_turn_at ?? session.created_at)}
                </Text>
              </AppPressable>
            )}
            <View style={styles.actions}>
              {isEditing ? (
                <>
                  <AppPressable onPress={() => setEditing("")}><Text style={styles.actionText}>取消</Text></AppPressable>
                  <AppPressable disabled={!title.trim()} onPress={() => void update(session, { title: title.trim() })}><Text style={styles.actionText}>保存</Text></AppPressable>
                </>
              ) : (
                <>
                  <AppPressable onPress={() => { setEditing(session.session_handle); setTitle(session.title); }}><Text style={styles.actionText}>重命名</Text></AppPressable>
                  <AppPressable onPress={() => void update(session, { state: session.state === "archived" ? "active" : "archived" })}>
                    <Text style={styles.actionText}>{session.state === "archived" ? "恢复" : "归档"}</Text>
                  </AppPressable>
                  <AppPressable onPress={() => confirmDelete(session)}><Text style={styles.deleteText}>删除</Text></AppPressable>
                </>
              )}
              {working === session.session_handle ? <ActivityIndicator color={colors.accent} size="small" /> : null}
            </View>
          </View>
        );
      })}
      {nextCursor ? (
        <AppPressable disabled={loadingMore} onPress={() => void loadMore()} style={styles.loadMore}>
          {loadingMore
            ? <ActivityIndicator color={colors.accent} />
            : <Text style={styles.loadMoreText}>加载更多会话</Text>}
        </AppPressable>
      ) : null}
    </ScrollView>
  );
}

function formatTime(value: number): string {
  return new Date(value * 1000).toLocaleString("zh-CN", { hour12: false });
}

const styles = StyleSheet.create({
  container: { padding: 16, gap: 12, paddingBottom: 48 },
  headerActions: { flexDirection: "row", gap: 10 },
  primary: { backgroundColor: colors.accent, borderRadius: 12, paddingHorizontal: 16, paddingVertical: 11 },
  primaryText: { color: "white", fontWeight: "700" },
  filter: { borderColor: colors.line, borderWidth: 1, borderRadius: 12, paddingHorizontal: 14, paddingVertical: 11, backgroundColor: colors.surface },
  filterText: { color: colors.accent, fontWeight: "600" },
  card: { backgroundColor: colors.surface, borderRadius: 16, borderWidth: 1, borderColor: colors.line, padding: 15, gap: 12 },
  currentCard: { borderColor: colors.accent, borderWidth: 2 },
  title: { color: colors.ink, fontSize: 17, fontWeight: "700", marginBottom: 5 },
  meta: { color: colors.muted, fontSize: 13 },
  titleInput: { color: colors.ink, borderWidth: 1, borderColor: colors.accent, borderRadius: 10, padding: 10, backgroundColor: colors.background },
  actions: { flexDirection: "row", gap: 18, alignItems: "center" },
  actionText: { color: colors.accent, fontWeight: "600" },
  deleteText: { color: colors.danger, fontWeight: "600" },
  loading: { marginTop: 60 },
  empty: { color: colors.muted, textAlign: "center", marginTop: 60 },
  error: { color: colors.danger, lineHeight: 20 },
  loadMore: { alignItems: "center", paddingVertical: 14 },
  loadMoreText: { color: colors.accent, fontWeight: "700" },
});
