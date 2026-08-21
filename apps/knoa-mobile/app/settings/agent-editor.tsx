import { router, Stack, useLocalSearchParams } from "expo-router";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from "react-native";

import type { ManagedConfig, ManagedNodeAgent } from "@/api/models";
import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { useI18n } from "@/i18n";
import {
  BUILT_IN_AGENT_IDS,
  createKnoaAgent,
  csvValues,
  normalizeAgentId,
  removeNodeAgent,
  setDelegationEnabled,
  upsertNodeAgent,
} from "@/models/agentConfiguration";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";

export default function AgentEditorScreen() {
  const params = useLocalSearchParams<{ agentId?: string; mode?: string }>();
  const gateway = useGateway();
  const { t } = useI18n();
  const originalAgentId = params.mode === "new" ? "" : String(params.agentId || "");
  const [document, setDocument] = useState<ManagedConfig | null>(null);
  const [agentId, setAgentId] = useState(originalAgentId);
  const [agent, setAgent] = useState<ManagedNodeAgent | null>(null);
  const [builtInPromptRef, setBuiltInPromptRef] = useState("");
  const [working, setWorking] = useState("");
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    setWorking("load");
    setMessage("");
    try {
      const current = await gateway.runAuthenticated((client) => client.getConfigCurrent());
      const next = current.revision.document;
      setDocument(next);
      if (originalAgentId) {
        const existing = next.agents.agents[originalAgentId];
        if (!existing) throw new Error(t("settings.agentEditor.notFound"));
        const copy = JSON.parse(JSON.stringify(existing)) as ManagedNodeAgent;
        setAgent(copy);
        setBuiltInPromptRef(copy.instructions_ref);
      } else {
        setAgent(createKnoaAgent(next.default_model, t("settings.agentEditor.defaultName")));
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : t("settings.agentEditor.loadFailed"));
    } finally {
      setWorking("");
    }
  }, [gateway.runAuthenticated, originalAgentId, t]);

  useEffect(() => { void load(); }, [load]);

  const isBuiltIn = BUILT_IN_AGENT_IDS.has(originalAgentId);
  const targetAgents = useMemo(() => {
    if (!document) return [];
    return Object.entries(document.agents.agents).filter(([, candidate]) => candidate.visibility === "delegate");
  }, [document]);

  function update(mutator: (value: ManagedNodeAgent) => void) {
    setAgent((value) => {
      if (!value) return value;
      const next = JSON.parse(JSON.stringify(value)) as ManagedNodeAgent;
      mutator(next);
      return next;
    });
  }

  async function publish(next: ManagedConfig, summary: string) {
    const created = await gateway.runAuthenticated((client) => client.createConfigDraft());
    const replaced = await gateway.runAuthenticated((client) => client.replaceConfigDraft(
      created.draft_id,
      next,
      created.draft_version,
    ));
    const validation = await gateway.runAuthenticated((client) => client.validateConfigDraft(replaced.draft_id, true));
    if (!validation.valid) throw new Error(validation.issues[0]?.message || t("settings.common.configValidationFailed"));
    const result = await gateway.runAuthenticated((client) => client.publishConfigDraft(
      replaced.draft_id,
      replaced.draft_version,
      summary,
    ));
    if (result.state.apply_status === "failed") throw new Error(result.state.apply_error_code || t("settings.common.configApplyFailed"));
    return result.revision.document;
  }

  async function save() {
    if (!document || !agent || working) return;
    setWorking("save");
    setMessage("");
    try {
      const next = upsertNodeAgent(document, agentId, agent, originalAgentId);
      await publish(next, originalAgentId ? t("settings.agentEditor.updateSummary", { agentId: originalAgentId }) : t("settings.agentEditor.createSummary", { agentId }));
      router.back();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : t("settings.agentEditor.saveFailed"));
    } finally {
      setWorking("");
    }
  }

  function confirmDelete() {
    if (!document || !originalAgentId || isBuiltIn || working) return;
    Alert.alert(t("settings.agentEditor.deleteTitle"), t("settings.agentEditor.deleteMessage", { name: agent?.display_name || originalAgentId }), [
      { text: t("common.cancel"), style: "cancel" },
      {
        text: t("common.delete"),
        style: "destructive",
        onPress: () => {
          void (async () => {
            setWorking("delete");
            setMessage("");
            try {
              const next = removeNodeAgent(document, originalAgentId);
              await publish(next, t("settings.agentEditor.deleteSummary", { agentId: originalAgentId }));
              router.back();
            } catch (error) {
              setMessage(error instanceof Error ? error.message : t("settings.agentEditor.deleteFailed"));
            } finally {
              setWorking("");
            }
          })();
        },
      },
    ]);
  }

  if (!agent || !document) {
    return <View style={styles.center}>{working === "load" ? <ActivityIndicator color={colors.accent} /> : <Text style={styles.message}>{message || t("settings.agentEditor.openFailed")}</Text>}</View>;
  }

  const isSystem = agent.visibility === "system";
  const canChangeVisibility = !isSystem && originalAgentId !== document.agents.default_agent;
  return (
    <>
      <Stack.Screen options={{ title: originalAgentId ? agent.display_name : t("settings.agentEditor.newTitle") }} />
      <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
        <View style={styles.hero}>
          <View style={styles.heroIcon}><AppIcon name="agent" color={colors.accent} size={27} /></View>
          <View style={styles.flex}>
            <Text style={styles.title}>{agent.display_name}</Text>
            <Text style={styles.meta}>{t("settings.agentEditor.heroDetail")}</Text>
          </View>
        </View>

        <Section title={t("settings.agentEditor.sectionIdentity")}>
          <Field label={t("settings.agentEditor.agentId")} value={agentId} editable={!originalAgentId} onChange={(value) => setAgentId(normalizeAgentId(value))} placeholder="research_agent" />
          <Field label={t("settings.agentEditor.displayName")} value={agent.display_name} onChange={(display_name) => update((next) => { next.display_name = display_name; })} />
          <Metric label="Runtime" value={agent.kind === "codex" ? t("settings.agentEditor.codexRuntimeAdapter") : t("settings.agents.knoaRuntime")} />
          <Toggle label={t("settings.agentEditor.enabled")} value={agent.enabled} disabled={originalAgentId === document.agents.default_agent} onChange={(enabled) => update((next) => { next.enabled = enabled; })} />
          {!isSystem ? (
            <Choice label={t("settings.agentEditor.invocationMode")} value={agent.visibility} disabled={!canChangeVisibility} choices={[["user", t("settings.agentEditor.visibilityUserDirect")], ["delegate", t("settings.agentEditor.visibilityDelegateOnly")]]} onChange={(visibility) => update((next) => { next.visibility = visibility as ManagedNodeAgent["visibility"]; })} />
          ) : <Metric label={t("settings.agentEditor.invocationMode")} value={t("settings.agentEditor.visibilitySystemOnly")} />}
          <NumberField label={t("settings.agentEditor.maxConcurrency")} value={agent.max_concurrency} min={1} onChange={(max_concurrency) => update((next) => { next.max_concurrency = max_concurrency; })} />
        </Section>

        {agent.kind === "knoa" ? (
          <Section title={t("settings.agentEditor.sectionPromptModel")}>
            {agent.instructions_ref ? (
              <View style={styles.notice}>
                <Text style={styles.itemTitle}>{t("settings.agentEditor.builtInPrompt")}</Text>
                <Text style={styles.meta}>{agent.instructions_ref}</Text>
                <AppPressable style={styles.secondary} onPress={() => update((next) => { next.instructions_ref = ""; next.instructions = `You are ${next.display_name}.`; })}><Text style={styles.secondaryText}>{t("settings.agentEditor.switchToCustomPrompt")}</Text></AppPressable>
              </View>
            ) : (
              <>
                <Text style={styles.label}>{t("settings.agentEditor.systemPrompt")}</Text>
                <TextInput multiline value={agent.instructions} onChangeText={(instructions) => update((next) => { next.instructions = instructions; })} style={styles.prompt} placeholder={t("settings.agentEditor.systemPromptPlaceholder")} placeholderTextColor={colors.muted} />
                {builtInPromptRef ? <AppPressable style={styles.secondary} onPress={() => update((next) => { next.instructions = ""; next.instructions_ref = builtInPromptRef; })}><Text style={styles.secondaryText}>{t("settings.agentEditor.restoreBuiltInPrompt")}</Text></AppPressable> : null}
              </>
            )}
            <Text style={styles.label}>{t("settings.agentEditor.model")}</Text>
            <View style={styles.chips}>
              {Object.entries(document.models).map(([alias, model]) => (
                <Chip key={alias} selected={agent.model_binding.model === alias} label={model.model || alias} onPress={() => update((next) => { next.model_binding = { ownership: "platform", model: alias, hint: "" }; })} />
              ))}
            </View>
          </Section>
        ) : (
          <Section title={t("settings.agentEditor.sectionCodex")}>
            <Choice label={t("settings.agentEditor.sandbox")} value={agent.sandbox} choices={[["read-only", t("settings.agentEditor.sandboxReadOnly")], ["workspace-write", t("settings.agentEditor.sandboxWorkspaceWrite")]]} onChange={(sandbox) => update((next) => {
              next.sandbox = sandbox;
              next.native_capability_ceiling = sandbox === "workspace-write"
                ? ["workspace_read", "workspace_write", "command_execution", "native_file_edit"]
                : ["workspace_read", "command_execution"];
            })} />
            <Field label={t("settings.agentEditor.startupCommand")} value={agent.command.join(", ")} onChange={(value) => update((next) => { next.command = csvValues(value); })} />
            <Field label={t("settings.agentEditor.workingDirectory")} value={agent.cwd} onChange={(cwd) => update((next) => { next.cwd = cwd; })} />
          </Section>
        )}

        <Section title={t("settings.agentEditor.sectionSkillsTools")}>
          <Text style={styles.meta}>{t("settings.agentEditor.skillsToolsHint")}</Text>
          {Object.entries(document.skills).filter(([, skill]) => skill.enabled).map(([skillId]) => {
            const allowed = agent.allowed_skill_refs.includes(skillId);
            const selected = agent.default_skill_refs.includes(skillId);
            return (
              <View key={skillId} style={styles.skillRow}>
                <View style={styles.flex}><Text style={styles.itemTitle}>{skillId}</Text><Text style={styles.meta}>{selected ? t("settings.agentEditor.skillDefaultInject") : allowed ? t("settings.agentEditor.skillAllowedOnDemand") : t("settings.agentEditor.skillUnauthorized")}</Text></View>
                <AppPressable style={[styles.smallChip, allowed && styles.chipSelected]} onPress={() => update((next) => {
                  next.allowed_skill_refs = allowed ? next.allowed_skill_refs.filter((id) => id !== skillId) : [...next.allowed_skill_refs, skillId];
                  if (allowed) next.default_skill_refs = next.default_skill_refs.filter((id) => id !== skillId);
                })}><Text style={allowed ? styles.chipTextSelected : styles.chipText}>{t("settings.agentEditor.allow")}</Text></AppPressable>
                <AppPressable disabled={!allowed} style={[styles.smallChip, selected && styles.chipSelected, !allowed && styles.disabled]} onPress={() => update((next) => {
                  next.default_skill_refs = selected ? next.default_skill_refs.filter((id) => id !== skillId) : [...next.default_skill_refs, skillId];
                })}><Text style={selected ? styles.chipTextSelected : styles.chipText}>{t("settings.agentEditor.default")}</Text></AppPressable>
              </View>
            );
          })}
          {!Object.keys(document.skills).length ? <Text style={styles.meta}>{t("settings.agentEditor.noSkillsImported")}</Text> : null}
          <Field label={t("settings.agentEditor.allowedPlatformTools")} value={agent.allowed_platform_tools.join(", ")} onChange={(value) => update((next) => { next.allowed_platform_tools = csvValues(value); })} placeholder={t("settings.agentEditor.platformToolsPlaceholder")} />
          <Field label={t("settings.agentEditor.capabilityCeiling")} value={agent.platform_capability_ceiling.join(", ")} onChange={(value) => update((next) => { next.platform_capability_ceiling = csvValues(value); })} placeholder={t("settings.agentEditor.capabilityCeilingPlaceholder")} />
        </Section>

        <Section title={t("settings.agentEditor.sectionSubagent")}>
          <Toggle label={t("settings.agentEditor.allowChildTasks")} value={agent.delegation.allowed} onChange={(allowed) => setAgent((current) => current ? setDelegationEnabled(current, allowed) : current)} />
          {agent.delegation.allowed ? (
            <>
              <Text style={styles.label}>{t("settings.agentEditor.allowedTargetAgents")}</Text>
              <View style={styles.chips}>
                {targetAgents.map(([targetId, target]) => {
                  const selected = agent.delegation.targets.includes(targetId);
                  return <Chip key={targetId} selected={selected} label={target.display_name} onPress={() => update((next) => { next.delegation.targets = selected ? next.delegation.targets.filter((id) => id !== targetId) : [...next.delegation.targets, targetId]; })} />;
                })}
              </View>
              {!targetAgents.length ? <Text style={styles.warning}>{t("settings.agentEditor.needDelegateAgentWarning")}</Text> : null}
              <View style={styles.numberGrid}>
                <NumberField label={t("settings.agentEditor.maxDepth")} value={agent.delegation.max_depth} min={1} onChange={(max_depth) => update((next) => { next.delegation.max_depth = max_depth; })} />
                <NumberField label={t("settings.agentEditor.maxChildren")} value={agent.delegation.max_children} min={1} onChange={(max_children) => update((next) => { next.delegation.max_children = max_children; next.delegation.max_parallel_children = Math.min(next.delegation.max_parallel_children, max_children); })} />
                <NumberField label={t("settings.agentEditor.maxParallelChildren")} value={agent.delegation.max_parallel_children} min={1} onChange={(max_parallel_children) => update((next) => { next.delegation.max_parallel_children = Math.min(max_parallel_children, next.delegation.max_children); })} />
                <NumberField label={t("settings.agentEditor.childTimeoutSeconds")} value={agent.delegation.max_deadline_seconds} min={1} onChange={(max_deadline_seconds) => update((next) => { next.delegation.max_deadline_seconds = max_deadline_seconds; })} />
              </View>
            </>
          ) : <Text style={styles.meta}>{t("settings.agentEditor.delegationOffHint")}</Text>}
        </Section>

        <Section title={t("settings.agentEditor.sectionRuntimeLimits")}>
          <OptionalNumberField label={t("settings.agentEditor.maxIterationsOptional")} value={agent.runtime_limits.max_iterations} min={1} onChange={(max_iterations) => update((next) => { next.runtime_limits.max_iterations = max_iterations; })} />
          <OptionalNumberField label={t("settings.agentEditor.maxOutputTokensOptional")} value={agent.runtime_limits.max_output_tokens} min={64} onChange={(max_output_tokens) => update((next) => { next.runtime_limits.max_output_tokens = max_output_tokens; })} />
        </Section>

        <AppPressable disabled={Boolean(working)} style={styles.primary} onPress={() => void save()}>
          {working === "save" ? <ActivityIndicator color={colors.white} /> : <><AppIcon name="check" color={colors.white} size={19} /><Text style={styles.primaryText}>{t("settings.agentEditor.publishButton")}</Text></>}
        </AppPressable>
        <Text style={styles.impact}>{t("settings.agentEditor.impactHint")}</Text>
        {originalAgentId && !isBuiltIn ? <AppPressable disabled={Boolean(working)} style={styles.dangerButton} onPress={confirmDelete}><Text style={styles.dangerText}>{t("settings.agentEditor.deleteAgent")}</Text></AppPressable> : null}
        {message ? <Text style={styles.message}>{message}</Text> : null}
      </ScrollView>
    </>
  );
}

