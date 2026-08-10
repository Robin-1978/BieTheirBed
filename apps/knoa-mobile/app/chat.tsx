import * as DocumentPicker from "expo-document-picker";
import {
  RecordingPresets,
  requestRecordingPermissionsAsync,
  setAudioModeAsync,
  useAudioRecorder,
  useAudioRecorderState,
} from "expo-audio";
import { router, useLocalSearchParams } from "expo-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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

import type { ApprovalRequest, ArtifactInput, TaskEvent, TaskSnapshot, TaskState } from "@/api/models";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";

type PendingAttachment = {
  uri: string;
  name: string;
  mediaType: string;
};

const TERMINAL_STATES = new Set<TaskState>(["completed", "failed", "cancelled"]);

export default function ChatScreen() {
  const gateway = useGateway();
  const params = useLocalSearchParams<{ capturedUri?: string; capturedName?: string }>();
  const list = useRef<FlatList<TaskSnapshot>>(null);
  const [turns, setTurns] = useState<TaskSnapshot[]>([]);
  const [events, setEvents] = useState<Record<string, TaskEvent[]>>({});
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const [sending, setSending] = useState(false);
  const [startingTopic, setStartingTopic] = useState(false);
  const [resolving, setResolving] = useState("");
  const [transcribing, setTranscribing] = useState(false);
  const recorder = useAudioRecorder(RecordingPresets.HIGH_QUALITY);
  const recording = useAudioRecorderState(recorder, 250);

  const refresh = useCallback(async () => {
    if (!gateway.client || !gateway.sessionHandle) return;
    const result = await gateway.client.listTasks({
      sessionHandle: gateway.sessionHandle,
      kind: "chat",
      limit: 50,
    });
    const interactive = result.tasks
      .reverse();
    setTurns(interactive);
    const active = interactive.filter((task) => !TERMINAL_STATES.has(task.state));
    const timelines = await Promise.all(
      active.map(async (task) => [task.task_id, await gateway.client!.taskEvents(task.task_id)] as const),
    );
    if (timelines.length) {
      setEvents((current) => ({ ...current, ...Object.fromEntries(timelines) }));
    }
  }, [gateway.client, gateway.sessionHandle]);

  useEffect(() => {
    setTurns([]);
    setEvents({});
    void refresh();
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

  useEffect(() => {
    const feed = gateway.latestEvent;
    if (!feed) return;
    const event = feed.event;
    setEvents((current) => {
      const timeline = current[event.task_id] ?? [];
      if (timeline.some((item) => item.event_seq === event.event_seq)) return current;
      return { ...current, [event.task_id]: [...timeline, event] };
    });
    setTurns((current) => current.map((turn) => {
      if (turn.task_id !== event.task_id) return turn;
      const state = eventState(event) ?? turn.state;
      return { ...turn, state, phase: String(event.payload.phase ?? turn.phase) };
    }));
    if (isTerminalEvent(event) && gateway.client) {
      void gateway.client.getTask(event.task_id).then((snapshot) => {
        setTurns((current) => current.map((turn) => turn.task_id === snapshot.task_id ? snapshot : turn));
      });
    }
  }, [gateway.client, gateway.latestEvent]);

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
      const accepted = await gateway.client.createTask({
        sessionHandle: gateway.sessionHandle,
        text: message,
        attachments: uploaded,
      });
      const now = Date.now() / 1000;
      setTurns((current) => [...current, {
        task_id: accepted.task_id,
        session_handle: gateway.sessionHandle,
        client_request_id: "interactive",
        origin: "chat",
        parent_task_id: "",
        goal: message,
        attachments: uploaded,
        tools_enabled: true,
        priority: 0,
        state: accepted.state,
        phase: "",
        attempt_count: 0,
        cancel_requested: false,
        final_summary: "",
        failure_code: "",
        created_at: now,
        updated_at: now,
        started_at: null,
        finished_at: null,
        next_event_seq: 1,
      }]);
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

  async function resolve(approval: ApprovalRequest, approved: boolean) {
    if (!gateway.client || resolving) return;
    setResolving(approval.approvalId);
    try {
      await gateway.client.resolveApproval(approval.approvalId, approved);
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
        keyExtractor={(item) => item.task_id}
        contentContainerStyle={styles.messages}
        onContentSizeChange={() => list.current?.scrollToEnd({ animated: true })}
        ListEmptyComponent={<Text style={styles.empty}>你好，我是小诺。</Text>}
        renderItem={({ item }) => (
          <ChatTurn
            task={item}
            events={events[item.task_id] ?? []}
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
  task,
  events,
  resolving,
  onResolve,
}: {
  task: TaskSnapshot;
  events: TaskEvent[];
  resolving: boolean;
  onResolve(approval: ApprovalRequest, approved: boolean): void;
}) {
  const response = useMemo(() => responseContent(task, events), [events, task]);
  const approval = useMemo(() => pendingApproval(events), [events]);
  const activity = activityText(task, events);
  return (
    <View style={styles.turn}>
      <View style={styles.userBubble}>
        <Text style={styles.userText}>{task.goal}</Text>
        {task.attachments.length ? <Text style={styles.userMeta}>附件 {task.attachments.length}</Text> : null}
      </View>
      <View style={styles.assistantBubble}>
        {response ? (
          <Markdown
            value={response}
            flatListProps={{ scrollEnabled: false, style: styles.markdownList }}
          />
        ) : (
          <View style={styles.activityRow}>
            {!TERMINAL_STATES.has(task.state) ? <ActivityIndicator color={colors.accent} size="small" /> : null}
            <Text style={styles.activity}>{activity}</Text>
          </View>
        )}
        {approval ? (
          <View style={styles.approval}>
            <Text style={styles.approvalReason}>{approval.reason || approval.toolName}</Text>
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

function eventState(event: TaskEvent): TaskState | null {
  const state = event.payload.state;
  if (typeof state === "string" && ["queued", "running", "waiting_approval", "paused", "completed", "failed", "cancelled"].includes(state)) {
    return state as TaskState;
  }
  if (event.event_type === "completed" || event.event_type === "failed" || event.event_type === "cancelled") {
    return event.event_type;
  }
  return null;
}

function isTerminalEvent(event: TaskEvent): boolean {
  return event.event_type === "completed" || event.event_type === "failed" || event.event_type === "cancelled";
}

function responseContent(task: TaskSnapshot, events: TaskEvent[]): string {
  if (task.final_summary) return task.final_summary;
  const final = [...events].reverse().find((event) => event.event_type === "final_output");
  if (final) return String(final.payload.content ?? "");
  const contentEvents = events.filter((event) => event.event_type === "content_delta");
  const latestIteration = Math.max(0, ...contentEvents.map((event) => Number(event.payload.iteration ?? 0)));
  return contentEvents
    .filter((event) => Number(event.payload.iteration ?? 0) === latestIteration)
    .map((event) => String(event.payload.content ?? ""))
    .join("");
}

function activityText(task: TaskSnapshot, events: TaskEvent[]): string {
  if (task.state === "failed") return "这次没有完成";
  if (task.state === "cancelled") return "已停止";
  if (task.state === "paused") return "已暂停";
  if (task.state === "waiting_approval") return "等你确认";
  const tool = [...events].reverse().find((event) => event.event_type === "tool_call");
  if (tool) return `正在使用 ${String(tool.payload.tool_name ?? "工具")}`;
  return task.state === "queued" ? "马上开始" : "正在思考…";
}

function pendingApproval(events: TaskEvent[]): ApprovalRequest | null {
  let pending: ApprovalRequest | null = null;
  for (const event of events) {
    if (event.event_type === "approval_requested") {
      pending = {
        approvalId: String(event.payload.approval_id ?? ""),
        taskId: event.task_id,
        toolName: String(event.payload.tool_name ?? ""),
        reason: String(event.payload.reason ?? ""),
      };
    } else if (event.event_type === "approval_resolved") {
      pending = null;
    }
  }
  return pending?.approvalId ? pending : null;
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
