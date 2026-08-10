import { Pressable, StyleSheet, Text, TextInput, View } from "react-native";

import type { TaskLaunchKind, TaskLaunchPolicy } from "@/api/models";
import { colors } from "@/theme";

export function TaskLaunchEditor({ policy, onChange }: { policy: TaskLaunchPolicy; onChange(policy: TaskLaunchPolicy): void }) {
  function selectKind(kind: TaskLaunchKind) {
    if (kind === "immediate") onChange(immediatePolicy());
    else if (kind === "scheduled") onChange({
      ...immediatePolicy(),
      kind,
      schedule_type: "interval",
      interval_seconds: 3600,
    });
    else onChange({
      ...immediatePolicy(),
      kind,
      event_source: "webhook",
    });
  }

  function selectSchedule(scheduleType: "one_time" | "interval" | "cron") {
    onChange({
      ...immediatePolicy(),
      kind: "scheduled",
      schedule_type: scheduleType,
      run_at: scheduleType === "one_time" ? Date.now() / 1000 + 3600 : null,
      interval_seconds: scheduleType === "interval" ? 3600 : null,
      cron: scheduleType === "cron" ? "0 9 * * *" : "",
    });
  }

  return (
    <View style={styles.card}>
      <Text style={styles.title}>启动方式</Text>
      <View style={styles.options}>
        <Choice label="立即" selected={policy.kind === "immediate"} onPress={() => selectKind("immediate")} />
        <Choice label="定时" selected={policy.kind === "scheduled"} onPress={() => selectKind("scheduled")} />
        <Choice label="事件" selected={policy.kind === "event"} onPress={() => selectKind("event")} />
      </View>
      {policy.kind === "immediate" ? <Text style={styles.help}>创建后立即执行一次，以后也可以手动再次执行。</Text> : null}
      {policy.kind === "scheduled" ? (
        <View style={styles.fields}>
          <View style={styles.options}>
            <Choice label="一次" selected={policy.schedule_type === "one_time"} onPress={() => selectSchedule("one_time")} />
            <Choice label="间隔" selected={policy.schedule_type === "interval"} onPress={() => selectSchedule("interval")} />
            <Choice label="Cron" selected={policy.schedule_type === "cron"} onPress={() => selectSchedule("cron")} />
          </View>
          {policy.schedule_type === "one_time" ? (
            <>
              <Text style={styles.label}>执行时间</Text>
              <TextInput
                accessibilityLabel="定时执行时间"
                placeholder="2026-08-11 09:00"
                placeholderTextColor={colors.muted}
                style={styles.input}
                defaultValue={formatLocalTime(policy.run_at)}
                onEndEditing={({ nativeEvent: { text: value } }) => {
                  const timestamp = Date.parse(value.replace(" ", "T"));
                  onChange({ ...policy, run_at: Number.isFinite(timestamp) ? timestamp / 1000 : null });
                }}
              />
              <Text style={styles.help}>使用手机当前时区；请输入“年-月-日 时:分”。</Text>
            </>
          ) : null}
          {policy.schedule_type === "interval" ? (
            <>
              <Text style={styles.label}>每隔多少分钟</Text>
              <TextInput
                accessibilityLabel="定时间隔分钟"
                keyboardType="number-pad"
                style={styles.input}
                value={policy.interval_seconds ? String(Math.round(policy.interval_seconds / 60)) : ""}
                onChangeText={(value) => onChange({ ...policy, interval_seconds: Math.max(1, Number(value) || 0) * 60 })}
              />
            </>
          ) : null}
          {policy.schedule_type === "cron" ? (
            <>
              <Text style={styles.label}>Cron 表达式</Text>
              <TextInput accessibilityLabel="Cron 表达式" autoCapitalize="none" style={styles.input} value={policy.cron} onChangeText={(cron) => onChange({ ...policy, cron })} />
              <Text style={styles.label}>时区</Text>
              <TextInput accessibilityLabel="任务时区" autoCapitalize="none" style={styles.input} value={policy.timezone} onChangeText={(timezone) => onChange({ ...policy, timezone })} />
            </>
          ) : null}
        </View>
      ) : null}
      {policy.kind === "event" ? (
        <View style={styles.fields}>
          <Text style={styles.label}>事件来源</Text>
          <TextInput
            accessibilityLabel="事件来源"
            autoCapitalize="none"
            placeholder="例如 webhook、github、jira"
            placeholderTextColor={colors.muted}
            style={styles.input}
            value={policy.event_source}
            onChangeText={(event_source) => onChange({ ...policy, event_source })}
          />
          <Text style={styles.help}>事件到达时会在同一个任务下创建新的执行记录，并自动去重。</Text>
        </View>
      ) : null}
    </View>
  );
}

export function immediatePolicy(): TaskLaunchPolicy {
  return {
    kind: "immediate",
    schedule_type: null,
    run_at: null,
    interval_seconds: null,
    cron: "",
    timezone: "Asia/Shanghai",
    event_source: "",
    source_config: {},
  };
}

export function isLaunchPolicyValid(policy: TaskLaunchPolicy): boolean {
  if (policy.kind === "immediate") return true;
  if (policy.kind === "event") return Boolean(policy.event_source.trim());
  if (policy.schedule_type === "one_time") return Boolean(policy.run_at && policy.run_at > Date.now() / 1000);
  if (policy.schedule_type === "interval") return Boolean(policy.interval_seconds && policy.interval_seconds > 0);
  if (policy.schedule_type === "cron") return Boolean(policy.cron.trim() && policy.timezone.trim());
  return false;
}

function Choice({ label, selected, onPress }: { label: string; selected: boolean; onPress(): void }) {
  return (
    <Pressable accessibilityRole="radio" accessibilityState={{ checked: selected }} onPress={onPress} style={[styles.choice, selected && styles.choiceSelected]}>
      <Text style={[styles.choiceText, selected && styles.choiceTextSelected]}>{label}</Text>
    </Pressable>
  );
}

function formatLocalTime(value: number | null): string {
  if (!value) return "";
  const date = new Date(value * 1000);
  const part = (number: number) => String(number).padStart(2, "0");
  return `${date.getFullYear()}-${part(date.getMonth() + 1)}-${part(date.getDate())} ${part(date.getHours())}:${part(date.getMinutes())}`;
}

const styles = StyleSheet.create({
  card: { marginTop: 6, padding: 14, borderRadius: 12, backgroundColor: colors.accentSoft, gap: 10 },
  title: { color: colors.ink, fontWeight: "700" },
  options: { flexDirection: "row", gap: 8 },
  choice: { flex: 1, minHeight: 38, alignItems: "center", justifyContent: "center", borderRadius: 10, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line },
  choiceSelected: { backgroundColor: colors.accent, borderColor: colors.accent },
  choiceText: { color: colors.muted, fontWeight: "600" },
  choiceTextSelected: { color: "white" },
  fields: { gap: 8 },
  label: { color: colors.ink, fontWeight: "600", fontSize: 13 },
  input: { minHeight: 44, borderRadius: 10, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.surface, paddingHorizontal: 11, color: colors.ink },
  help: { color: colors.muted, fontSize: 12, lineHeight: 18 },
});
