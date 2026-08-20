import { useCallback, useEffect, useState } from "react";
import { useLocalSearchParams } from "expo-router";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from "react-native";

import type {
  ConfigChange,
  ConfigControlState,
  ConfigDraft,
  ConfigGeneration,
  ConfigRevision,
  ConfigValidationResult,
  ManagedConfig,
} from "@/api/models";
import { AppIcon } from "@/components/AppIcon";
import { useI18n } from "@/i18n";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";

type Current = {
  revision: ConfigRevision;
  state: ConfigControlState;
  generations: ConfigGeneration[];
};

export default function SystemConfigurationScreen() {
  const params = useLocalSearchParams<{ draftId?: string }>();
  const gateway = useGateway();
  const { t } = useI18n();
  const [current, setCurrent] = useState<Current | null>(null);
  const [draft, setDraft] = useState<ConfigDraft | null>(null);
  const [validation, setValidation] = useState<ConfigValidationResult | null>(null);
  const [changes, setChanges] = useState<ConfigChange[]>([]);
  const [summary, setSummary] = useState("");
  const [working, setWorking] = useState("");
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    if (!gateway.client) return;
    setWorking("load");
    setMessage("");
    try {
      const [next, importedDraft] = await gateway.runAuthenticated((client) => Promise.all([
        client.getConfigCurrent(),
        params.draftId ? client.getConfigDraft(params.draftId) : Promise.resolve(null),
      ]));
      setCurrent(next);
      if (importedDraft) setDraft(importedDraft);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : t("config.loadFailed"));
    } finally {
      setWorking("");
    }
  }, [gateway.client, gateway.runAuthenticated, params.draftId, t]);

  useEffect(() => { void load(); }, [load]);

  async function beginDraft() {
    setWorking("draft");
    setMessage("");
    try {
      const next = await gateway.runAuthenticated((client) => client.createConfigDraft());
      setDraft(next);
      setValidation(null);
      setChanges([]);
      setSummary("");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : t("config.operationFailed"));
    } finally {
      setWorking("");
    }
  }

  function updateDocument(mutator: (document: ManagedConfig) => void) {
    setDraft((value) => {
      if (!value) return value;
      const document = JSON.parse(JSON.stringify(value.document)) as ManagedConfig;
      mutator(document);
      setValidation(null);
      return { ...value, document };
    });
  }

  async function saveDraft(): Promise<ConfigDraft | null> {
    if (!draft) return null;
    const saved = await gateway.runAuthenticated((client) => client.replaceConfigDraft(
      draft.draft_id,
      draft.document,
      draft.draft_version,
    ));
    setDraft(saved);
    return saved;
  }

  async function check(preflight: boolean) {
    if (!draft || working) return;
    setWorking(preflight ? "preflight" : "validate");
    setMessage("");
    try {
      const saved = await saveDraft();
      if (!saved) return;
      const result = await gateway.runAuthenticated((client) => client.validateConfigDraft(
        saved.draft_id,
        preflight,
      ));
      setValidation(result);
      setMessage(result.valid ? t(preflight ? "config.preflightPassed" : "config.validationPassed") : t("config.validationFailed"));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : t("config.operationFailed"));
    } finally {
      setWorking("");
    }
  }

  async function publish() {
    if (!draft || working) return;
    setWorking("publish");
    setMessage("");
    try {
      const saved = await saveDraft();
      if (!saved) return;
      const preflight = await gateway.runAuthenticated((client) => client.validateConfigDraft(saved.draft_id, true));
      setValidation(preflight);
      if (!preflight.valid) {
        setMessage(t("config.validationFailed"));
        return;
      }
      const result = await gateway.runAuthenticated((client) => client.publishConfigDraft(
        saved.draft_id,
        saved.draft_version,
        summary.trim(),
      ));
      const diff = await gateway.runAuthenticated((client) => client.getConfigDiff(
        saved.base_revision_id,
        result.revision.revision_id,
      ));
      setChanges(diff);
      setDraft(null);
      setMessage(result.state.apply_status === "failed"
        ? t("config.applyFailed", { code: result.state.apply_error_code })
        : t("config.published"));
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : t("config.operationFailed"));
    } finally {
      setWorking("");
    }
  }

  if (!current && working === "load") {
    return <View style={styles.center}><ActivityIndicator color={colors.accent} /></View>;
  }

  const document = draft?.document ?? current?.revision.document;
  return (
    <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
      <Section title={t("config.overview")}>
        <Metric label={t("config.appliedRevision")} value={shortId(current?.state.applied_revision_id ?? "")} />
        <Metric label={t("config.desiredRevision")} value={shortId(current?.state.desired_revision_id ?? "")} />
        <Metric label={t("config.applyStatus")} value={applyStatusLabel(current?.state.apply_status, t)} danger={current?.state.apply_status === "failed"} />
        <Metric label={t("config.defaultAgent")} value={document?.agents.default_agent ?? "—"} />
        <View style={styles.generationGrid}>
          {current?.generations.map((generation) => (
            <View key={generation.agent_id} style={styles.generation}>
              <Text style={styles.itemTitle}>{generation.agent_id}</Text>
              <Text style={styles.meta}>{generation.enabled ? t("config.active") : t("config.disabled")} · {shortId(generation.active_generation)}</Text>
              {generation.draining_generation ? <Text style={styles.warning}>{t("config.draining", { count: generation.draining_leases })}</Text> : null}
            </View>
          ))}
        </View>
        {!draft ? (
          <Action label={t("config.createDraft")} busy={working === "draft"} onPress={() => void beginDraft()} />
        ) : <Text style={styles.draftBadge}>{t("config.editingDraft", { version: draft.draft_version })}</Text>}
      </Section>

      {document ? (
        <>
          <Section title={t("config.agents")}>
            {Object.entries(document.agents.agents).map(([agentId, agent]) => {
              const isDefault = document.agents.default_agent === agentId;
              return (
                <View key={agentId} style={styles.item}>
                  <View style={styles.row}>
                    <View style={styles.flex}>
                      <Text style={styles.itemTitle}>{agent.display_name}</Text>
                      <Text style={styles.meta}>{agentId} · {agentKindLabel(agent.kind, t)} · {visibilityLabel(agent.visibility, t)}</Text>
                    </View>
                    <Switch
                      disabled={!draft || isDefault}
                      value={agent.enabled}
                      onValueChange={(enabled) => updateDocument((next) => {
                        const target = next.agents.agents[agentId];
                        if (target) target.enabled = enabled;
                      })}
                    />
                  </View>
                  <Pressable
                    disabled={!draft || isDefault || !agent.enabled}
                    onPress={() => updateDocument((next) => { next.agents.default_agent = agentId; })}
                    style={[styles.inlineButton, isDefault && styles.inlineButtonSelected]}
                  >
                    <Text style={[styles.inlineButtonText, isDefault && styles.inlineButtonTextSelected]}>
                      {isDefault ? t("config.currentDefault") : t("config.makeDefault")}
                    </Text>
                  </Pressable>
                  <TextInput
                    editable={Boolean(draft)}
                    multiline
                    placeholder={agent.instructions_ref || t("config.instructions")}
                    placeholderTextColor={colors.muted}
                    style={styles.instructions}
                    value={agent.instructions}
                    onChangeText={(instructions) => updateDocument((next) => {
                      const target = next.agents.agents[agentId];
                      if (target) {
                        target.instructions = instructions;
                        target.instructions_ref = instructions ? "" : agent.instructions_ref;
                      }
                    })}
                  />
                  <NumericField label={t("config.maxConcurrency")} value={agent.max_concurrency} disabled={!draft} onCommit={(value) => updateDocument((next) => { const target = next.agents.agents[agentId]; if (target) target.max_concurrency = value; })} />
                  {agent.kind === "codex" ? (
                    <ChoiceRow
                      label={t("config.sandboxBundle")}
                      value={agent.sandbox}
                      disabled={!draft}
                      choices={[["read-only", t("config.readOnly")], ["workspace-write", t("config.workspaceWrite")]]}
                      onChange={(sandbox) => updateDocument((next) => {
                        const target = next.agents.agents[agentId];
                        if (!target) return;
                        target.sandbox = sandbox;
                        target.native_capability_ceiling = sandbox === "workspace-write"
                          ? ["workspace_read", "workspace_write", "command_execution", "native_file_edit"]
                          : ["workspace_read", "command_execution"];
                      })}
                    />
                  ) : null}
                  {agent.delegation.allowed ? (
                    <View style={styles.fieldGrid}>
                      <NumericField label={t("config.maxChildren")} value={agent.delegation.max_children} disabled={!draft} onCommit={(value) => updateDocument((next) => { const target = next.agents.agents[agentId]; if (target) target.delegation.max_children = value; })} />
                      <NumericField label={t("config.maxParallelChildren")} value={agent.delegation.max_parallel_children} disabled={!draft} onCommit={(value) => updateDocument((next) => { const target = next.agents.agents[agentId]; if (target) target.delegation.max_parallel_children = value; })} />
                      <NumericField label={t("config.childDeadline")} value={agent.delegation.max_deadline_seconds} disabled={!draft} onCommit={(value) => updateDocument((next) => { const target = next.agents.agents[agentId]; if (target) target.delegation.max_deadline_seconds = value; })} />
                    </View>
                  ) : null}
                </View>
              );
            })}
          </Section>

          <Section title={t("config.modelsAndRuntimes")}>
            {Object.entries(document.models).map(([alias, model]) => (
              <View key={alias} style={styles.item}>
                <Text style={styles.itemTitle}>{alias}{document.default_model === alias ? ` · ${t("config.default")}` : ""}</Text>
                <Text style={styles.meta}>{model.provider} · {model.model || t("config.providerDefaultModel")}</Text>
              </View>
            ))}
          </Section>

          <Section title={t("config.approvalReview")}>
            <ChoiceRow label={t("config.reviewMode")} value={document.approval_review.mode} disabled={!draft} choices={[["off", t("config.reviewOff")], ["suggest", t("config.reviewSuggest")], ["auto", t("config.reviewAuto")]]} onChange={(value) => updateDocument((next) => { next.approval_review.mode = value as "off" | "suggest" | "auto"; const reviewer = next.agents.agents[next.approval_review.agent_id]; if (reviewer && value !== "off") reviewer.enabled = true; })} />
            <Metric label={t("config.reviewerAgent")} value={document.approval_review.agent_id} />
            <NumericField label={t("config.reviewTimeout")} value={document.approval_review.timeout_seconds} disabled={!draft} onCommit={(value) => updateDocument((next) => { next.approval_review.timeout_seconds = value; })} />
            <ChoiceRow label={t("config.autoMaxRisk")} value={document.approval_review.auto_max_risk} disabled={!draft} choices={[["low", t("config.riskLow")], ["medium", t("config.riskMedium")]]} onChange={(value) => updateDocument((next) => { next.approval_review.auto_max_risk = value as "low" | "medium"; })} />
          </Section>

          <Section title={t("config.operational")}>
            <View style={styles.fieldGrid}>
              <NumericField label={t("config.maxIterations")} value={document.operational.max_iterations} disabled={!draft} onCommit={(value) => updateDocument((next) => { next.operational.max_iterations = value; })} />
              <NumericField label={t("config.maxToolCalls")} value={document.operational.max_total_tool_calls} disabled={!draft} onCommit={(value) => updateDocument((next) => { next.operational.max_total_tool_calls = value; })} />
              <NumericField label={t("config.maxOutputTokens")} value={document.operational.max_output_tokens} disabled={!draft} onCommit={(value) => updateDocument((next) => { next.operational.max_output_tokens = value; })} />
              <NumericField label={t("config.contextBudget")} value={document.operational.context_window_budget} disabled={!draft} onCommit={(value) => updateDocument((next) => { next.operational.context_window_budget = value; })} />
              <NumericField label={t("config.drainSeconds")} value={document.operational.generation_drain_seconds} disabled={!draft} onCommit={(value) => updateDocument((next) => { next.operational.generation_drain_seconds = value; })} />
            </View>
          </Section>

          <Section title={t("config.skillsAndTools")}>
            {Object.entries(document.skills).map(([id, skill]) => (
              <ToggleRow key={`skill:${id}`} title={id} detail={t("config.skillDetail", { source: skill.source })} value={skill.enabled} disabled={!draft} onChange={(enabled) => updateDocument((next) => { const target = next.skills[id]; if (target) target.enabled = enabled; })} />
            ))}
            {Object.entries(document.mcp_servers).map(([id, server]) => (
              <ToggleRow key={`mcp:${id}`} title={id} detail={t("config.mcpDetail", { transport: server.transport })} value={server.enabled} disabled={!draft} onChange={(enabled) => updateDocument((next) => { const target = next.mcp_servers[id]; if (target) target.enabled = enabled; })} />
            ))}
            {!Object.keys(document.skills).length && !Object.keys(document.mcp_servers).length ? <Text style={styles.meta}>{t("config.noExtensions")}</Text> : null}
          </Section>
        </>
      ) : null}

      {draft ? (
        <Section title={t("config.publishWorkflow")}>
          <TextInput
            value={summary}
            onChangeText={setSummary}
            placeholder={t("config.summaryPlaceholder")}
            placeholderTextColor={colors.muted}
            style={styles.summaryInput}
          />
          <View style={styles.actions}>
            <Action label={t("config.validate")} busy={working === "validate"} onPress={() => void check(false)} />
            <Action label={t("config.preflight")} busy={working === "preflight"} onPress={() => void check(true)} />
            <Action label={t("config.publish")} busy={working === "publish"} primary onPress={() => void publish()} />
          </View>
          {validation?.issues.map((issue) => <Text key={`${issue.code}:${issue.path}`} style={styles.error}>{issue.path || "/"}: {issue.message}</Text>)}
          <Pressable onPress={() => { setDraft(null); setValidation(null); }}><Text style={styles.cancel}>{t("common.cancel")}</Text></Pressable>
        </Section>
      ) : null}

      {message ? <Text style={styles.message}>{message}</Text> : null}

      {changes.length ? (
        <Section title={t("config.lastDiff", { count: changes.length })}>
          {changes.slice(0, 30).map((change, index) => <Text key={`${change.path}:${index}`} style={styles.diff}>{change.op.toUpperCase()} {change.path}</Text>)}
        </Section>
      ) : null}

      <Section title={t("settings.system.effectiveSemanticsTitle")}>
        <Text style={styles.meta}>{t("settings.system.effectiveSemanticsDetail")}</Text>
      </Section>
    </ScrollView>
  );
}

