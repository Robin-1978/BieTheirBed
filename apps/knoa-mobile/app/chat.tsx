import * as DocumentPicker from "expo-document-picker";
import {
  RecordingPresets,
  requestRecordingPermissionsAsync,
  setAudioModeAsync,
  useAudioRecorder,
  useAudioRecorderState,
} from "expo-audio";
import { router, useLocalSearchParams } from "expo-router";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Image,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import Svg, { Circle, Line, Path, Rect } from "react-native-svg";

import { subscribeChatTurn, type ChatTurnSubscription } from "@/api/chatTurns";
import type { AndroidRelease, ArtifactInput, ChatApproval, ChatTurnSnapshot } from "@/api/models";
import { AppMarkdown } from "@/components/AppMarkdown";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";
import { isAndroidUpdateAvailable } from "@/update/androidUpdater";

type PendingAttachment = {
  uri: string;
  name: string;
  mediaType: string;
};

type InputMode = "text" | "voice";

const TERMINAL_STATES = new Set<ChatTurnSnapshot["state"]>(["completed", "failed", "cancelled"]);

export default function ChatScreen() {
  const gateway = useGateway();
  const params = useLocalSearchParams<{ capturedUri?: string; capturedName?: string }>();
  const list = useRef<FlatList<ChatTurnSnapshot>>(null);
  const subscriptions = useRef(new Map<string, ChatTurnSubscription>());
  const [turns, setTurns] = useState<ChatTurnSnapshot[]>([]);
  const [text, setText] = useState("");
  const [inputMode, setInputMode] = useState<InputMode>("text");
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const [sending, setSending] = useState(false);
  const [startingTopic, setStartingTopic] = useState(false);
  const [resolving, setResolving] = useState("");
  const [transcribing, setTranscribing] = useState(false);
  const [actionsOpen, setActionsOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [availableUpdate, setAvailableUpdate] = useState<AndroidRelease | null>(null);
  const recorder = useAudioRecorder(RecordingPresets.HIGH_QUALITY);
  const recording = useAudioRecorderState(recorder, 250);

  const watchTurn = useCallback((turnId: string) => {
    if (!gateway.gatewayUrl || !gateway.sessionToken || subscriptions.current.has(turnId)) return;
    const subscription = subscribeChatTurn({
      gatewayUrl: gateway.gatewayUrl,
      token: gateway.sessionToken,
      turnId,
      onSnapshot: (snapshot) => {
        setTurns((current) => {
          const index = current.findIndex((turn) => turn.turn_id === snapshot.turn_id);
          if (index < 0) return [...current, snapshot];
          const next = [...current];
          next[index] = snapshot;
          return next;
        });
        if (TERMINAL_STATES.has(snapshot.state)) {
          subscriptions.current.get(snapshot.turn_id)?.close();
          subscriptions.current.delete(snapshot.turn_id);
        }
      },
      onError: () => setMessage("回复连接中断，请稍后重试"),
    });
    subscriptions.current.set(turnId, subscription);
  }, [gateway.gatewayUrl, gateway.sessionToken]);

  const refresh = useCallback(async () => {
    if (!gateway.client || !gateway.sessionHandle) return;
    const history = await gateway.client.listChatTurns(gateway.sessionHandle, 100);
    setTurns(history);
    for (const turn of history) {
      if (!TERMINAL_STATES.has(turn.state)) watchTurn(turn.turn_id);
    }
  }, [gateway.client, gateway.sessionHandle, watchTurn]);

  useEffect(() => {
    setTurns([]);
    for (const subscription of subscriptions.current.values()) subscription.close();
    subscriptions.current.clear();
    void refresh();
    return () => {
      for (const subscription of subscriptions.current.values()) subscription.close();
      subscriptions.current.clear();
    };
  }, [gateway.sessionHandle, refresh]);

  useEffect(() => {
    if (!gateway.client) return;
    void gateway.client.latestAndroidRelease()
      .then((release) => setAvailableUpdate(isAndroidUpdateAvailable(release) ? release : null))
      .catch(() => undefined);
  }, [gateway.client]);

  useEffect(() => {
    const uri = params.capturedUri?.trim();
    if (!uri) return;
    setAttachments((current) => current.some((item) => item.uri === uri)
      ? current
      : [...current, {
          uri,
          name: params.capturedName?.trim() || `camera-${Date.now()}.jpg`,
          mediaType: "image/jpeg",
        }]);
    router.setParams({ capturedUri: "", capturedName: "" });
  }, [params.capturedName, params.capturedUri]);

  const hasComposerContent = Boolean(text.trim() || attachments.length);
  const canSend = Boolean(!sending && gateway.client && hasComposerContent);

  async function chooseFile() {
    const picked = await DocumentPicker.getDocumentAsync({
      multiple: true,
      copyToCacheDirectory: true,
    });
    if (picked.canceled) return;
    setAttachments((current) => [
      ...current,
      ...picked.assets.slice(0, 8 - current.length).map((asset) => ({
        uri: asset.uri,
        name: asset.name,
        mediaType: asset.mimeType ?? "application/octet-stream",
      })),
    ].slice(0, 8));
  }

  async function send() {
    if (!gateway.client || !canSend) return;
    const message = text.trim() || "请看一下这些内容";
    const pending = attachments;
    setSending(true);
    setMessage("");
    try {
      const uploaded = await Promise.all(pending.map(async (item): Promise<ArtifactInput> => {
        const response = await fetch(item.uri);
        return gateway.client!.uploadArtifact({
          sessionHandle: gateway.sessionHandle,
          bytes: await response.arrayBuffer(),
          mediaType: item.mediaType,
          name: item.name,
          caption: item.name,
        });
      }));
      const accepted = await gateway.client.createChatTurn({
        sessionHandle: gateway.sessionHandle,
        text: message,
        attachments: uploaded,
      });
      setTurns((current) => [...current, accepted]);
      watchTurn(accepted.turn_id);
      setText("");
      setAttachments([]);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "消息没有发出去，请重试");
    } finally {
      setSending(false);
    }
  }

  async function toggleRecording() {
    if (!gateway.client || transcribing) return;
    if (recording.isRecording) {
      await recorder.stop();
      const uri = recorder.uri;
      if (!uri) return;
      setTranscribing(true);
      try {
        await setAudioModeAsync({ allowsRecording: false });
        const response = await fetch(uri);
        const extension = uri.toLowerCase().endsWith(".webm") ? "webm" : "m4a";
        const artifact = await gateway.client.uploadArtifact({
          sessionHandle: gateway.sessionHandle,
          bytes: await response.arrayBuffer(),
          mediaType: extension === "webm" ? "audio/webm" : "audio/mp4",
          name: `voice-${Date.now()}.${extension}`,
          caption: "语音输入",
        });
        const transcript = await gateway.client.transcribeArtifact(
          gateway.sessionHandle,
          artifact.artifact_id,
        );
        setText((current) => current ? `${current}\n${transcript}` : transcript);
      } finally {
        setTranscribing(false);
      }
      return;
    }
    const permission = await requestRecordingPermissionsAsync();
    if (!permission.granted) return;
    await setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true });
    await recorder.prepareToRecordAsync();
    recorder.record();
  }

  async function resolve(approval: ChatApproval, approved: boolean) {
    if (!gateway.client || resolving) return;
    setResolving(approval.approval_id);
    try {
      await gateway.client.resolveChatApproval(approval.approval_id, approved);
    } finally {
      setResolving("");
    }
  }

  async function startNewTopic() {
    if (startingTopic || sending) return;
    setStartingTopic(true);
    try {
      await gateway.newConversation();
      setText("");
      setAttachments([]);
    } finally {
      setStartingTopic(false);
    }
  }

  return (
    <KeyboardAvoidingView
      style={styles.screen}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
      keyboardVerticalOffset={88}
    >
      <View style={styles.topbar}>
        <Text style={styles.subtitle}>随时告诉我你想做什么</Text>
        <View style={styles.topActions}>
          <Pressable onPress={() => void startNewTopic()} disabled={startingTopic || sending}>
            <Text style={styles.link}>{startingTopic ? "创建中…" : "新话题"}</Text>
          </Pressable>
          <Pressable onPress={() => router.push("/tasks")}><Text style={styles.link}>任务</Text></Pressable>
          <Pressable onPress={() => router.push("/capabilities")}><Text style={styles.link}>状态</Text></Pressable>
          <Pressable onPress={() => router.push("/update")}><Text style={styles.link}>版本</Text></Pressable>
        </View>
      </View>
      {gateway.status !== "ready" ? (
        <View style={styles.connectionBanner}>
          <View style={styles.bannerCopy}>
            <Text style={styles.connectionTitle}>暂时没有连接到小诺</Text>
            <Text style={styles.connectionDetail}>{gateway.error || "正在重新建立安全连接"}</Text>
          </View>
          <Pressable onPress={() => void gateway.reconnect()} style={styles.bannerButton}>
            <Text style={styles.bannerButtonText}>重连</Text>
          </Pressable>
        </View>
      ) : null}
      {availableUpdate ? (
        <Pressable style={styles.updateBanner} onPress={() => router.push("/update")}>
          <View style={styles.bannerCopy}>
            <Text style={styles.updateTitle}>小诺 {availableUpdate.version_name} 可以更新</Text>
            <Text style={styles.updateDetail}>查看版本说明并下载安装</Text>
          </View>
          <Text style={styles.updateLink}>更新</Text>
        </Pressable>
      ) : null}
      <FlatList
        ref={list}
        data={turns}
        keyExtractor={(item) => item.turn_id}
        contentContainerStyle={styles.messages}
        onContentSizeChange={() => list.current?.scrollToEnd({ animated: true })}
        ListEmptyComponent={<Text style={styles.empty}>你好，我是小诺。</Text>}
        renderItem={({ item }) => (
          <ChatTurn
            turn={item}
            resolving={Boolean(resolving)}
            onResolve={resolve}
          />
        )}
      />
      {attachments.length ? (
        <View style={styles.attachmentStrip}>
          {attachments.map((item, index) => (
            <Pressable
              key={`${item.uri}:${index}`}
              style={styles.attachment}
              onPress={() => setAttachments((current) => current.filter((_, itemIndex) => itemIndex !== index))}
            >
              {item.mediaType.startsWith("image/") ? <Image source={{ uri: item.uri }} style={styles.thumbnail} /> : null}
              <Text style={styles.attachmentName} numberOfLines={1}>{item.name}</Text>
              <Text style={styles.remove}>×</Text>
            </Pressable>
          ))}
        </View>
      ) : null}
      {message ? (
        <Pressable onPress={() => setMessage("")} style={styles.errorBanner}>
          <Text style={styles.errorText}>{message}</Text>
        </Pressable>
      ) : null}
      <View style={styles.composer}>
        <Pressable
          accessibilityLabel="添加照片或文件"
          onPress={() => setActionsOpen(true)}
          style={styles.roundAction}
        >
          <LineIcon name="plus" color={colors.accent} />
        </Pressable>
        <View style={styles.inputShell}>
          <Pressable
            accessibilityLabel={inputMode === "text" ? "切换到语音输入" : "切换到文字输入"}
            disabled={recording.isRecording || transcribing}
            onPress={() => setInputMode((current) => current === "text" ? "voice" : "text")}
            style={styles.inputMode}
          >
            <LineIcon name={inputMode === "text" ? "mic" : "keyboard"} color={colors.muted} size={20} />
          </Pressable>
          <TextInput
            editable={inputMode === "text"}
            style={styles.input}
            value={text}
            onChangeText={setText}
            placeholder={inputMode === "text" ? "和小诺说点什么…" : "语音转写会出现在这里"}
            placeholderTextColor={colors.muted}
            multiline
          />
        </View>
        <Pressable
          accessibilityLabel={inputMode === "voice" ? recording.isRecording ? "停止录音" : "开始录音" : "发送"}
          onPress={() => {
            if (inputMode === "voice") void toggleRecording();
            else void send();
          }}
          disabled={inputMode === "text"
            ? !canSend
            : sending || transcribing || (!gateway.client && !recording.isRecording)}
          style={[
            styles.primaryAction,
            recording.isRecording && styles.primaryRecording,
            (inputMode === "text"
              ? !canSend
              : sending || transcribing || (!gateway.client && !recording.isRecording)) && styles.sendDisabled,
          ]}
        >
          {sending || transcribing ? (
            <ActivityIndicator color="white" size="small" />
          ) : recording.isRecording ? (
            <View style={styles.recordingContent}>
              <LineIcon name="stop" color="white" size={17} />
              <Text style={styles.recordingTime}>{Math.round(recording.durationMillis / 1000)}s</Text>
            </View>
          ) : inputMode === "text" ? (
            <Text style={styles.sendText}>发送</Text>
          ) : (
            <LineIcon name="mic" color="white" />
          )}
        </Pressable>
      </View>
      <Modal
        animationType="fade"
        onRequestClose={() => setActionsOpen(false)}
        transparent
        visible={actionsOpen}
      >
        <View style={styles.modalRoot}>
          <Pressable style={styles.backdrop} onPress={() => setActionsOpen(false)} />
          <View style={styles.actionSheet}>
            <View style={styles.sheetHandle} />
            <Text style={styles.sheetTitle}>添加内容</Text>
            <View style={styles.sheetActions}>
              <MediaAction
                icon="camera"
                label="拍照"
                onPress={() => {
                  setActionsOpen(false);
                  router.push("/capture");
                }}
              />
              <MediaAction
                icon="file"
                label="文件"
                onPress={() => {
                  setActionsOpen(false);
                  void chooseFile();
                }}
              />
            </View>
          </View>
        </View>
      </Modal>
    </KeyboardAvoidingView>
  );
}

