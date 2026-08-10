import { router } from "expo-router";
import { useCallback, useEffect, useMemo, useState } from "react";
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

import type { AndroidRelease, TaskOrigin, TaskSnapshot, TaskState } from "@/api/models";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";
import { isAndroidUpdateAvailable } from "@/update/androidUpdater";

type Filter = "all" | "active" | "approval" | "completed";

const filters: Array<{ label: string; value: Filter }> = [
  { label: "全部", value: "all" },
  { label: "进行中", value: "active" },
  { label: "待确认", value: "approval" },
  { label: "已完成", value: "completed" },
];

export default function TasksScreen() {
  const gateway = useGateway();
  const [tasks, setTasks] = useState<TaskSnapshot[]>([]);
  const [filter, setFilter] = useState<Filter>("all");
  const [goal, setGoal] = useState("");
  const [creating, setCreating] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [availableUpdate, setAvailableUpdate] = useState<AndroidRelease | null>(null);

  const refresh = useCallback(async () => {
    if (!gateway.client) return;
    setRefreshing(true);
    try {
      const result = await gateway.client.listTasks({ limit: 100 });
      setTasks(result.tasks);
    } finally {
      setRefreshing(false);
    }
  }, [gateway.client]);

  useEffect(() => { void refresh(); }, [refresh, gateway.latestEvent]);

  useEffect(() => {
    if (!gateway.client) return;
    void gateway.client.latestAndroidRelease()
      .then((release) => setAvailableUpdate(isAndroidUpdateAvailable(release) ? release : null))
      .catch(() => undefined);
  }, [gateway.client]);

  const visibleTasks = useMemo(() => tasks.filter((task) => matchesFilter(task.state, filter)), [filter, tasks]);

  async function createTask() {
    const text = goal.trim();
    if (!text || !gateway.client || creating) return;
    setCreating(true);
    try {
      const accepted = await gateway.client.createTask({
        text,
      });
      setGoal("");
      await refresh();
      router.push(`/tasks/${accepted.task_id}`);
    } finally {
      setCreating(false);
    }
  }

  return (
    <View style={styles.container}>
      <View style={styles.topline}>
        <View>
          <Text style={styles.heading}>任务</Text>
          <Text style={styles.description}>交给小诺在后台独立完成</Text>
        </View>
        <Pressable onPress={() => router.replace("/chat")}><Text style={styles.link}>返回对话</Text></Pressable>
      </View>
      <View style={styles.composer}>
        <TextInput
          value={goal}
          onChangeText={setGoal}
          placeholder="输入一个可独立完成的任务…"
          placeholderTextColor={colors.muted}
          multiline
          style={styles.input}
        />
        <Pressable
          disabled={!goal.trim() || creating}
          onPress={() => void createTask()}
          style={[styles.create, (!goal.trim() || creating) && styles.createDisabled]}
        >
          {creating ? <ActivityIndicator color="white" size="small" /> : <Text style={styles.createText}>开始</Text>}
        </Pressable>
      </View>
      {availableUpdate ? (
        <Pressable style={styles.updateBanner} onPress={() => router.push("/update")}>
          <View>
            <Text style={styles.updateTitle}>小诺 {availableUpdate.version_name} 可以更新</Text>
            <Text style={styles.updateDetail}>支持断点续传</Text>
          </View>
          <Text style={styles.updateLink}>查看</Text>
        </Pressable>
      ) : null}
      <View style={styles.filters}>
        {filters.map((item) => (
          <Pressable
            key={item.value}
            onPress={() => setFilter(item.value)}
            style={[styles.filter, filter === item.value && styles.filterActive]}
          >
            <Text style={[styles.filterText, filter === item.value && styles.filterTextActive]}>{item.label}</Text>
          </Pressable>
        ))}
      </View>
      <FlatList
        data={visibleTasks}
        keyExtractor={(task) => task.task_id}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void refresh()} />}
        contentContainerStyle={styles.list}
        ListEmptyComponent={<Text style={styles.empty}>这里还没有任务</Text>}
        renderItem={({ item }) => (
          <Pressable style={styles.task} onPress={() => router.push(`/tasks/${item.task_id}`)}>
            <View style={styles.taskHeader}>
              <Text style={styles.origin}>{originLabel(item.origin)}</Text>
              <Text style={styles.state}>{stateLabel(item.state)}</Text>
            </View>
            <Text style={styles.goal} numberOfLines={3}>{item.goal}</Text>
            <View style={styles.metaRow}>
              <Text style={styles.time}>{new Date(item.updated_at * 1000).toLocaleString()}</Text>
              <Text style={styles.attempts}>执行 {Math.max(1, item.attempt_count)} 次</Text>
            </View>
          </Pressable>
        )}
      />
    </View>
  );
}