function shortId(value: string) { return value ? value.slice(0, 12) : "—"; }

function agentKindLabel(kind: ManagedConfig["agents"]["agents"][string]["kind"], t: ReturnType<typeof useI18n>["t"]): string {
  return kind === "knoa" ? t("config.agentKindKnoa") : t("config.agentKindCodex");
}

function applyStatusLabel(status: ConfigControlState["apply_status"] | undefined, t: ReturnType<typeof useI18n>["t"]): string {
  if (!status) return "—";
  return ({
    idle: t("config.applyStatusIdle"),
    applying: t("config.applyStatusApplying"),
    failed: t("config.applyStatusFailed"),
  })[status];
}

function visibilityLabel(visibility: ManagedConfig["agents"]["agents"][string]["visibility"], t: ReturnType<typeof useI18n>["t"]): string {
  return ({
    user: t("settings.agents.visibilityUser"),
    delegate: t("settings.agents.visibilityDelegate"),
    system: t("settings.agents.visibilitySystem"),
  })[visibility] ?? visibility;
}

function Section({ title, children }: React.PropsWithChildren<{ title: string }>) {
  return <View style={styles.section}><Text style={styles.sectionTitle}>{title}</Text>{children}</View>;
}

function Metric({ label, value, danger = false }: { label: string; value: string; danger?: boolean }) {
  return <View style={styles.row}><Text style={styles.meta}>{label}</Text><Text style={[styles.metric, danger && styles.error]}>{value}</Text></View>;
}