function MediaAction({ icon, label, onPress }: { icon: "camera" | "file"; label: string; onPress(): void }) {
  return (
    <Pressable onPress={onPress} style={styles.mediaAction}>
      <View style={styles.mediaIcon}><LineIcon name={icon} color={colors.accent} size={28} /></View>
      <Text style={styles.mediaLabel}>{label}</Text>
    </Pressable>
  );
}

function LineIcon({
  name,
  color,
  size = 22,
}: {
  name: "plus" | "camera" | "file" | "mic" | "keyboard" | "stop";
  color: string;
  size?: number;
}) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      {name === "plus" ? (
        <><Line x1="12" y1="5" x2="12" y2="19" stroke={color} strokeWidth="2" strokeLinecap="round" /><Line x1="5" y1="12" x2="19" y2="12" stroke={color} strokeWidth="2" strokeLinecap="round" /></>
      ) : null}
      {name === "camera" ? (
        <><Path d="M4 7.5h3l1.4-2h7.2l1.4 2h3v11H4z" stroke={color} strokeWidth="1.8" strokeLinejoin="round" /><Circle cx="12" cy="13" r="3.2" stroke={color} strokeWidth="1.8" /></>
      ) : null}
      {name === "file" ? (
        <><Path d="M7 3.5h6l4 4V20.5H7z" stroke={color} strokeWidth="1.8" strokeLinejoin="round" /><Path d="M13 3.5v4h4" stroke={color} strokeWidth="1.8" strokeLinejoin="round" /><Line x1="9.5" y1="12" x2="14.5" y2="12" stroke={color} strokeWidth="1.6" strokeLinecap="round" /><Line x1="9.5" y1="15.5" x2="14.5" y2="15.5" stroke={color} strokeWidth="1.6" strokeLinecap="round" /></>
      ) : null}
      {name === "mic" ? (
        <><Rect x="9" y="3" width="6" height="11" rx="3" stroke={color} strokeWidth="1.8" /><Path d="M6.5 11.5a5.5 5.5 0 0 0 11 0M12 17v4M9 21h6" stroke={color} strokeWidth="1.8" strokeLinecap="round" /></>
      ) : null}
      {name === "keyboard" ? (
        <><Rect x="3" y="6" width="18" height="12" rx="2.5" stroke={color} strokeWidth="1.8" /><Path d="M6.5 10h.01M10 10h.01M14 10h.01M17.5 10h.01M6.5 13.5h.01M10 13.5h.01M14 13.5h.01M17.5 13.5h.01M8 16h8" stroke={color} strokeWidth="2" strokeLinecap="round" /></>
      ) : null}
      {name === "stop" ? <Rect x="6" y="6" width="12" height="12" rx="2.5" fill={color} /> : null}
    </Svg>
  );
}

