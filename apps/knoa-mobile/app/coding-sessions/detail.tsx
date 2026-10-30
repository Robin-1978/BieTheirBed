import { useLocalSearchParams } from "expo-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Platform,
  StyleSheet,
  Text,
  TextInput,
  View,
  type NativeScrollEvent,
  type NativeSyntheticEvent,
} from "react-native";
import { KeyboardAvoidingView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import type { CodingSessionDetail, CodingSessionMessage } from "@/api/models";
import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import {
  canInterruptCodingSession,
  codingMessageRoleLabel,
  codingSessionErrorMessage,
  codingSessionRouteKey,
  codingSessionStateLabel,
  interruptOutcomeMessage,
  readableCodingMessages,
} from "@/state/codingSessionState";
import { useSession } from "@/state/GatewayProvider";
import { createRequestSequence } from "@/state/requestSequence";
import { createSerialPoller } from "@/state/serialPoller";
import { colors, radii, spacing, typography } from "@/theme";

const REFRESH_INTERVAL_MS = 5000;
// Same distance chat.tsx uses to decide that the reader is still at the bottom.
const SCROLL_OFFSET_THRESHOLD = 80;

export default function CodingSessionDetailScreen() {
  const params = useLocalSearchParams<{ provider?: string | string[]; id?: string | string[] }>();
  const key = codingSessionRouteKey(params);
  const provider = key?.provider ?? "";
  const sessionId = key?.external_session_id ?? "";
  const { runAuthenticated, sessionHandle } = useSession();
  const insets = useSafeAreaInsets();
  const [detail, setDetail] = useState<CodingSessionDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [text, setText] = useState("");
  const [working, setWorking] = useState<"" | "attach" | "prompt" | "interrupt">("");
  // `loadError` belongs to the last refresh, `error` to the last user action, so
  // a background refresh never wipes the message of a failed attach/prompt.
  const [loadError, setLoadError] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  // A poll and a refresh after a send can overlap; only the latest response may
  // reach the screen.
  const [loadSequence] = useState(createRequestSequence);
  const listRef = useRef<FlatList<CodingSessionMessage>>(null);
  const followLatest = useRef(true);
  const userDragging = useRef(false);
  const scrollFrame = useRef<number | null>(null);

  const loadDetail = useCallback(async () => {
    if (!provider || !sessionId) return;
    const ticket = loadSequence.begin();
    try {
      const fetched = await runAuthenticated((client) => client.getCodingSession(provider, sessionId));
      if (!loadSequence.isLatest(ticket)) return;
      setDetail(fetched);
      setLoadError("");
    } catch (cause) {
      // The last good detail stays on screen; only the banner changes.
      if (!loadSequence.isLatest(ticket)) return;
      setLoadError(codingSessionErrorMessage(cause, "open", "无法读取会话详情"));
    } finally {
      if (loadSequence.isLatest(ticket)) setLoading(false);
    }
  }, [loadSequence, provider, runAuthenticated, sessionId]);

  useEffect(() => {
    const poller = createSerialPoller({ intervalMs: REFRESH_INTERVAL_MS, run: () => loadDetail() });
    poller.start();
    return () => {
      poller.stop();
      // A response that lands after leaving the screen must not set state.
      loadSequence.cancel();
      if (scrollFrame.current !== null) cancelAnimationFrame(scrollFrame.current);
    };
  }, [loadDetail, loadSequence]);

  const readable = useMemo(() => readableCodingMessages(detail?.messages ?? []), [detail]);

  const scrollToBottom = useCallback(() => {
    if (scrollFrame.current !== null) cancelAnimationFrame(scrollFrame.current);
    scrollFrame.current = requestAnimationFrame(() => {
      listRef.current?.scrollToEnd({ animated: false });
    });
  }, []);

  const handleScroll = useCallback((event: NativeSyntheticEvent<NativeScrollEvent>) => {
    if (!userDragging.current) return;
    const { contentOffset, contentSize, layoutMeasurement } = event.nativeEvent;
    followLatest.current = contentSize.height - (contentOffset.y + layoutMeasurement.height) <= SCROLL_OFFSET_THRESHOLD;
  }, []);

  async function attach() {
    if (!detail) return;
    if (!sessionHandle) {
      setError("需要先在「会话」页打开一个小诺会话，才能把这个编码会话绑定上去。");
      return;
    }
    setWorking("attach");
    setError("");
    setNotice("");
    try {
      await runAuthenticated((client) => client.attachCodingSession(provider, sessionId, sessionHandle));
      setNotice("已绑定到当前小诺会话，现在可以继续这个会话。");
    } catch (cause) {
      setError(codingSessionErrorMessage(cause, "attach", "绑定失败"));
    } finally {
      setWorking("");
    }
  }

  async function send() {
    const body = text.trim();
    if (!detail || !body || detail.session.read_only) return;
    setWorking("prompt");
    setError("");
    setNotice("");
    try {
      await runAuthenticated((client) => client.continueCodingSession(provider, sessionId, body));
      setText("");
      setNotice("已发送。桌面端的回复会自动出现在这里。");
      followLatest.current = true;
      await loadDetail();
    } catch (cause) {
      setError(codingSessionErrorMessage(cause, "prompt", "继续会话失败；如果桌面端正在运行，请等它结束后再试"));
    } finally {
      setWorking("");
    }
  }

  async function interrupt() {
    if (!detail) return;
    setWorking("interrupt");
    setError("");
    setNotice("");
    try {
      const interrupted = await runAuthenticated((client) => client.interruptCodingSession(provider, sessionId));
      await loadDetail();
      const outcome = interruptOutcomeMessage(interrupted);
      if (outcome) setError(outcome);
    } catch (cause) {
      setError(codingSessionErrorMessage(cause, "interrupt", "中断失败"));
    } finally {
      setWorking("");
    }
  }

  if (!key) {
    return (
      <View style={styles.missing}>
        <Text style={styles.empty}>无法打开这个会话：缺少会话标识。请返回列表重新选择。</Text>
      </View>
    );
  }

  const session = detail?.session ?? null;
  const readOnly = session?.read_only ?? false;
  const canSend = Boolean(session) && !readOnly && working === "" && text.trim().length > 0;

  return (
    <KeyboardAvoidingView
      style={styles.screen}
      behavior={Platform.OS === "ios" ? "padding" : "height"}
      keyboardVerticalOffset={insets.top + (Platform.OS === "ios" ? 44 : 56)}
    >
      {session ? (
        <View style={styles.header}>
          <View style={styles.headerTop}>
            <Text style={styles.provider}>{session.provider.toUpperCase()}</Text>
            <Text style={[styles.state, session.state === "running" && styles.running]}>
              {codingSessionStateLabel(session.state)}
            </Text>
          </View>
          <Text style={styles.title} numberOfLines={2}>{session.title}</Text>
          <Text style={styles.meta} numberOfLines={1}>{session.directory || "未提供工作目录"}</Text>
          <View style={styles.actions}>
            <AppPressable
              disabled={working === "attach"}
              onPress={() => void attach()}
              style={styles.secondaryButton}
            >
              <Text style={styles.secondaryText}>绑定当前小诺会话</Text>
            </AppPressable>
            {canInterruptCodingSession(session) ? (
              <AppPressable
                disabled={working === "interrupt"}
                onPress={() => void interrupt()}
                style={styles.secondaryButton}
              >
                <Text style={styles.secondaryText}>请求中断</Text>
              </AppPressable>
            ) : null}
          </View>
          <Text style={styles.hint}>继续这个会话之前，需要先绑定当前小诺会话。</Text>
        </View>
      ) : null}
      {loadError ? <Text style={styles.error}>{loadError}</Text> : null}
      {error ? <Text style={styles.error}>{error}</Text> : null}
      {notice ? <Text style={styles.notice}>{notice}</Text> : null}
      <View style={styles.listArea}>
        {loading && !detail ? <ActivityIndicator color={colors.accent} style={styles.loading} /> : null}
        <FlatList
          ref={listRef}
          contentContainerStyle={styles.messages}
          keyboardShouldPersistTaps="handled"
          data={readable.messages}
          keyExtractor={(item, index) => `${item.message_id}:${index}`}
          onContentSizeChange={() => {
            if (followLatest.current) scrollToBottom();
          }}
          onScrollBeginDrag={() => { userDragging.current = true; }}
          onScroll={handleScroll}
          onScrollEndDrag={() => { userDragging.current = false; }}
          ListHeaderComponent={
            readable.hidden > 0 ? (
              <Text style={styles.hiddenNote}>已隐藏 {readable.hidden} 条没有文字内容的消息</Text>
            ) : null
          }
          ListEmptyComponent={
            detail ? <Text style={styles.empty}>这个会话还没有可显示的文字内容。</Text> : null
          }
          renderItem={({ item }) => (
            <View style={[styles.message, item.role === "user" && styles.userMessage]}>
              <Text style={styles.messageRole}>{codingMessageRoleLabel(item.role)}</Text>
              <Text selectable style={styles.messageText}>{item.text}</Text>
            </View>
          )}
        />
      </View>
      <View style={styles.composer}>
        <TextInput
          multiline
          editable={Boolean(session) && !readOnly && working !== "prompt"}
          onChangeText={setText}
          placeholder={readOnly ? "桌面端运行中，暂时只能查看" : "给这个编码会话发送下一条指令"}
          placeholderTextColor={colors.muted}
          style={styles.input}
          value={text}
        />
        <AppPressable
          accessibilityLabel="发送"
          disabled={!canSend}
          onPress={() => void send()}
          style={styles.sendButton}
        >
          {working === "prompt"
            ? <ActivityIndicator color={colors.onAccent} />
            : <AppIcon name="send" color={colors.onAccent} size={20} />}
        </AppPressable>
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  missing: { flex: 1, padding: spacing.large, backgroundColor: colors.background },
  header: {
    padding: spacing.large,
    gap: spacing.small,
    backgroundColor: colors.surface,
    borderBottomColor: colors.line,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  headerTop: { flexDirection: "row", justifyContent: "space-between" },
  provider: { color: colors.accent, fontWeight: "800", fontSize: 12 },
  state: { color: colors.muted, fontSize: 12 },
  running: { color: colors.warning },
  title: { color: colors.ink, ...typography.subheading },
  meta: { color: colors.muted, fontSize: 12 },
  actions: { flexDirection: "row", flexWrap: "wrap", gap: spacing.small },
  secondaryButton: { borderColor: colors.line, borderWidth: 1, borderRadius: radii.medium, paddingHorizontal: spacing.medium, paddingVertical: spacing.small },
  secondaryText: { color: colors.accent, fontWeight: "700" },
  hint: { color: colors.muted, fontSize: 12 },
  error: { color: colors.danger, lineHeight: 20, paddingHorizontal: spacing.large, paddingTop: spacing.small },
  notice: { color: colors.accent, lineHeight: 20, paddingHorizontal: spacing.large, paddingTop: spacing.small },
  listArea: { flex: 1 },
  loading: { marginTop: spacing.large },
  messages: { padding: spacing.large, gap: spacing.small },
  hiddenNote: { color: colors.muted, fontSize: 12, textAlign: "center", paddingBottom: spacing.small },
  empty: { color: colors.muted, lineHeight: 22, textAlign: "center" },
  message: { backgroundColor: colors.surface, borderRadius: radii.medium, padding: spacing.medium, gap: spacing.xsmall },
  userMessage: { backgroundColor: colors.accentSoft },
  messageRole: { color: colors.accent, fontWeight: "800", fontSize: 12 },
  messageText: { color: colors.ink, lineHeight: 21 },
  composer: {
    flexDirection: "row",
    alignItems: "flex-end",
    gap: spacing.small,
    paddingHorizontal: spacing.large,
    paddingVertical: spacing.small,
    backgroundColor: colors.surface,
    borderTopColor: colors.line,
    borderTopWidth: StyleSheet.hairlineWidth,
  },
  input: {
    flex: 1,
    minHeight: 44,
    maxHeight: 140,
    color: colors.ink,
    fontSize: 15,
    paddingVertical: 10,
    paddingHorizontal: spacing.medium,
    borderColor: colors.line,
    borderWidth: 1,
    borderRadius: radii.medium,
  },
  sendButton: { width: 44, height: 44, borderRadius: 22, alignItems: "center", justifyContent: "center", backgroundColor: colors.accent },
});
