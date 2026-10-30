import { router, useLocalSearchParams } from "expo-router";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import type { CodingSessionDetail, CodingSessionSummary } from "@/api/models";
import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import {
  type CodingSessionKey,
  canInterruptCodingSession,
  codingSessionErrorMessage,
  interruptOutcomeMessage,
  loadCodingSessionSnapshot,
  sameCodingSession,
} from "@/state/codingSessionState";
import { useSession } from "@/state/GatewayProvider";
import { createRequestSequence } from "@/state/requestSequence";
import { createSerialPoller } from "@/state/serialPoller";
import { colors, radii, shadows, spacing, typography } from "@/theme";

const REFRESH_INTERVAL_MS = 5000;

export default function CodingSessionsScreen() {
  const gateway = useSession();
  const params = useLocalSearchParams<{ workspaceId?: string; workspaceName?: string; nodeId?: string }>();
  const [sessions, setSessions] = useState<CodingSessionSummary[]>([]);
  const [selected, setSelected] = useState<CodingSessionDetail | null>(null);
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [working, setWorking] = useState("");
  // `error` belongs to the last user action; `loadError` to the last refresh,
  // so a background refresh never wipes the message of a failed attach/prompt.
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  // The polling callback reads the selection through this ref instead of closing
  // over `selected`: depending on state that the callback itself sets re-created
  // it after every response and re-ran the effect with no delay in between.
  const selectedKeyRef = useRef<CodingSessionKey | null>(null);
  // Responses to taps arrive in any order; only the latest open may reach the screen.
  const [openSequence] = useState(createRequestSequence);

  const select = useCallback((detail: CodingSessionDetail | null) => {
    selectedKeyRef.current = detail
      ? { provider: detail.session.provider, external_session_id: detail.session.external_session_id }
      : null;
    setSelected(detail);
  }, []);

  const refresh = useCallback(async (background = false) => {
    if (background) setRefreshing(true);
    else setLoading(true);
    try {
      const snapshot = await loadCodingSessionSnapshot({
        list: () => gateway.runAuthenticated((client) => client.listCodingSessions({ limit: 100 })),
        getDetail: (key) => gateway.runAuthenticated((client) => client.getCodingSession(key.provider, key.external_session_id)),
        selectedKey: () => selectedKeyRef.current,
      });
      setSessions(snapshot.sessions);
      if (snapshot.detail) select(snapshot.detail);
      setLoadError(snapshot.error);
    } catch (cause) {
      setLoadError(codingSessionErrorMessage(cause, "open", "无法读取外部编码会话"));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [gateway.runAuthenticated, select]);

  useEffect(() => {
    // The next refresh starts REFRESH_INTERVAL_MS after the previous one has
    // finished, so a slow gateway can never stack requests.
    const poller = createSerialPoller({
      intervalMs: REFRESH_INTERVAL_MS,
      run: (attempt) => refresh(attempt > 0),
    });
    poller.start();
    return () => poller.stop();
  }, [refresh]);

  async function openSession(item: CodingSessionSummary) {
    const ticket = openSequence.begin();
    setWorking(item.external_session_id);
    setError("");
    try {
      const detail = await gateway.runAuthenticated((client) => client.getCodingSession(item.provider, item.external_session_id));
      if (openSequence.isLatest(ticket)) select(detail);
    } catch (cause) {
      if (openSequence.isLatest(ticket)) setError(codingSessionErrorMessage(cause, "open", "无法读取会话详情"));
    } finally {
      setWorking("");
    }
  }

  function closeSession() {
    openSequence.cancel();
    select(null);
  }

  async function attach() {
    if (!selected) return;
    if (!gateway.sessionHandle) {
      Alert.alert("需要先打开小诺会话", "请先返回聊天页创建或打开一个小诺会话，再绑定外部编码会话。");
      return;
    }
    setWorking("attach");
    setError("");
    try {
      await gateway.runAuthenticated((client) => client.attachCodingSession(selected.session.provider, selected.session.external_session_id, gateway.sessionHandle));
      Alert.alert("已绑定", "这个 Codex/OpenCode 会话现在归属于当前小诺会话。");
    } catch (cause) {
      setError(codingSessionErrorMessage(cause, "attach", "绑定失败"));
    } finally {
      setWorking("");
    }
  }

  async function continueSession() {
    if (!selected || !text.trim()) return;
    setWorking("prompt");
    setError("");
    try {
      await gateway.runAuthenticated((client) => client.continueCodingSession(selected.session.provider, selected.session.external_session_id, text.trim()));
      setText("");
      await openSession(selected.session);
    } catch (cause) {
      setError(codingSessionErrorMessage(cause, "prompt", "继续会话失败；如果桌面端正在运行，请等待它结束"));
    } finally {
      setWorking("");
    }
  }

  async function interrupt() {
    if (!selected) return;
    setWorking("interrupt");
    setError("");
    try {
      const interrupted = await gateway.runAuthenticated((client) => client.interruptCodingSession(selected.session.provider, selected.session.external_session_id));
      await openSession(selected.session);
      const outcome = interruptOutcomeMessage(interrupted);
      if (outcome) setError(outcome);
    } catch (cause) {
      setError(codingSessionErrorMessage(cause, "interrupt", "中断失败"));
    } finally {
      setWorking("");
    }
  }

  return (
    <ScrollView
      contentContainerStyle={styles.container}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void refresh(true)} />}
    >
      <View style={styles.intro}>
        <View style={styles.icon}><AppIcon name="code" color={colors.onAccent} size={24} /></View>
        <View style={styles.introText}>
          <Text style={styles.heading}>编码会话</Text>
          <Text style={styles.subheading}>查看并继续桌面端 Codex 与 OpenCode 会话。运行中的会话只读。</Text>
        </View>
      </View>
      {loadError ? <Text style={styles.error}>{loadError}</Text> : null}
      {error ? <Text style={styles.error}>{error}</Text> : null}
      {loading && !sessions.length ? <ActivityIndicator color={colors.accent} /> : null}
      {!loading && !sessions.length ? <Text style={styles.empty}>没有发现可访问的 Codex/OpenCode 会话。请确认 Codex app-server 或 OpenCode 服务已启动。</Text> : null}
      <View style={styles.list}>
        {sessions.map((item) => (
          <AppPressable key={`${item.provider}:${item.external_session_id}`} onPress={() => void openSession(item)} style={[styles.card, sameCodingSession(selected?.session, item) && styles.selectedCard]}>
            <View style={styles.cardHeader}>
              <Text style={styles.provider}>{item.provider.toUpperCase()}</Text>
              <Text style={[styles.state, item.state === "running" && styles.running]}>{stateLabel(item.state)}</Text>
            </View>
            <Text style={styles.title} numberOfLines={1}>{item.title}</Text>
            <Text style={styles.meta} numberOfLines={1}>{item.directory || "未提供工作目录"}</Text>
            {item.latest_assistant_message ? <Text style={styles.preview} numberOfLines={2}>{item.latest_assistant_message}</Text> : null}
            {working === item.external_session_id ? <ActivityIndicator color={colors.accent} size="small" /> : null}
          </AppPressable>
        ))}
      </View>
      {selected ? (
        <View style={styles.detail}>
          <View style={styles.detailHeader}>
            <Text style={styles.detailTitle}>{selected.session.title}</Text>
            <AppPressable onPress={closeSession}><AppIcon name="x" color={colors.muted} size={22} /></AppPressable>
          </View>
          <Text style={styles.meta}>{selected.session.provider} · {stateLabel(selected.session.state)}{selected.session.read_only ? " · 其他客户端正在运行" : ""}</Text>
          <View style={styles.messages}>
            {selected.messages.slice(-12).map((message) => (
              <View key={message.message_id} style={styles.message}>
                <Text style={styles.messageRole}>{message.role === "assistant" ? "助手" : message.role === "user" ? "你" : message.role}</Text>
                <Text style={styles.messageText}>{message.text || "（无文本内容）"}</Text>
              </View>
            ))}
          </View>
          <View style={styles.actionRow}>
            <AppPressable disabled={working === "attach"} onPress={() => void attach()} style={styles.secondaryButton}>
              <Text style={styles.secondaryText}>绑定当前小诺会话</Text>
            </AppPressable>
            {canInterruptCodingSession(selected.session) ? (
              <AppPressable disabled={working === "interrupt"} onPress={() => void interrupt()} style={styles.secondaryButton}>
                <Text style={styles.secondaryText}>请求中断</Text>
              </AppPressable>
            ) : null}
          </View>
          <TextInput
            multiline
            editable={!selected.session.read_only && working !== "prompt"}
            onChangeText={setText}
            placeholder={selected.session.read_only ? "桌面端运行中，暂时只能查看" : "给这个编码会话发送下一条指令"}
            placeholderTextColor={colors.muted}
            style={styles.input}
            value={text}
          />
          <AppPressable disabled={selected.session.read_only || !text.trim() || working === "prompt"} onPress={() => void continueSession()} style={[styles.primaryButton, (selected.session.read_only || !text.trim()) && styles.disabled]}>
            {working === "prompt" ? <ActivityIndicator color={colors.onAccent} /> : <Text style={styles.primaryText}>继续会话</Text>}
          </AppPressable>
        </View>
      ) : null}
      <AppPressable onPress={() => router.back()} style={styles.back}><Text style={styles.backText}>返回</Text></AppPressable>
      <Text style={styles.context}>{params.nodeId ? `Node: ${params.nodeId}` : ""}</Text>
    </ScrollView>
  );
}