function ToggleRow({ title, detail, value, disabled, onChange }: { title: string; detail: string; value: boolean; disabled: boolean; onChange(value: boolean): void }) {
  return <View style={[styles.item, styles.row]}><View style={styles.flex}><Text style={styles.itemTitle}>{title}</Text><Text style={styles.meta}>{detail}</Text></View><Switch disabled={disabled} value={value} onValueChange={onChange} /></View>;
}

function Action({ label, onPress, busy = false, primary = false }: { label: string; onPress(): void; busy?: boolean; primary?: boolean }) {
  return <Pressable disabled={busy} onPress={onPress} style={[styles.action, primary && styles.actionPrimary]}>{busy ? <ActivityIndicator color={primary ? colors.white : colors.accent} /> : <><Text style={[styles.actionText, primary && styles.actionTextPrimary]}>{label}</Text><AppIcon name="chevron-right" size={17} color={primary ? colors.white : colors.accent} /></>}</Pressable>;
}

function NumericField({ label, value, disabled, onCommit }: { label: string; value: number; disabled: boolean; onCommit(value: number): void }) {
  return <View style={styles.numericField}><Text style={styles.meta}>{label}</Text><TextInput key={String(value)} defaultValue={String(value)} editable={!disabled} keyboardType="numeric" style={styles.numericInput} onEndEditing={(event) => { const parsed = Number(event.nativeEvent.text); if (Number.isFinite(parsed) && parsed >= 0) onCommit(parsed); }} /></View>;
}