function ChatTurn({
  turn,
  resolving,
  onResolve,
}: {
  turn: ChatTurnSnapshot;
  resolving: boolean;
  onResolve(approval: ChatApproval, approved: boolean): void;
}) {
  const response = turn.final_output || turn.content;
  const approval = turn.approvals.find((item) => item.state === "pending") ?? null;
  const activity = activityText(turn);
  return (
    <View style={styles.turn}>
      <View style={styles.userBubble}>
        <Text style={styles.userText}>{turn.user_input}</Text>
        {turn.attachments.length ? <Text style={styles.userMeta}>附件 {turn.attachments.length}</Text> : null}
      </View>
      <View style={styles.assistantBubble}>
        {response ? (
          <AppMarkdown value={response} style={styles.markdownList} />
        ) : (
          <View style={styles.activityRow}>
            {!TERMINAL_STATES.has(turn.state) ? <ActivityIndicator color={colors.accent} size="small" /> : null}
            <Text style={styles.activity}>{activity}</Text>
          </View>
        )}
        {approval ? (
          <View style={styles.approval}>
            <Text style={styles.approvalReason}>{approval.reason || approval.tool_name}</Text>
            <View style={styles.approvalActions}>
              <Pressable style={styles.deny} disabled={resolving} onPress={() => onResolve(approval, false)}>
                <Text style={styles.denyText}>取消</Text>
              </Pressable>
              <Pressable style={styles.approve} disabled={resolving} onPress={() => onResolve(approval, true)}>
                <Text style={styles.approveText}>确认</Text>
              </Pressable>
            </View>
          </View>
        ) : null}
      </View>
    </View>
  );
}

