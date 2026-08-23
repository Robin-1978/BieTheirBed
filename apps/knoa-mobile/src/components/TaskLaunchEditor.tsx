import { useState } from "react";
import { StyleSheet, Text, TextInput, View } from "react-native";

import type { MCPResourceCatalogItem, TaskLaunchKind, TaskLaunchPolicy } from "@/api/models";
import { AppPressable } from "@/components/AppPressable";
import { useI18n } from "@/i18n";
import { colors } from "@/theme";

type SchedulePreset = "one_time" | "daily" | "weekly" | "interval" | "cron";

export function TaskLaunchEditor({ policy, onChange, mcpResources = [] }: { policy: TaskLaunchPolicy; onChange(policy: TaskLaunchPolicy): void; mcpResources?: MCPResourceCatalogItem[] }) {
  const { t } = useI18n();
  const [advanced, setAdvanced] = useState(policy.kind === "event" || isAdvancedCron(policy.cron));
  const [preset, setPreset] = useState<SchedulePreset>(() => schedulePreset(policy));
  const selectedResource = mcpResources.find(
    (item) => item.server_id === mcpServerId(policy) && item.uri === resourceUri(policy),
  );

  function selectMcpResource(resource: MCPResourceCatalogItem) {
    onChange({
      ...policy,
      event_source: `mcp:${resource.server_id}`,
      source_config: {
        resource_uri_prefix: resource.uri,
        include_root: true,
        include_descendants: false,
      },
    });
  }

  function selectKind(kind: TaskLaunchKind) {
    if (kind === "immediate") onChange(immediatePolicy());
    else if (kind === "scheduled") {
      setPreset("one_time");
      onChange({ ...immediatePolicy(), kind, schedule_type: "one_time", run_at: Date.now() / 1000 + 3600 });
    }
    else onChange({ ...immediatePolicy(), kind, event_source: "webhook", source_config: {} });
  }

  function selectPreset(next: SchedulePreset) {
    setPreset(next);
    if (next === "one_time") onChange({ ...immediatePolicy(), kind: "scheduled", schedule_type: "one_time", run_at: Date.now() / 1000 + 3600 });
    if (next === "interval") onChange({ ...immediatePolicy(), kind: "scheduled", schedule_type: "interval", interval_seconds: 3600 });
    if (next === "daily") onChange({ ...immediatePolicy(), kind: "scheduled", schedule_type: "cron", cron: "0 9 * * *" });
    if (next === "weekly") onChange({ ...immediatePolicy(), kind: "scheduled", schedule_type: "cron", cron: "0 9 * * 1" });
    if (next === "cron") onChange({ ...immediatePolicy(), kind: "scheduled", schedule_type: "cron", cron: policy.cron || "0 9 * * *" });
  }

  function updateCronTime(value: string) {
    const [hour, minute] = parseTime(value);
    if (hour === null || minute === null) {
      onChange({ ...policy, schedule_type: "cron", cron: "" });
      return;
    }
    const parts = policy.cron.trim().split(/\s+/);
    const day = parts[4] ?? (preset === "weekly" ? "1" : "*");
    onChange({ ...policy, schedule_type: "cron", cron: `${minute} ${hour} * * ${day}` });
  }

  function updateWeekday(day: string) {
    const [minute, hour] = policy.cron.trim().split(/\s+/);
    onChange({ ...policy, schedule_type: "cron", cron: `${minute || "0"} ${hour || "9"} * * ${day}` });
  }

  return (
    <View style={styles.card}>
      <Text style={styles.title}>{t("taskLaunch.title")}</Text>
      <View style={styles.options}>
        <Choice label={t("taskLaunch.immediate")} selected={policy.kind === "immediate"} onPress={() => selectKind("immediate")} />
        <Choice label={t("taskLaunch.scheduled")} selected={policy.kind === "scheduled"} onPress={() => selectKind("scheduled")} />
        {advanced ? <Choice label={t("taskLaunch.event")} selected={policy.kind === "event"} onPress={() => selectKind("event")} /> : null}
      </View>
      {policy.kind === "immediate" ? <Text style={styles.help}>{t("taskLaunch.immediateHelp")}</Text> : null}
      {policy.kind === "scheduled" ? (
        <View style={styles.fields}>
          <Text style={styles.label}>{t("taskLaunch.repeat")}</Text>
          <View style={styles.options}>
            <Choice label={t("taskLaunch.once")} selected={preset === "one_time"} onPress={() => selectPreset("one_time")} />
            <Choice label={t("taskLaunch.daily")} selected={preset === "daily"} onPress={() => selectPreset("daily")} />
            <Choice label={t("taskLaunch.weekly")} selected={preset === "weekly"} onPress={() => selectPreset("weekly")} />
          </View>
          <View style={styles.options}>
            <Choice label={t("taskLaunch.interval")} selected={preset === "interval"} onPress={() => selectPreset("interval")} />
            {advanced ? <Choice label={t("taskLaunch.cron")} selected={preset === "cron"} onPress={() => selectPreset("cron")} /> : null}
          </View>
          {preset === "one_time" ? (
            <>
              <Text style={styles.label}>{t("taskLaunch.runAt")}</Text>
              <TextInput key="one-time" accessibilityLabel={t("taskLaunch.runAt")} placeholder={t("taskLaunch.runAtPlaceholder")} placeholderTextColor={colors.muted} style={styles.input} defaultValue={formatLocalTime(policy.run_at)} onEndEditing={({ nativeEvent: { text: value } }) => {
                const timestamp = Date.parse(value.replace(" ", "T"));
                onChange({ ...policy, run_at: Number.isFinite(timestamp) ? timestamp / 1000 : null });
              }} />
              <Text style={styles.help}>{t("taskLaunch.localTime")}</Text>
            </>
          ) : null}
          {preset === "daily" || preset === "weekly" ? (
            <>
              <Text style={styles.label}>{t("taskLaunch.time")}</Text>
              <TextInput key={`clock-${preset}`} accessibilityLabel={t("taskLaunch.time")} placeholder="09:00" placeholderTextColor={colors.muted} style={styles.input} defaultValue={timeFromCron(policy.cron)} onEndEditing={({ nativeEvent: { text: value } }) => updateCronTime(value)} />
              {preset === "weekly" ? (
                <View style={styles.weekdays}>
                  {[{ value: "1", label: t("weekday.mon") }, { value: "2", label: t("weekday.tue") }, { value: "3", label: t("weekday.wed") }, { value: "4", label: t("weekday.thu") }, { value: "5", label: t("weekday.fri") }, { value: "6", label: t("weekday.sat") }, { value: "0", label: t("weekday.sun") }].map((day) => (
                    <Choice key={day.value} label={day.label} selected={policy.cron.trim().split(/\s+/)[4] === day.value} onPress={() => updateWeekday(day.value)} compact />
                  ))}
                </View>
              ) : null}
              <Text style={styles.help}>{t("taskLaunch.timezoneSummary", { timezone: policy.timezone || "Asia/Shanghai" })}</Text>
            </>
          ) : null}
          {preset === "interval" ? (
            <>
              <Text style={styles.label}>{t("taskLaunch.intervalMinutes")}</Text>
              <TextInput accessibilityLabel={t("taskLaunch.intervalMinutes")} keyboardType="number-pad" style={styles.input} value={policy.interval_seconds ? String(Math.round(policy.interval_seconds / 60)) : ""} onChangeText={(value) => onChange({ ...policy, interval_seconds: Math.max(1, Number(value) || 0) * 60 })} />
            </>
          ) : null}
          {preset === "cron" ? (
            <>
              <Text style={styles.label}>{t("taskLaunch.cronExpression")}</Text>
              <TextInput accessibilityLabel={t("taskLaunch.cronExpression")} autoCapitalize="none" style={styles.input} value={policy.cron} onChangeText={(cron) => onChange({ ...policy, cron })} />
              <Text style={styles.label}>{t("taskLaunch.timezone")}</Text>
              <TextInput accessibilityLabel={t("taskLaunch.timezone")} autoCapitalize="none" style={styles.input} value={policy.timezone} onChangeText={(timezone) => onChange({ ...policy, timezone })} />
            </>
          ) : null}
        </View>
      ) : null}
      {policy.kind === "event" ? (
        <View style={styles.fields}>
          <Text style={styles.label}>{t("taskLaunch.eventType")}</Text>
          <View style={styles.options}>
            <Choice label={t("taskLaunch.webhook")} selected={!isMcpEvent(policy)} onPress={() => onChange({ ...policy, event_source: "webhook", source_config: {} })} />
            <Choice label={t("taskLaunch.mcpResource")} selected={isMcpEvent(policy)} onPress={() => {
              const first = selectedResource ?? mcpResources[0];
              if (first) selectMcpResource(first);
              else onChange({ ...policy, event_source: "mcp:", source_config: {} });
            }} />
          </View>
          {isMcpEvent(policy) ? (
            <>
              <Text style={styles.label}>{t("taskLaunch.selectResource")}</Text>
              {mcpResources.length ? (
                <View style={styles.catalogList}>
                  {mcpResources.map((resource) => (
                    <CatalogChoice
                      key={`${resource.server_id}\n${resource.uri}`}
                      label={resource.name || t("taskLaunch.resourceFallback")}
                      detail={resource.description || resource.mime_type || t("taskLaunch.discoveredResource")}
                      selected={resource === selectedResource}
                      onPress={() => selectMcpResource(resource)}
                    />
                  ))}
                </View>
              ) : <Text style={styles.help}>{t("taskLaunch.noResources")}</Text>}
              <View style={styles.options}>
                <Choice label={t("taskLaunch.resourceOnly")} selected={!Boolean(policy.source_config?.include_descendants)} onPress={() => onChange({ ...policy, source_config: { ...policy.source_config, include_root: true, include_descendants: false } })} />
                <Choice label={t("taskLaunch.resourceTree")} selected={Boolean(policy.source_config?.include_descendants) && Boolean(policy.source_config?.include_root ?? true)} onPress={() => onChange({ ...policy, source_config: { ...policy.source_config, include_root: true, include_descendants: true } })} />
                <Choice label={t("taskLaunch.descendantEvents")} selected={Boolean(policy.source_config?.include_descendants) && policy.source_config?.include_root === false} onPress={() => onChange({ ...policy, source_config: { ...policy.source_config, include_root: false, include_descendants: true } })} />
              </View>
              <Text style={styles.help}>{t("taskLaunch.mcpResourceHelp")}</Text>
            </>
          ) : null}
          <Text style={styles.help}>{t("taskLaunch.eventHelp")}</Text>
        </View>
      ) : null}
      <View style={styles.summary}>
        <Text style={styles.summaryLabel}>{t("taskLaunch.summary")}</Text>
        <Text style={styles.summaryText}>{policySummary(policy, preset, t, mcpResources)}</Text>
      </View>
      <AppPressable accessibilityRole="button" onPress={() => setAdvanced((value) => !value)} style={styles.advanced}>
        <Text style={styles.advancedText}>{advanced ? t("taskLaunch.hideAdvanced") : t("taskLaunch.showAdvanced")}</Text>
      </AppPressable>
    </View>
  );
}