function matchesFilter(state: TaskState, filter: Filter): boolean {
  if (filter === "active") return state === "queued" || state === "running" || state === "paused";
  if (filter === "approval") return state === "waiting_approval";
  if (filter === "completed") return state === "completed";
  return true;
}

function originLabel(origin: TaskOrigin): string {
  return ({ user: "立即执行", agent: "小诺创建", scheduled: "定时执行", event: "事件启动", chat: "对话" })[origin];
}

function stateLabel(state: TaskState): string {
  return ({ queued: "排队中", running: "进行中", waiting_approval: "待确认", paused: "已暂停", completed: "已完成", failed: "失败", cancelled: "已取消" })[state];
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  topline: { padding: 18, flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  heading: { color: colors.ink, fontSize: 22, fontWeight: "700" },
  description: { color: colors.muted, marginTop: 4, fontSize: 13 },
  link: { color: colors.accent, fontWeight: "600" },
  composer: { marginHorizontal: 16, marginBottom: 14, padding: 12, borderRadius: 16, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line, flexDirection: "row", alignItems: "flex-end", gap: 10 },
  input: { flex: 1, minHeight: 42, maxHeight: 110, color: colors.ink, fontSize: 15, lineHeight: 21, paddingHorizontal: 4, paddingVertical: 8 },
  create: { minWidth: 58, height: 38, paddingHorizontal: 14, borderRadius: 19, backgroundColor: colors.accent, alignItems: "center", justifyContent: "center" },
  createDisabled: { opacity: 0.4 },
  createText: { color: "white", fontWeight: "700" },
  updateBanner: { marginHorizontal: 16, marginBottom: 14, padding: 14, borderRadius: 16, backgroundColor: colors.accentSoft, flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  updateTitle: { color: colors.ink, fontWeight: "700" },
  updateDetail: { color: colors.muted, fontSize: 12, marginTop: 3 },
  updateLink: { color: colors.accent, fontWeight: "700" },
  filters: { flexDirection: "row", gap: 8, paddingHorizontal: 16, paddingBottom: 8 },
  filter: { paddingHorizontal: 12, paddingVertical: 7, borderRadius: 14, backgroundColor: colors.surface },
  filterActive: { backgroundColor: colors.accentSoft },
  filterText: { color: colors.muted },
  filterTextActive: { color: colors.accent, fontWeight: "600" },
  list: { padding: 16, gap: 12, flexGrow: 1 },
  task: { padding: 16, backgroundColor: colors.surface, borderRadius: 16, borderWidth: 1, borderColor: colors.line, gap: 8 },
  taskHeader: { flexDirection: "row", justifyContent: "space-between" },
  origin: { color: colors.ink, fontWeight: "700" },
  state: { color: colors.accent, fontWeight: "600" },
  metaRow: { flexDirection: "row", justifyContent: "space-between" },
  time: { color: colors.muted, fontSize: 12 },
  attempts: { color: colors.muted, fontSize: 12 },
  goal: { color: colors.ink, fontSize: 16, lineHeight: 23 },
  empty: { color: colors.muted, textAlign: "center", paddingTop: 64 },
});