function activityText(turn: ChatTurnSnapshot): string {
  if (turn.state === "failed") return "这次没有完成";
  if (turn.state === "cancelled") return "已停止";
  if (turn.state === "waiting_approval") return "等你确认";
  const tool = [...turn.timeline].reverse().find((entry) => entry.kind === "tool_call");
  if (tool) return `正在使用 ${tool.tool_name || "工具"}`;
  return "正在思考…";
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  topbar: { paddingHorizontal: 16, paddingVertical: 10, flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  subtitle: { color: colors.muted, fontSize: 13 },
  topActions: { flexDirection: "row", gap: 16 },
  link: { color: colors.accent, fontWeight: "600" },
  messages: { padding: 16, paddingBottom: 24, gap: 18, flexGrow: 1 },
  empty: { color: colors.muted, textAlign: "center", marginTop: 80, fontSize: 17 },
  turn: { gap: 8 },
  userBubble: { alignSelf: "flex-end", maxWidth: "84%", backgroundColor: colors.accent, borderRadius: 18, borderBottomRightRadius: 5, paddingHorizontal: 15, paddingVertical: 11 },
  userText: { color: "white", fontSize: 16, lineHeight: 23 },
  userMeta: { color: "#DCEAE4", fontSize: 12, marginTop: 5 },
  assistantBubble: { alignSelf: "stretch", width: "100%", backgroundColor: colors.surface, borderRadius: 18, borderBottomLeftRadius: 5, padding: 15, borderWidth: 1, borderColor: colors.line },
  markdownList: { width: "100%", alignSelf: "stretch" },
  activityRow: { flexDirection: "row", alignItems: "center", gap: 9 },
  activity: { color: colors.muted },
  approval: { marginTop: 12, borderTopWidth: 1, borderTopColor: colors.line, paddingTop: 12, gap: 10 },
  approvalReason: { color: colors.ink, lineHeight: 21 },
  approvalActions: { flexDirection: "row", gap: 10 },
  deny: { flex: 1, alignItems: "center", borderWidth: 1, borderColor: colors.line, borderRadius: 11, padding: 10 },
  denyText: { color: colors.ink, fontWeight: "600" },
  approve: { flex: 1, alignItems: "center", backgroundColor: colors.accent, borderRadius: 11, padding: 10 },
  approveText: { color: "white", fontWeight: "700" },
  attachmentStrip: { paddingHorizontal: 12, paddingVertical: 8, flexDirection: "row", gap: 8, borderTopWidth: 1, borderTopColor: colors.line },
  attachment: { maxWidth: 150, flexDirection: "row", alignItems: "center", gap: 7, backgroundColor: colors.surface, borderRadius: 10, padding: 6, borderWidth: 1, borderColor: colors.line },
  thumbnail: { width: 34, height: 34, borderRadius: 6 },
  attachmentName: { color: colors.ink, fontSize: 12, flexShrink: 1 },
  remove: { color: colors.muted, fontSize: 18 },
  connectionBanner: { marginHorizontal: 16, marginBottom: 8, padding: 13, borderRadius: 14, backgroundColor: "#FFF3ED", flexDirection: "row", alignItems: "center", gap: 12 },
  bannerCopy: { flex: 1 },
  connectionTitle: { color: colors.ink, fontWeight: "700" },
  connectionDetail: { color: colors.muted, fontSize: 12, marginTop: 3 },
  bannerButton: { paddingHorizontal: 13, paddingVertical: 8, borderRadius: 11, backgroundColor: colors.surface },
  bannerButtonText: { color: colors.accent, fontWeight: "700" },
  updateBanner: { marginHorizontal: 16, marginBottom: 8, padding: 13, borderRadius: 14, backgroundColor: colors.accentSoft, flexDirection: "row", alignItems: "center", gap: 12 },
  updateTitle: { color: colors.ink, fontWeight: "700" },
  updateDetail: { color: colors.muted, fontSize: 12, marginTop: 3 },
  updateLink: { color: colors.accent, fontWeight: "700" },
  errorBanner: { paddingHorizontal: 16, paddingVertical: 9, backgroundColor: "#FFF3ED", borderTopWidth: 1, borderTopColor: "#F3D7CB" },
  errorText: { color: colors.danger, textAlign: "center", fontSize: 13 },
  composer: { flexDirection: "row", alignItems: "flex-end", gap: 8, padding: 10, backgroundColor: colors.surface, borderTopWidth: 1, borderTopColor: colors.line },
  roundAction: { width: 42, height: 42, borderRadius: 21, alignItems: "center", justifyContent: "center", backgroundColor: colors.background, borderWidth: 1, borderColor: colors.line },
  inputShell: { flex: 1, minHeight: 42, maxHeight: 120, flexDirection: "row", alignItems: "flex-end", backgroundColor: colors.background, borderRadius: 16 },
  inputMode: { width: 40, height: 42, alignItems: "center", justifyContent: "center" },
  input: { flex: 1, minHeight: 42, maxHeight: 120, color: colors.ink, paddingRight: 13, paddingVertical: 10, textAlignVertical: "top" },
  primaryAction: { minWidth: 52, height: 42, paddingHorizontal: 12, alignItems: "center", justifyContent: "center", backgroundColor: colors.accent, borderRadius: 14 },
  primaryRecording: { backgroundColor: colors.danger },
  recordingContent: { flexDirection: "row", alignItems: "center", gap: 5 },
  recordingTime: { color: "white", fontSize: 12, fontWeight: "700" },
  sendDisabled: { opacity: 0.45 },
  sendText: { color: "white", fontWeight: "700" },
  modalRoot: { flex: 1, justifyContent: "flex-end" },
  backdrop: { position: "absolute", top: 0, right: 0, bottom: 0, left: 0, backgroundColor: "rgba(25, 31, 29, 0.28)" },
  actionSheet: { backgroundColor: colors.surface, borderTopLeftRadius: 24, borderTopRightRadius: 24, paddingHorizontal: 22, paddingTop: 10, paddingBottom: 34, gap: 18 },
  sheetHandle: { width: 38, height: 4, borderRadius: 2, backgroundColor: colors.line, alignSelf: "center" },
  sheetTitle: { color: colors.ink, fontSize: 17, fontWeight: "700" },
  sheetActions: { flexDirection: "row", gap: 24 },
  mediaAction: { width: 76, alignItems: "center", gap: 8 },
  mediaIcon: { width: 58, height: 58, borderRadius: 18, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft },
  mediaLabel: { color: colors.ink, fontWeight: "600" },
});