export function immediatePolicy(): TaskLaunchPolicy {
  return { kind: "immediate", schedule_type: null, run_at: null, interval_seconds: null, cron: "", timezone: "Asia/Shanghai", event_source: "", source_config: {} };
}

export function isLaunchPolicyValid(policy: TaskLaunchPolicy): boolean {
  if (policy.kind === "immediate") return true;
  if (policy.kind === "event") {
    if (!policy.event_source.trim()) return false;
    if (isMcpEvent(policy)) return Boolean(mcpServerId(policy) && resourceUri(policy));
    return true;
  }
  if (policy.schedule_type === "one_time") return Boolean(policy.run_at && policy.run_at > Date.now() / 1000);
  if (policy.schedule_type === "interval") return Boolean(policy.interval_seconds && policy.interval_seconds > 0);
  if (policy.schedule_type === "cron") return Boolean(policy.cron.trim() && policy.timezone.trim());
  return false;
}

function schedulePreset(policy: TaskLaunchPolicy): SchedulePreset {
  if (policy.schedule_type === "one_time") return "one_time";
  if (policy.schedule_type === "interval") return "interval";
  if (isDailyCron(policy.cron)) return "daily";
  if (isWeeklyCron(policy.cron)) return "weekly";
  return "cron";
}

function isMcpEvent(policy: TaskLaunchPolicy): boolean {
  return policy.event_source.trim().toLowerCase().startsWith("mcp:");
}