function Section({ title, children }: React.PropsWithChildren<{ title: string }>) {
  return <View style={styles.section}><Text style={styles.sectionTitle}>{title}</Text>{children}</View>;
}

function Metric({ label, value }: { label: string; value: string }) {
  return <View style={styles.row}><Text style={styles.meta}>{label}</Text><Text style={styles.metric}>{value}</Text></View>;
}

function Field({ label, value, onChange, placeholder = "", editable = true }: { label: string; value: string; onChange(value: string): void; placeholder?: string; editable?: boolean }) {
  return <View style={styles.field}><Text style={styles.label}>{label}</Text><TextInput editable={editable} value={value} onChangeText={onChange} placeholder={placeholder} placeholderTextColor={colors.muted} autoCapitalize="none" style={[styles.input, !editable && styles.disabled]} /></View>;
}

function Toggle({ label, value, onChange, disabled = false }: { label: string; value: boolean; onChange(value: boolean): void; disabled?: boolean }) {
  return <View style={styles.row}><Text style={styles.itemTitle}>{label}</Text><Switch disabled={disabled} value={value} onValueChange={onChange} /></View>;
}

function Choice({ label, value, choices, onChange, disabled = false }: { label: string; value: string; choices: Array<[string, string]>; onChange(value: string): void; disabled?: boolean }) {
  return <View style={styles.field}><Text style={styles.label}>{label}</Text><View style={styles.chips}>{choices.map(([id, title]) => <Chip key={id} label={title} selected={value === id} disabled={disabled} onPress={() => onChange(id)} />)}</View></View>;
}