function ChoiceRow({ label, value, choices, disabled, onChange }: { label: string; value: string; choices: [string, string][]; disabled: boolean; onChange(value: string): void }) {
  return <View style={styles.choiceBlock}><Text style={styles.meta}>{label}</Text><View style={styles.choiceRow}>{choices.map(([id, title]) => <Pressable key={id} disabled={disabled} onPress={() => onChange(id)} style={[styles.choice, value === id && styles.choiceSelected]}><Text style={[styles.choiceText, value === id && styles.choiceTextSelected]}>{title}</Text></Pressable>)}</View></View>;
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: colors.background },
  container: { padding: 16, paddingBottom: 56, gap: 14 },
  section: { borderRadius: 18, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.surface, padding: 16, gap: 12 },
  sectionTitle: { color: colors.ink, fontSize: 18, fontWeight: "700" },
  row: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12 },
  flex: { flex: 1, gap: 3 },
  metric: { color: colors.ink, fontWeight: "700", textAlign: "right" },
  meta: { color: colors.muted, fontSize: 13 },
  item: { borderTopWidth: 1, borderTopColor: colors.line, paddingTop: 11, gap: 8 },
  itemTitle: { color: colors.ink, fontWeight: "700" },
  generationGrid: { gap: 8 },
  generation: { borderRadius: 12, backgroundColor: colors.surfaceMuted, padding: 11, gap: 3 },
  draftBadge: { color: colors.warning, fontWeight: "700" },
  warning: { color: colors.warning, fontSize: 12 },
  error: { color: colors.danger, lineHeight: 19 },
  message: { borderRadius: 14, backgroundColor: colors.accentSoft, color: colors.ink, padding: 13, lineHeight: 20 },
  inlineButton: { alignSelf: "flex-start", borderRadius: 999, borderWidth: 1, borderColor: colors.line, paddingHorizontal: 10, paddingVertical: 6 },
  inlineButtonSelected: { backgroundColor: colors.accentSoft, borderColor: colors.accent },
  inlineButtonText: { color: colors.muted, fontSize: 12, fontWeight: "600" },
  inlineButtonTextSelected: { color: colors.accent },
  instructions: { minHeight: 62, borderRadius: 12, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.background, color: colors.ink, padding: 10, textAlignVertical: "top", fontSize: 13 },
  summaryInput: { borderRadius: 12, borderWidth: 1, borderColor: colors.line, color: colors.ink, paddingHorizontal: 12, paddingVertical: 11 },
  actions: { gap: 8 },
  action: { minHeight: 44, borderRadius: 12, borderWidth: 1, borderColor: colors.accent, paddingHorizontal: 14, flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  actionPrimary: { backgroundColor: colors.accent },
  actionText: { color: colors.accent, fontWeight: "700" },
  actionTextPrimary: { color: colors.white },
  cancel: { color: colors.muted, textAlign: "center", padding: 8 },
  diff: { color: colors.ink, fontFamily: "monospace", fontSize: 12 },
  fieldGrid: { gap: 8 },
  numericField: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12 },
  numericInput: { minWidth: 100, borderRadius: 10, borderWidth: 1, borderColor: colors.line, color: colors.ink, paddingHorizontal: 10, paddingVertical: 7, textAlign: "right" },
  choiceBlock: { gap: 7 },
  choiceRow: { flexDirection: "row", flexWrap: "wrap", gap: 7 },
  choice: { borderRadius: 999, borderWidth: 1, borderColor: colors.line, paddingHorizontal: 11, paddingVertical: 7 },
  choiceSelected: { borderColor: colors.accent, backgroundColor: colors.accentSoft },
  choiceText: { color: colors.muted, fontSize: 12, fontWeight: "600" },
  choiceTextSelected: { color: colors.accent },
});