function mcpServerId(policy: TaskLaunchPolicy): string {
  return isMcpEvent(policy) ? policy.event_source.trim().slice(4).trim() : "";
}

function resourceUri(policy: TaskLaunchPolicy): string {
  const config = policy.source_config ?? {};
  return String(config.resource_uri_prefix ?? "");
}

function isDailyCron(value: string): boolean {
  const parts = value.trim().split(/\s+/);
  return parts.length === 5 && parts[2] === "*" && parts[3] === "*" && parts[4] === "*";
}

function isWeeklyCron(value: string): boolean {
  const parts = value.trim().split(/\s+/);
  return parts.length === 5 && parts[2] === "*" && parts[3] === "*" && /^[0-6]$/.test(parts[4] ?? "");
}

function isAdvancedCron(value: string): boolean {
  return Boolean(value.trim()) && !isDailyCron(value) && !isWeeklyCron(value);
}

function parseTime(value: string): [number | null, number | null] {
  const match = /^(\d{1,2}):(\d{2})$/.exec(value.trim());
  if (!match) return [null, null];
  const hour = Number(match[1]);
  const minute = Number(match[2]);
  return hour >= 0 && hour <= 23 && minute >= 0 && minute <= 59 ? [hour, minute] : [null, null];
}

function timeFromCron(value: string): string {
  const parts = value.trim().split(/\s+/);
  if (parts.length < 2) return "09:00";
  return `${String(Number(parts[1]) || 0).padStart(2, "0")}:${String(Number(parts[0]) || 0).padStart(2, "0")}`;
}

