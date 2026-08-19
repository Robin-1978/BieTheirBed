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
import {
  BUILT_IN_AGENT_IDS,
  createKnoaAgent,
  csvValues,
  normalizeAgentId,
  removeNodeAgent,
  setDelegationEnabled,
  upsertNodeAgent,
} from "@/models/agentConfiguration";
import { cloneManagedConfig } from "@/models/modelConfiguration";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";

export default function AgentEditorScreen() {
  const params = useLocalSearchParams<{ agentId?: string; mode?: string }>();
  const gateway = useGateway();
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
        if (!existing) throw new Error("Agent 不存在");
        const copy = JSON.parse(JSON.stringify(existing)) as ManagedNodeAgent;
        setAgent(copy);
        setBuiltInPromptRef(copy.instructions_ref);
      } else {
        setAgent(createKnoaAgent(next.default_model, "New Knoa Agent"));
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Agent 加载失败");
    } finally {
      setWorking("");
    }
  }, [gateway.runAuthenticated, originalAgentId]);

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
    if (!validation.valid) throw new Error(validation.issues[0]?.message || "配置检查失败");
    const result = await gateway.runAuthenticated((client) => client.publishConfigDraft(
      replaced.draft_id,
      replaced.draft_version,
      summary,
    ));
    if (result.state.apply_status === "failed") throw new Error(result.state.apply_error_code || "配置应用失败");
    return result.revision.document;
  }

  async function save() {
    if (!document || !agent || working) return;
    setWorking("save");
    setMessage("");
    try {
      const next = upsertNodeAgent(document, agentId, agent, originalAgentId);
      await publish(next, originalAgentId ? `更新 Agent ${originalAgentId}` : `创建 Agent ${agentId}`);
      router.back();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Agent 保存失败");
    } finally {
      setWorking("");
    }
  }

  function confirmDelete() {
    if (!document || !originalAgentId || isBuiltIn || working) return;
    Alert.alert("删除 Agent", `删除 ${agent?.display_name || originalAgentId}？历史会话和 Task 不会被删除。`, [
      { text: "取消", style: "cancel" },
      {
        text: "删除",
        style: "destructive",
        onPress: () => {
          void (async () => {
            setWorking("delete");
            setMessage("");
            try {
              const next = removeNodeAgent(document, originalAgentId);
              await publish(next, `删除 Agent ${originalAgentId}`);
              router.back();
            } catch (error) {
              setMessage(error instanceof Error ? error.message : "Agent 删除失败");
            } finally {
              setWorking("");
            }
          })();
        },
      },
    ]);
  }

  if (!agent || !document) {
    return <View style={styles.center}>{working === "load" ? <ActivityIndicator color={colors.accent} /> : <Text style={styles.message}>{message || "无法打开 Agent"}</Text>}</View>;
  }

  const isSystem = agent.visibility === "system";
  const canChangeVisibility = !isSystem && originalAgentId !== document.agents.default_agent;
  return (
    <>
      <Stack.Screen options={{ title: originalAgentId ? agent.display_name : "新建 Agent" }} />
      <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
        <View style={styles.hero}>
          <View style={styles.heroIcon}><AppIcon name="agent" color={colors.accent} size={27} /></View>
          <View style={styles.flex}>
            <Text style={styles.title}>{agent.display_name}</Text>
            <Text style={styles.meta}>一个 NodeAgent 聚合 Prompt、模型、Skill、Tool ceiling、运行限制和 Subagent 策略；保存后只影响新的 Invocation。</Text>
          </View>
        </View>

        <Section title="身份与 Runtime">
          <Field label="Agent ID" value={agentId} editable={!originalAgentId} onChange={(value) => setAgentId(normalizeAgentId(value))} placeholder="research_agent" />
          <Field label="显示名称" value={agent.display_name} onChange={(display_name) => update((next) => { next.display_name = display_name; })} />
          <Metric label="Runtime" value={agent.kind === "codex" ? "Codex Runtime Adapter" : "Knoa Runtime"} />
          <Toggle label="启用" value={agent.enabled} disabled={originalAgentId === document.agents.default_agent} onChange={(enabled) => update((next) => { next.enabled = enabled; })} />
          {!isSystem ? (
            <Choice label="调用方式" value={agent.visibility} disabled={!canChangeVisibility} choices={[["user", "用户直接使用"], ["delegate", "仅作为 Subagent"]]} onChange={(visibility) => update((next) => { next.visibility = visibility as ManagedNodeAgent["visibility"]; })} />
          ) : <Metric label="调用方式" value="系统服务专用" />}
          <NumberField label="并发 Invocation" value={agent.max_concurrency} min={1} onChange={(max_concurrency) => update((next) => { next.max_concurrency = max_concurrency; })} />
        </Section>

        {agent.kind === "knoa" ? (
          <Section title="Prompt 与模型">
            {agent.instructions_ref ? (
              <View style={styles.notice}>
                <Text style={styles.itemTitle}>内置 Prompt</Text>
                <Text style={styles.meta}>{agent.instructions_ref}</Text>
                <AppPressable style={styles.secondary} onPress={() => update((next) => { next.instructions_ref = ""; next.instructions = `You are ${next.display_name}.`; })}><Text style={styles.secondaryText}>改为自定义 Prompt</Text></AppPressable>
              </View>
            ) : (
              <>
                <Text style={styles.label}>系统 Prompt</Text>
                <TextInput multiline value={agent.instructions} onChangeText={(instructions) => update((next) => { next.instructions = instructions; })} style={styles.prompt} placeholder="描述这个 Agent 的职责和行为" placeholderTextColor={colors.muted} />
                {builtInPromptRef ? <AppPressable style={styles.secondary} onPress={() => update((next) => { next.instructions = ""; next.instructions_ref = builtInPromptRef; })}><Text style={styles.secondaryText}>恢复内置 Prompt</Text></AppPressable> : null}
              </>
            )}
            <Text style={styles.label}>模型</Text>
            <View style={styles.chips}>
              {Object.entries(document.models).map(([alias, model]) => (
                <Chip key={alias} selected={agent.model_binding.model === alias} label={model.model || alias} onPress={() => update((next) => { next.model_binding = { ownership: "platform", model: alias, hint: "" }; })} />
              ))}
            </View>
          </Section>
        ) : (
          <Section title="Codex Runtime">
            <Choice label="Sandbox" value={agent.sandbox} choices={[["read-only", "只读"], ["workspace-write", "Workspace 可写"]]} onChange={(sandbox) => update((next) => {
              next.sandbox = sandbox;
              next.native_capability_ceiling = sandbox === "workspace-write"
                ? ["workspace_read", "workspace_write", "command_execution", "native_file_edit"]
                : ["workspace_read", "command_execution"];
            })} />
            <Field label="启动命令（逗号分隔）" value={agent.command.join(", ")} onChange={(value) => update((next) => { next.command = csvValues(value); })} />
            <Field label="工作目录" value={agent.cwd} onChange={(cwd) => update((next) => { next.cwd = cwd; })} />
          </Section>
        )}

        <Section title="Skill 与 Tool ceiling">
          <Text style={styles.meta}>Skill 是 Node 共享内容，Agent 这里只保存允许引用和默认注入。`*` 表示允许当前 Node 提供的全部 Platform Tool，最终仍受 Principal、Task 和 Capability Gateway 收窄。</Text>
          {Object.entries(document.skills).filter(([, skill]) => skill.enabled).map(([skillId]) => {
            const allowed = agent.allowed_skill_refs.includes(skillId);
            const selected = agent.default_skill_refs.includes(skillId);
            return (
              <View key={skillId} style={styles.skillRow}>
                <View style={styles.flex}><Text style={styles.itemTitle}>{skillId}</Text><Text style={styles.meta}>{selected ? "每次 Invocation 默认注入" : allowed ? "允许 Task 按需使用" : "未授权"}</Text></View>
                <AppPressable style={[styles.smallChip, allowed && styles.chipSelected]} onPress={() => update((next) => {
                  next.allowed_skill_refs = allowed ? next.allowed_skill_refs.filter((id) => id !== skillId) : [...next.allowed_skill_refs, skillId];
                  if (allowed) next.default_skill_refs = next.default_skill_refs.filter((id) => id !== skillId);
                })}><Text style={allowed ? styles.chipTextSelected : styles.chipText}>允许</Text></AppPressable>
                <AppPressable disabled={!allowed} style={[styles.smallChip, selected && styles.chipSelected, !allowed && styles.disabled]} onPress={() => update((next) => {
                  next.default_skill_refs = selected ? next.default_skill_refs.filter((id) => id !== skillId) : [...next.default_skill_refs, skillId];
                })}><Text style={selected ? styles.chipTextSelected : styles.chipText}>默认</Text></AppPressable>
              </View>
            );
          })}
          {!Object.keys(document.skills).length ? <Text style={styles.meta}>当前 Node 尚未导入 Skill。</Text> : null}
          <Field label="允许的 Platform Tool（逗号分隔）" value={agent.allowed_platform_tools.join(", ")} onChange={(value) => update((next) => { next.allowed_platform_tools = csvValues(value); })} placeholder="* 或 read_file, web_search" />
          <Field label="Capability ceiling（逗号分隔）" value={agent.platform_capability_ceiling.join(", ")} onChange={(value) => update((next) => { next.platform_capability_ceiling = csvValues(value); })} placeholder="* 或 host_read, network" />
        </Section>

        <Section title="Subagent 委派">
          <Toggle label="允许创建受治理 Child Task" value={agent.delegation.allowed} onChange={(allowed) => setAgent((current) => current ? setDelegationEnabled(current, allowed) : current)} />
          {agent.delegation.allowed ? (
            <>
              <Text style={styles.label}>允许的目标 Agent</Text>
              <View style={styles.chips}>
                {targetAgents.map(([targetId, target]) => {
                  const selected = agent.delegation.targets.includes(targetId);
                  return <Chip key={targetId} selected={selected} label={target.display_name} onPress={() => update((next) => { next.delegation.targets = selected ? next.delegation.targets.filter((id) => id !== targetId) : [...next.delegation.targets, targetId]; })} />;
                })}
              </View>
              {!targetAgents.length ? <Text style={styles.warning}>请先创建或启用一个“仅作为 Subagent”的 Agent。</Text> : null}
              <View style={styles.numberGrid}>
                <NumberField label="最大深度" value={agent.delegation.max_depth} min={1} onChange={(max_depth) => update((next) => { next.delegation.max_depth = max_depth; })} />
                <NumberField label="总 Child 数" value={agent.delegation.max_children} min={1} onChange={(max_children) => update((next) => { next.delegation.max_children = max_children; next.delegation.max_parallel_children = Math.min(next.delegation.max_parallel_children, max_children); })} />
                <NumberField label="并行 Child 数" value={agent.delegation.max_parallel_children} min={1} onChange={(max_parallel_children) => update((next) => { next.delegation.max_parallel_children = Math.min(max_parallel_children, next.delegation.max_children); })} />
                <NumberField label="Child 超时（秒）" value={agent.delegation.max_deadline_seconds} min={1} onChange={(max_deadline_seconds) => update((next) => { next.delegation.max_deadline_seconds = max_deadline_seconds; })} />
              </View>
            </>
          ) : <Text style={styles.meta}>关闭后 Runtime 不会向这个 Agent 暴露有效的委派目标和 Child Task 预算。</Text>}
        </Section>

        <Section title="运行限制">
          <OptionalNumberField label="最大推理迭代（留空继承 Node）" value={agent.runtime_limits.max_iterations} min={1} onChange={(max_iterations) => update((next) => { next.runtime_limits.max_iterations = max_iterations; })} />
          <OptionalNumberField label="最大输出 Token（留空继承 Node）" value={agent.runtime_limits.max_output_tokens} min={64} onChange={(max_output_tokens) => update((next) => { next.runtime_limits.max_output_tokens = max_output_tokens; })} />
        </Section>

        <AppPressable disabled={Boolean(working)} style={styles.primary} onPress={() => void save()}>
          {working === "save" ? <ActivityIndicator color={colors.white} /> : <><AppIcon name="check" color={colors.white} size={19} /><Text style={styles.primaryText}>校验、Preflight 并发布</Text></>}
        </AppPressable>
        {originalAgentId && !isBuiltIn ? <AppPressable disabled={Boolean(working)} style={styles.dangerButton} onPress={confirmDelete}><Text style={styles.dangerText}>删除这个 Agent</Text></AppPressable> : null}
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
  message: { color: colors.ink, backgroundColor: colors.accentSoft, borderRadius: 13, padding: 13 },
  disabled: { opacity: 0.45 },
});