function Chip({ label, selected, onPress, disabled = false }: { label: string; selected: boolean; onPress(): void; disabled?: boolean }) {
  return <AppPressable disabled={disabled} style={[styles.chip, selected && styles.chipSelected, disabled && styles.disabled]} onPress={onPress}><Text style={selected ? styles.chipTextSelected : styles.chipText}>{label}</Text></AppPressable>;
}

function NumberField({ label, value, onChange, min }: { label: string; value: number; onChange(value: number): void; min: number }) {
  return <View style={styles.numberField}><Text style={styles.label}>{label}</Text><TextInput key={String(value)} defaultValue={String(value)} keyboardType="numeric" style={styles.input} onEndEditing={({ nativeEvent }) => { const parsed = Number(nativeEvent.text); if (Number.isFinite(parsed)) onChange(Math.max(min, Math.floor(parsed))); }} /></View>;
}

function OptionalNumberField({ label, value, onChange, min }: { label: string; value: number | null; onChange(value: number | null): void; min: number }) {
  return <View style={styles.field}><Text style={styles.label}>{label}</Text><TextInput key={String(value)} defaultValue={value === null ? "" : String(value)} keyboardType="numeric" style={styles.input} onEndEditing={({ nativeEvent }) => { const text = nativeEvent.text.trim(); if (!text) return onChange(null); const parsed = Number(text); if (Number.isFinite(parsed)) onChange(Math.max(min, Math.floor(parsed))); }} /></View>;
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24, backgroundColor: colors.background },
  container: { padding: 17, paddingBottom: 58, gap: 14 },
  hero: { flexDirection: "row", alignItems: "center", gap: 12, padding: 16, borderRadius: 18, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line },
  heroIcon: { width: 48, height: 48, borderRadius: 15, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft },
  flex: { flex: 1, minWidth: 0, gap: 3 },
  title: { color: colors.ink, fontSize: 19, fontWeight: "800" },
  meta: { color: colors.muted, fontSize: 12, lineHeight: 18 },
  section: { padding: 16, gap: 13, borderRadius: 18, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line },
  sectionTitle: { color: colors.ink, fontSize: 17, fontWeight: "800" },
  row: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12 },
  field: { gap: 6 },
  label: { color: colors.ink, fontSize: 13, fontWeight: "700" },
  itemTitle: { color: colors.ink, fontWeight: "700" },
  metric: { color: colors.ink, fontWeight: "700", textAlign: "right" },
  input: { minHeight: 44, paddingHorizontal: 12, borderRadius: 12, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.background, color: colors.ink },
  prompt: { minHeight: 130, padding: 12, borderRadius: 12, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.background, color: colors.ink, textAlignVertical: "top" },
  notice: { gap: 8, padding: 12, borderRadius: 13, backgroundColor: colors.surfaceMuted },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip: { minHeight: 38, justifyContent: "center", paddingHorizontal: 12, borderRadius: 999, borderWidth: 1, borderColor: colors.line },
  smallChip: { minHeight: 34, justifyContent: "center", paddingHorizontal: 10, borderRadius: 999, borderWidth: 1, borderColor: colors.line },
  chipSelected: { borderColor: colors.accent, backgroundColor: colors.accentSoft },
  chipText: { color: colors.muted, fontSize: 12, fontWeight: "700" },
  chipTextSelected: { color: colors.accent, fontSize: 12, fontWeight: "800" },
  skillRow: { flexDirection: "row", alignItems: "center", gap: 7, paddingTop: 8, borderTopWidth: 1, borderTopColor: colors.line },
  numberGrid: { gap: 10 },
  numberField: { flex: 1, minWidth: 130, gap: 6 },
  secondary: { minHeight: 40, alignItems: "center", justifyContent: "center", paddingHorizontal: 12, borderRadius: 11, borderWidth: 1, borderColor: colors.accent },
  secondaryText: { color: colors.accent, fontWeight: "800", fontSize: 13 },
  primary: { minHeight: 52, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, borderRadius: 15, backgroundColor: colors.accent },
  primaryText: { color: colors.white, fontWeight: "800" },
  dangerButton: { minHeight: 48, alignItems: "center", justifyContent: "center", borderRadius: 14, borderWidth: 1, borderColor: colors.danger },
  dangerText: { color: colors.danger, fontWeight: "800" },
  warning: { color: colors.warning, fontSize: 12, lineHeight: 18 },
  impact: { color: colors.muted, fontSize: 12, lineHeight: 18, paddingHorizontal: 4 },
  message: { color: colors.ink, backgroundColor: colors.accentSoft, borderRadius: 13, padding: 13 },
  disabled: { opacity: 0.45 },
});