function formatLocalTime(value: number | null): string {
  if (!value) return "";
  const date = new Date(value * 1000);
  const part = (number: number) => String(number).padStart(2, "0");
  return `${date.getFullYear()}-${part(date.getMonth() + 1)}-${part(date.getDate())} ${part(date.getHours())}:${part(date.getMinutes())}`;
}

function policySummary(policy: TaskLaunchPolicy, preset: SchedulePreset, t: ReturnType<typeof useI18n>["t"], mcpResources: MCPResourceCatalogItem[]): string {
  if (policy.kind === "immediate") return t("taskLaunch.summaryImmediate");
  if (policy.kind === "event") {
    if (!isMcpEvent(policy)) return t("taskLaunch.summaryEvent");
    const resource = mcpResources.find(
      (item) => item.server_id === mcpServerId(policy) && item.uri === resourceUri(policy),
    );
    return t("taskLaunch.summaryMcpEvent", {
      resource: resource?.name || t("taskLaunch.resourceFallback"),
    });
  }
  if (preset === "one_time") return policy.run_at
    ? t("taskLaunch.summaryOnce", { time: new Date(policy.run_at * 1000).toLocaleString() })
    : t("taskLaunch.summaryInvalid");
  if (preset === "interval") return policy.interval_seconds
    ? t("taskLaunch.summaryInterval", { minutes: Math.round(policy.interval_seconds / 60) })
    : t("taskLaunch.summaryInvalid");
  if (preset === "daily") return policy.cron ? t("taskLaunch.summaryDaily", { time: timeFromCron(policy.cron), timezone: policy.timezone }) : t("taskLaunch.summaryInvalid");
  if (preset === "weekly") {
    const day = policy.cron.trim().split(/\s+/)[4] ?? "";
    return policy.cron ? t("taskLaunch.summaryWeekly", { day: weekdayLabel(day, t), time: timeFromCron(policy.cron), timezone: policy.timezone }) : t("taskLaunch.summaryInvalid");
  }
  return policy.cron ? t("taskLaunch.summaryCron", { cron: policy.cron, timezone: policy.timezone }) : t("taskLaunch.summaryInvalid");
}

