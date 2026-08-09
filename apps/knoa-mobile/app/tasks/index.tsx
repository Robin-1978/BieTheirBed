import * as DocumentPicker from "expo-document-picker";
import {
  RecordingPresets,
  requestRecordingPermissionsAsync,
  setAudioModeAsync,
  useAudioRecorder,
  useAudioRecorderState,
} from "expo-audio";
import { router } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import type { ArtifactInput, TaskSnapshot, TaskState } from "@/api/models";
import { subscribeTaskEvents, type TaskEventSubscription } from "@/api/taskEvents";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";

const filters: Array<{ label: string; value?: TaskState }> = [
  { label: "全部" },
  { label: "进行中", value: "running" },
  { label: "待确认", value: "waiting_approval" },
  { label: "已完成", value: "completed" },
];

export default function TaskListScreen() {
  const gateway = useGateway();
  const [tasks, setTasks] = useState<TaskSnapshot[]>([]);
  const [filter, setFilter] = useState<TaskState | undefined>();
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<ArtifactInput[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const recorder = useAudioRecorder(RecordingPresets.HIGH_QUALITY);
  const recording = useAudioRecorderState(recorder, 250);

  const refresh = useCallback(async () => {
    if (!gateway.client) return;
    setRefreshing(true);
    try {
      const result = await gateway.client.listTasks({ state: filter });
      setTasks(result.tasks);
    } finally {
      setRefreshing(false);
    }
  }, [filter, gateway.client]);

  useEffect(() => {
    void refresh();
  }, [refresh, gateway.latestEvent]);

  useEffect(() => {
    let subscription: TaskEventSubscription | null = null;
    if (gateway.status === "ready") {
      void subscribeTaskEvents({
        gatewayUrl: gateway.gatewayUrl,
        token: gateway.sessionToken,
        onEvent: gateway.publish,
        onError: () => undefined,
      }).then((active) => {
        subscription = active;
      });
    }
    return () => subscription?.close();
  }, [gateway.gatewayUrl, gateway.publish, gateway.sessionToken, gateway.status]);

  async function chooseFile() {
    if (!gateway.client) return;
    const picked = await DocumentPicker.getDocumentAsync({ multiple: true, copyToCacheDirectory: true });
    if (picked.canceled) return;
    const uploaded: ArtifactInput[] = [];
    for (const asset of picked.assets.slice(0, 8 - attachments.length)) {
      const response = await fetch(asset.uri);
      uploaded.push(
        await gateway.client.uploadArtifact({
          sessionHandle: gateway.sessionHandle,
          bytes: await response.arrayBuffer(),
          mediaType: asset.mimeType ?? "application/octet-stream",
          name: asset.name,
          caption: asset.name,
        }),
      );
    }
    setAttachments((current) => [...current, ...uploaded].slice(0, 8));
  }

  async function createTask() {
    if (!gateway.client || (!text.trim() && attachments.length === 0)) return;
    setSubmitting(true);
    try {
      const accepted = await gateway.client.createTask({
        sessionHandle: gateway.sessionHandle,
        text: text.trim(),
        attachments,
      });
      setText("");
      setAttachments([]);
      router.push(`/tasks/${accepted.task_id}`);
    } finally {
      setSubmitting(false);
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
        const mediaType = extension === "webm" ? "audio/webm" : "audio/mp4";
        const artifact = await gateway.client.uploadArtifact({
          sessionHandle: gateway.sessionHandle,
          bytes: await response.arrayBuffer(),
          mediaType,
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

  return (
    <View style={styles.container}>
      <View style={styles.topline}>
        <Text style={styles.heading}>小诺工作台</Text>
        <Pressable onPress={() => router.push("/capabilities")}>
          <Text style={styles.link}>能力与连接</Text>
        </Pressable>
      </View>
      <View style={styles.composer}>
        <TextInput
          style={styles.input}
          value={text}
          onChangeText={setText}
          placeholder="交给小诺一件事…"
          placeholderTextColor={colors.muted}
          multiline
        />
        {attachments.length ? <Text style={styles.attachments}>已附加 {attachments.length} 个文件</Text> : null}
        <View style={styles.actions}>
          <View style={styles.ingress}>
            <Pressable onPress={() => void chooseFile()}><Text style={styles.link}>添加文件</Text></Pressable>
            <Pressable onPress={() => router.push("/capture")}><Text style={styles.link}>拍照</Text></Pressable>
            <Pressable onPress={() => void toggleRecording()}>
              <Text style={[styles.link, recording.isRecording && styles.recording]}>
                {transcribing
                  ? "转写中…"
                  : recording.isRecording
                    ? `停止录音 ${Math.round(recording.durationMillis / 1000)}s`
                    : "语音输入"}
              </Text>
            </Pressable>
          </View>
          <Pressable style={styles.send} onPress={() => void createTask()} disabled={submitting}>
            {submitting ? <ActivityIndicator color="white" /> : <Text style={styles.sendText}>开始</Text>}
          </Pressable>
        </View>
      </View>
      <View style={styles.filters}>
        {filters.map((item) => (
          <Pressable key={item.label} onPress={() => setFilter(item.value)} style={[styles.filter, filter === item.value && styles.filterActive]}>
            <Text style={[styles.filterText, filter === item.value && styles.filterTextActive]}>{item.label}</Text>
          </Pressable>
        ))}
      </View>
      <FlatList
        data={tasks}
        keyExtractor={(task) => task.task_id}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void refresh()} />}
        contentContainerStyle={styles.list}
        ListEmptyComponent={<Text style={styles.empty}>这里还没有任务</Text>}
        renderItem={({ item }) => (
          <Pressable style={styles.task} onPress={() => router.push(`/tasks/${item.task_id}`)}>
            <View style={styles.taskHeader}>
              <Text style={styles.state}>{stateLabel(item.state)}</Text>
              <Text style={styles.time}>{new Date(item.updated_at * 1000).toLocaleString()}</Text>
            </View>
            <Text style={styles.goal} numberOfLines={3}>{item.goal}</Text>
            {item.phase ? <Text style={styles.phase}>{item.phase}</Text> : null}
          </Pressable>
        )}
      />
    </View>
  );
}

function stateLabel(state: TaskState): string {
  return ({ queued: "排队中", running: "进行中", waiting_approval: "待确认", paused: "已暂停", completed: "已完成", failed: "失败", cancelled: "已取消" })[state];
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  topline: { paddingHorizontal: 18, paddingTop: 12, flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  heading: { color: colors.ink, fontSize: 20, fontWeight: "700" },
  composer: { margin: 16, padding: 14, borderRadius: 18, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line, gap: 10 },
  input: { minHeight: 72, color: colors.ink, fontSize: 16, textAlignVertical: "top" },
  attachments: { color: colors.muted, fontSize: 13 },
  actions: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  ingress: { flexDirection: "row", alignItems: "center", gap: 16 },
  link: { color: colors.accent, fontWeight: "600" },
  recording: { color: colors.danger },
  send: { minWidth: 74, alignItems: "center", backgroundColor: colors.accent, paddingVertical: 10, paddingHorizontal: 18, borderRadius: 12 },
  sendText: { color: "white", fontWeight: "700" },
  filters: { flexDirection: "row", gap: 8, paddingHorizontal: 16, paddingBottom: 8 },
  filter: { paddingHorizontal: 12, paddingVertical: 7, borderRadius: 14, backgroundColor: colors.surface },
  filterActive: { backgroundColor: colors.accentSoft },
  filterText: { color: colors.muted },
  filterTextActive: { color: colors.accent, fontWeight: "600" },
  list: { padding: 16, gap: 12 },
  task: { padding: 16, backgroundColor: colors.surface, borderRadius: 16, borderWidth: 1, borderColor: colors.line, gap: 8 },
  taskHeader: { flexDirection: "row", justifyContent: "space-between" },
  state: { color: colors.accent, fontWeight: "700" },
  time: { color: colors.muted, fontSize: 12 },
  goal: { color: colors.ink, fontSize: 16, lineHeight: 23 },
  phase: { color: colors.muted, fontSize: 13 },
  empty: { color: colors.muted, textAlign: "center", paddingTop: 48 },
});