function stateLabel(state: string): string {
  if (state === "running") return "运行中 · 只读";
  if (state === "interrupted") return "已中断";
  if (state === "failed") return "失败";
  if (state === "idle") return "空闲，可继续";
  return "未知";
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
  selectedCard: { borderColor: colors.accent, borderWidth: 2 },
  cardHeader: { flexDirection: "row", justifyContent: "space-between" },
  provider: { color: colors.accent, fontWeight: "800", fontSize: 12 },
  state: { color: colors.muted, fontSize: 12 },
  running: { color: colors.warning },
  title: { color: colors.ink, ...typography.subheading },
  meta: { color: colors.muted, fontSize: 12 },
  preview: { color: colors.ink, lineHeight: 20 },
  empty: { color: colors.muted, lineHeight: 22 },
  error: { color: colors.danger, lineHeight: 20 },
  detail: { backgroundColor: colors.surface, borderRadius: radii.large, padding: spacing.large, gap: spacing.medium, ...shadows.card },
  detailHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  detailTitle: { color: colors.ink, ...typography.subheading, flex: 1 },
  messages: { gap: spacing.small, maxHeight: 360 },
  message: { backgroundColor: colors.background, borderRadius: radii.medium, padding: spacing.medium, gap: spacing.xsmall },
  messageRole: { color: colors.accent, fontWeight: "800", fontSize: 12 },
  messageText: { color: colors.ink, lineHeight: 20 },
  actionRow: { flexDirection: "row", flexWrap: "wrap", gap: spacing.small },
  secondaryButton: { borderColor: colors.line, borderWidth: 1, borderRadius: radii.medium, paddingHorizontal: spacing.medium, paddingVertical: spacing.small },
  secondaryText: { color: colors.accent, fontWeight: "700" },
  input: { minHeight: 90, maxHeight: 180, color: colors.ink, borderColor: colors.line, borderWidth: 1, borderRadius: radii.medium, padding: spacing.medium, textAlignVertical: "top" },
  primaryButton: { minHeight: 46, alignItems: "center", justifyContent: "center", borderRadius: radii.medium, backgroundColor: colors.accent, padding: spacing.medium },
  primaryText: { color: colors.onAccent, fontWeight: "800" },
  disabled: { opacity: 0.5 },
  back: { alignItems: "center", paddingVertical: spacing.medium },
  backText: { color: colors.accent, fontWeight: "700" },
  context: { color: colors.muted, fontSize: 11, textAlign: "center" },
});