function weekdayLabel(value: string, t: ReturnType<typeof useI18n>["t"]): string {
  return ({ "1": t("weekday.monday"), "2": t("weekday.tuesday"), "3": t("weekday.wednesday"), "4": t("weekday.thursday"), "5": t("weekday.friday"), "6": t("weekday.saturday"), "0": t("weekday.sunday") } as Record<string, string>)[value] ?? "—";
}

function Choice({ label, selected, onPress, compact = false }: { label: string; selected: boolean; onPress(): void; compact?: boolean }) {
  return <AppPressable accessibilityRole="radio" accessibilityState={{ checked: selected }} onPress={onPress} style={[styles.choice, compact && styles.choiceCompact, selected && styles.choiceSelected]}><Text style={[styles.choiceText, selected && styles.choiceTextSelected]}>{label}</Text></AppPressable>;
}

function CatalogChoice({ label, detail, selected, onPress }: { label: string; detail: string; selected: boolean; onPress(): void }) {
  return <AppPressable accessibilityRole="radio" accessibilityState={{ checked: selected }} onPress={onPress} style={[styles.catalogChoice, selected && styles.catalogChoiceSelected]}><Text style={[styles.catalogLabel, selected && styles.catalogLabelSelected]}>{label}</Text><Text numberOfLines={2} style={styles.catalogDetail}>{detail}</Text></AppPressable>;
}

const styles = StyleSheet.create({
  card: { marginTop: 6, padding: 14, borderRadius: 12, backgroundColor: colors.accentSoft, gap: 10 },
  title: { color: colors.ink, fontWeight: "700" },
  options: { flexDirection: "row", gap: 8 },
  choice: { flex: 1, minHeight: 38, alignItems: "center", justifyContent: "center", borderRadius: 10, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line },
  choiceCompact: { flex: 0, width: 38, minHeight: 36 },
  choiceSelected: { backgroundColor: colors.accent, borderColor: colors.accent },
  choiceText: { color: colors.muted, fontWeight: "600" },
  choiceTextSelected: { color: "white" },
  fields: { gap: 8 },
  catalogList: { gap: 6 },
  catalogChoice: { padding: 10, borderRadius: 10, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.surface, gap: 3 },
  catalogChoiceSelected: { borderColor: colors.accent, backgroundColor: colors.accentSoft },
  catalogLabel: { color: colors.ink, fontWeight: "700" },
  catalogLabelSelected: { color: colors.accent },
  catalogDetail: { color: colors.muted, fontSize: 11, lineHeight: 16 },
  label: { color: colors.ink, fontWeight: "600", fontSize: 13 },
  input: { minHeight: 44, borderRadius: 10, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.surface, paddingHorizontal: 11, color: colors.ink },
  help: { color: colors.muted, fontSize: 12, lineHeight: 18 },
  weekdays: { flexDirection: "row", justifyContent: "space-between", gap: 4 },
  summary: { padding: 11, borderRadius: 10, backgroundColor: colors.surface, gap: 3 },
  summaryLabel: { color: colors.muted, fontSize: 11, fontWeight: "700" },
  summaryText: { color: colors.ink, fontSize: 13, lineHeight: 19 },
  advanced: { minHeight: 36, justifyContent: "center", alignItems: "center" },
  advancedText: { color: colors.muted, fontSize: 12, fontWeight: "600" },
});
