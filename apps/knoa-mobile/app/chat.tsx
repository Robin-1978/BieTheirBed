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
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import Markdown from "react-native-marked";

import { subscribeChatTurn, type ChatTurnSubscription } from "@/api/chatTurns";
import type { ArtifactInput, ChatApproval, ChatTurnSnapshot } from "@/api/models";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";

type PendingAttachment = {
  uri: string;
  name: string;
  mediaType: string;
};

const TERMINAL_STATES = new Set<ChatTurnSnapshot["state"]>(["completed", "failed", "cancelled"]);

export default function ChatScreen() {
  const gateway = useGateway();
  const params = useLocalSearchParams<{ capturedUri?: string; capturedName?: string }>();
  const list = useRef<FlatList<ChatTurnSnapshot>>(null);
  const subscriptions = useRef(new Map<string, ChatTurnSubscription>());
  const [turns, setTurns] = useState<ChatTurnSnapshot[]>([]);
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const [sending, setSending] = useState(false);
  const [startingTopic, setStartingTopic] = useState(false);
  const [resolving, setResolving] = useState("");
  const [transcribing, setTranscribing] = useState(false);
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
      onError: () => undefined,
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

  const canSend = Boolean(!sending && gateway.client && (text.trim() || attachments.length));

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
        </View>
      </View>
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
      <View style={styles.composer}>
        <View style={styles.ingress}>
          <Pressable onPress={() => router.push("/capture")}><Text style={styles.iconButton}>相机</Text></Pressable>
          <Pressable onPress={() => void chooseFile()}><Text style={styles.iconButton}>文件</Text></Pressable>
          <Pressable onPress={() => void toggleRecording()}>
            <Text style={[styles.iconButton, recording.isRecording && styles.recording]}>
              {transcribing ? "转写…" : recording.isRecording ? `${Math.round(recording.durationMillis / 1000)}s` : "语音"}
            </Text>
          </Pressable>
        </View>
        <TextInput
          style={styles.input}
          value={text}
          onChangeText={setText}
          placeholder="和小诺说点什么…"
          placeholderTextColor={colors.muted}
          multiline
        />
        <Pressable style={[styles.send, !canSend && styles.sendDisabled]} onPress={() => void send()} disabled={!canSend}>
          {sending ? <ActivityIndicator color="white" size="small" /> : <Text style={styles.sendText}>发送</Text>}
        </Pressable>
      </View>
    </KeyboardAvoidingView>
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
          <Markdown
            value={response}
            flatListProps={{ scrollEnabled: false, style: styles.markdownList }}
          />
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
  composer: { flexDirection: "row", alignItems: "flex-end", gap: 8, padding: 10, backgroundColor: colors.surface, borderTopWidth: 1, borderTopColor: colors.line },
  ingress: { gap: 5, paddingBottom: 3 },
  iconButton: { color: colors.accent, fontSize: 12, fontWeight: "600" },
  recording: { color: colors.danger },
  input: { flex: 1, minHeight: 42, maxHeight: 120, color: colors.ink, backgroundColor: colors.background, borderRadius: 16, paddingHorizontal: 13, paddingVertical: 10, textAlignVertical: "top" },
  send: { minWidth: 58, height: 42, alignItems: "center", justifyContent: "center", backgroundColor: colors.accent, borderRadius: 14 },
  sendDisabled: { opacity: 0.45 },
  sendText: { color: "white", fontWeight: "700" },
});
