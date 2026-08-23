import * as Crypto from "expo-crypto";
import { router } from "expo-router";
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

import type { ManagedConfig } from "@/api/models";
import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import {
  listHubNodes,
  loadWorkspaceResourceState,
  type HubNode,
  type WorkspaceResourceState,
} from "@/hub/hubClient";
import {
  attachWorkspaceRemoteModel,
  deploymentForModel,
  providerEndpoint,
  setModelSharing,
  upsertModel,
  type ModelDriver,
  type ModelEditorValue,
} from "@/models/modelConfiguration";
import {
  availableWorkspaceModels,
  type AvailableWorkspaceModel,
  workspaceModelIdentity,
  workspaceModelSupportsVision,
} from "@/models/workspaceModelConsumption";
import { useGateway } from "@/state/GatewayProvider";
import { useI18n, type MessageKey } from "@/i18n";
import { colors } from "@/theme";
import { presentHubNodeName } from "@/presentation/nodePresentation";

type Editor = ModelEditorValue & { secret: string; originalAlias: string };

const emptyEditor = (): Editor => ({
  alias: "",
  originalAlias: "",
  providerId: "",
  driver: "openai_compatible",
  endpoint: "",
  modelId: "",
  secretRef: "",
  secretVersion: 0,
  secret: "",
  supportsVision: false,
  setAsDefault: false,
});

export default function ModelsScreen() {
  const gateway = useGateway();
  const { t } = useI18n();
  const [document, setDocument] = useState<ManagedConfig | null>(null);
  const [workspace, setWorkspace] = useState<WorkspaceResourceState | null>(null);
  const [nodes, setNodes] = useState<HubNode[]>([]);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [sharingAlias, setSharingAlias] = useState("");
  const [allowedNodeIds, setAllowedNodeIds] = useState<string[]>([]);
  const [concurrency, setConcurrency] = useState(1);
  const [working, setWorking] = useState("");
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    setWorking("load");
    setMessage("");
    try {
      const [current, resourceState, directory] = await Promise.all([
        gateway.runAuthenticated((client) => client.getConfigCurrent()),
        loadWorkspaceResourceState().catch(() => null),
        listHubNodes().catch(() => []),
      ]);
      setDocument(current.revision.document);
      setWorkspace(resourceState);
      setNodes(directory);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : t("settings.models.loadFailed"));
    } finally {
      setWorking("");
    }
  }, [gateway.runAuthenticated, t]);

  useEffect(() => { void load(); }, [load]);

  const nodeName = useMemo(
    () => presentHubNodeName(nodes.find((node) => node.node_id === gateway.nodeId), t("common.unnamedComputer")),
    [gateway.nodeId, nodes, t],
  );

  const availableRemoteModels = useMemo<AvailableWorkspaceModel[]>(() => {
    if (!document || !workspace || !gateway.nodeId) return [];
    return availableWorkspaceModels(document, workspace, nodes, gateway.nodeId);
  }, [document, gateway.nodeId, nodes, workspace]);

  function beginCreate() {
    const suffix = Crypto.randomUUID().replaceAll("-", "").slice(0, 10);
    setEditor({
      ...emptyEditor(),
      alias: `model_${suffix}`,
      providerId: `provider_${suffix}`,
      secretRef: `provider_${suffix}_key`,
    });
    setSharingAlias("");
  }

  function beginEdit(alias: string) {
    if (!document) return;
    const model = document.models[alias];
    if (!model) return;
    const provider = document.providers[model.provider];
    if (!provider) return;
    setEditor({
      alias,
      originalAlias: alias,
      providerId: model.provider,
      driver: provider.driver,
      endpoint: providerEndpoint(provider),
      modelId: model.model,
      secretRef: provider.api_key_ref,
      secretVersion: provider.secret_version,
      secret: "",
      supportsVision: Boolean(model.supports_vision),
      setAsDefault: document.default_model === alias,
    });
    setSharingAlias("");
  }

  async function applyDocument(next: ManagedConfig, summary: string) {
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
    setDocument(result.revision.document);
    return result.revision.document;
  }

  async function saveModel() {
    if (!document || !editor?.alias.trim() || !editor.providerId.trim()) return;
    setWorking("save");
    setMessage("");
    try {
      let secretVersion = editor.secretVersion;
      if (!["llamacpp", "workspace_remote"].includes(editor.driver) && editor.secret.trim()) {
        const status = await gateway.runAuthenticated((client) => client.writeSecret(editor.secretRef, editor.secret));
        secretVersion = Math.max(editor.secretVersion + 1, Math.ceil(status.rotated_at * 1000));
      }
      if (editor.originalAlias && editor.originalAlias !== editor.alias) {
        throw new Error(t("settings.models.aliasImmutable"));
      }
      const next = upsertModel(document, { ...editor, secretVersion });
      await applyDocument(next, editor.originalAlias ? t("settings.models.updateModel") : t("settings.models.addModelConfig"));
      setEditor(null);
      setMessage(t("settings.models.saveSuccess"));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : t("settings.models.saveFailed"));
    } finally {
      setWorking("");
    }
  }

  async function attachRemoteModel(item: AvailableWorkspaceModel) {
    if (!document) return;
    setWorking(`attach:${item.deployment.deployment_id}`);
    setMessage("");
    try {
      const digest = await Crypto.digestStringAsync(
        Crypto.CryptoDigestAlgorithm.SHA256,
        `workspace-model:${item.deployment.deployment_id}`,
      );
      const next = attachWorkspaceRemoteModel(document, {
        providerId: `remote_provider_${digest.slice(0, 24)}`,
        modelAlias: `remote_model_${digest.slice(0, 24)}`,
        deploymentId: item.deployment.deployment_id,
        displayName: item.resource.display_name,
        modelIdentity: workspaceModelIdentity(item.resource),
        supportsVision: workspaceModelSupportsVision(item.resource),
      });
      await applyDocument(next, t("settings.models.attachSummary", { name: item.resource.display_name }));
      Alert.alert(
        t("settings.models.attachSuccess"),
        t("settings.models.attachSuccessDetail", { name: item.resource.display_name, node: nodeName }),
        [
          { text: t("settings.models.attachLater") },
          { text: t("settings.common.configureAgents"), onPress: () => router.push("/settings/agents") },
        ],
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : t("settings.models.attachFailed"));
    } finally {
      setWorking("");
    }
  }

  function beginShare(alias: string) {
    if (!document) return;
    if (!workspace) {
      setMessage(t("settings.models.workspaceUnavailable"));
      return;
    }
    const deployment = deploymentForModel(document, alias)?.[1];
    const deploymentId = deploymentForModel(document, alias)?.[0];
    setAllowedNodeIds(deployment?.allowed_node_ids ?? []);
    setConcurrency(deployment?.max_remote_concurrency ?? 1);
    setSharingAlias(alias);
    setEditor(null);
  }

  async function saveSharing(enabled: boolean) {
    if (!document || !workspace || !gateway.nodeId || !sharingAlias) return;
    setWorking("share");
    setMessage("");
    try {
      const model = document.models[sharingAlias];
      if (!model) throw new Error(t("settings.models.notFound"));
      const provider = document.providers[model.provider];
      if (!provider) throw new Error(t("settings.models.providerNotFound"));
      const existing = deploymentForModel(document, sharingAlias);
      const suffix = Crypto.randomUUID().replaceAll("-", "").slice(0, 18);
      const deploymentId = existing?.[0] ?? `model_deployment_${suffix}`;
      const resourceId = existing?.[1].resource_id ?? `model_resource_${suffix}`;
      const displayName = model.model || sharingAlias;
      const next = setModelSharing(document, sharingAlias, {
        deploymentId,
        resourceId,
        displayName,
        enabled,
        maxRemoteConcurrency: concurrency,
        allowedNodeIds,
      });
      const applied = await applyDocument(next, enabled ? t("settings.models.shareEnabled") : t("settings.models.shareDisabled"));
      const state = await loadWorkspaceResourceState();
      setDocument(applied);
      setWorkspace(state);
      setSharingAlias("");
      setMessage(enabled ? t("settings.models.shareEnabledSuccess") : t("settings.models.shareDisabledSuccess"));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : t("settings.models.shareFailed"));
    } finally {
      setWorking("");
    }
  }

  return (
      <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
        <View style={styles.hero}>
          <View style={styles.heroIcon}><AppIcon name="agent" color={colors.accent} size={27} /></View>
          <View style={styles.flex}>
            <Text style={styles.title}>{t("settings.models.heroTitle", { node: nodeName })}</Text>
            <Text style={styles.hint}>{t("settings.models.heroDetail")}</Text>
          </View>
          <AppPressable accessibilityLabel={t("common.refresh")} onPress={() => void load()} style={styles.iconButton}><AppIcon name="refresh" color={colors.muted} size={20} /></AppPressable>
        </View>

        {working === "load" ? <ActivityIndicator color={colors.accent} /> : null}
        {availableRemoteModels.length ? <View style={styles.sectionHeader}><Text style={styles.sectionTitle}>{t("settings.models.workspaceAvailable")}</Text><Text style={styles.meta}>{t("settings.models.workspaceAvailableHint")}</Text></View> : null}
        {availableRemoteModels.map((item) => {
          const busy = working === `attach:${item.deployment.deployment_id}`;
          return (
            <View key={item.deployment.deployment_id} style={styles.card}>
              <View style={styles.row}>
                <View style={[styles.modelIcon, styles.modelIconShared]}><AppIcon name="share" color={colors.accent} size={23} /></View>
                <View style={styles.flex}>
                  <Text style={styles.cardTitle}>{item.resource.display_name}</Text>
                  <Text style={styles.meta}>{t("settings.models.fromNodeAuthorized", { node: presentHubNodeName(item.providerNode, t("common.unnamedComputer")) })}</Text>
                </View>
              </View>
              <Text style={item.health === "healthy" && item.providerNode?.online ? styles.healthy : styles.meta}>
                {item.providerNode?.online ? item.health === "healthy" ? t("settings.models.serviceHealthy") : t("settings.models.waitingServiceHealth") : t("settings.models.hostOfflineCanConfigure")}
              </Text>
              {item.attachedAlias ? (
                <View style={styles.actions}>
                  <Text style={styles.healthy}>{t("settings.models.attached")}</Text>
                  <AppPressable style={styles.secondary} onPress={() => router.push("/settings/agents")}><Text style={styles.secondaryText}>{t("settings.common.configureAgents")}</Text></AppPressable>
                </View>
              ) : (
                <AppPressable disabled={Boolean(working)} style={styles.primary} onPress={() => void attachRemoteModel(item)}>
                  {busy ? <ActivityIndicator color={colors.white} /> : <Text style={styles.primaryText}>{t("settings.models.addToNode", { node: nodeName })}</Text>}
                </AppPressable>
              )}
            </View>
          );
        })}
        {document ? <View style={styles.sectionHeader}><Text style={styles.sectionTitle}>{t("settings.models.configuredTitle", { node: nodeName })}</Text></View> : null}
        {document ? Object.entries(document.models).map(([alias, model]) => {
          const provider = document.providers[model.provider];
          if (!provider) return null;
          const deployment = deploymentForModel(document, alias);
          const shared = Boolean(deployment?.[1].share_enabled);
          const observation = workspace?.observations.find((item) => item.deployment_id === deployment?.[0]);
          return (
            <View key={alias} style={styles.card}>
              <View style={styles.row}>
                <View style={[styles.modelIcon, shared && styles.modelIconShared]}><AppIcon name="agent" color={colors.accent} size={23} /></View>
                <View style={styles.flex}>
                  <Text style={styles.cardTitle}>{model.model || alias}</Text>
                  <Text style={styles.meta}>{driverLabel(provider.driver, t)} · {providerEndpoint(provider) || t("settings.models.workspaceRemoteService")}</Text>
                </View>
                {document.default_model === alias ? <Text style={styles.badge}>{t("settings.common.defaultBadge")}</Text> : null}
              </View>
              <Text style={provider.driver === "workspace_remote" || shared ? styles.healthy : styles.meta}>
                {provider.driver === "workspace_remote" ? t("settings.models.fromWorkspaceRelay") : shared ? observation?.health === "healthy" ? t("settings.models.sharedHealthy") : t("settings.models.sharedWaiting") : t("settings.models.localOnly")}
              </Text>
              <View style={styles.actions}>
                {provider.driver === "workspace_remote" ? (
                  <AppPressable style={styles.secondary} onPress={() => router.push("/settings/agents")}><Text style={styles.secondaryText}>{t("settings.common.configureAgents")}</Text></AppPressable>
                ) : (
                  <>
                    <AppPressable style={styles.secondary} onPress={() => beginEdit(alias)}><Text style={styles.secondaryText}>{t("settings.common.edit")}</Text></AppPressable>
                    <AppPressable style={styles.secondary} onPress={() => beginShare(alias)}><Text style={styles.secondaryText}>{shared ? t("settings.models.manageShare") : t("settings.models.share")}</Text></AppPressable>
                  </>
                )}
              </View>
            </View>
          );
        }) : null}

        {!editor && !sharingAlias ? (
          <AppPressable style={styles.primary} onPress={beginCreate}>
            <AppIcon name="plus" color={colors.white} size={20} /><Text style={styles.primaryText}>{t("settings.models.addModel")}</Text>
          </AppPressable>
        ) : null}

        {editor ? <ModelEditor editor={editor} setEditor={setEditor} working={Boolean(working)} onSave={saveModel} onCancel={() => setEditor(null)} /> : null}
        {sharingAlias && document ? (
          <ShareEditor
            alias={sharingAlias}
            shared={Boolean(deploymentForModel(document, sharingAlias)?.[1].share_enabled)}
            nodes={nodes.filter((node) => node.node_id !== gateway.nodeId)}
            allowedNodeIds={allowedNodeIds}
            setAllowedNodeIds={setAllowedNodeIds}
            concurrency={concurrency}
            setConcurrency={setConcurrency}
            working={working === "share"}
            onSave={() => void saveSharing(true)}
            onStop={() => Alert.alert(t("settings.models.stopShareTitle"), t("settings.models.stopShareMessage"), [
              { text: t("common.cancel"), style: "cancel" },
              { text: t("settings.models.stopShareConfirm"), style: "destructive", onPress: () => void saveSharing(false) },
            ])}
            onCancel={() => setSharingAlias("")}
          />
        ) : null}
        {message ? <Text style={styles.message}>{message}</Text> : null}
      </ScrollView>
  );
}

function ModelEditor({ editor, setEditor, working, onSave, onCancel }: { editor: Editor; setEditor(value: Editor): void; working: boolean; onSave(): Promise<void>; onCancel(): void }) {
  const { t } = useI18n();
  const needsSecret = !["llamacpp", "workspace_remote"].includes(editor.driver);
  return (
    <View style={styles.editor}>
      <Text style={styles.title}>{editor.originalAlias ? t("settings.models.editModel") : t("settings.models.addModel")}</Text>
      <Text style={styles.label}>{t("settings.models.connectionType")}</Text>
      <View style={styles.choices}>{(["llamacpp", "openai_compatible", "openai", "anthropic"] as ModelDriver[]).map((driver) => <AppPressable key={driver} style={[styles.choice, editor.driver === driver && styles.choiceSelected]} onPress={() => setEditor({ ...editor, driver })}><Text style={editor.driver === driver ? styles.choiceTextSelected : styles.choiceText}>{driverLabel(driver, t)}</Text></AppPressable>)}</View>
      <Field label={t("settings.models.modelName")} value={editor.modelId} onChange={(modelId) => setEditor({ ...editor, modelId })} placeholder={t("settings.models.modelNamePlaceholder")} />
      <Field label={editor.driver === "llamacpp" ? t("settings.models.localEndpoint") : t("settings.models.apiEndpoint")} value={editor.endpoint} onChange={(endpoint) => setEditor({ ...editor, endpoint })} placeholder={editor.driver === "llamacpp" ? t("settings.models.localEndpointPlaceholder") : t("settings.models.apiEndpointPlaceholder")} />
      {needsSecret ? <Field label={t("settings.models.apiKey")} value={editor.secret} onChange={(secret) => setEditor({ ...editor, secret })} placeholder={editor.originalAlias ? t("settings.models.apiKeyKeepEmpty") : t("settings.models.apiKeyPlaceholder")} secure /> : null}
      <Toggle label={t("settings.models.supportsVision")} detail={t("settings.models.supportsVisionDetail")} value={editor.supportsVision} onChange={(supportsVision) => setEditor({ ...editor, supportsVision })} />
      <Toggle label={t("settings.models.setDefaultModel")} detail={t("settings.models.setDefaultModelDetail")} value={editor.setAsDefault} onChange={(setAsDefault) => setEditor({ ...editor, setAsDefault })} />
      <View style={styles.actions}><AppPressable style={styles.secondary} onPress={onCancel}><Text style={styles.secondaryText}>{t("common.cancel")}</Text></AppPressable><AppPressable disabled={working} style={styles.primarySmall} onPress={() => void onSave()}>{working ? <ActivityIndicator color={colors.white} /> : <Text style={styles.primaryText}>{t("settings.common.checkAndSave")}</Text>}</AppPressable></View>
    </View>
  );
}

function ShareEditor({ alias, shared, nodes, allowedNodeIds, setAllowedNodeIds, concurrency, setConcurrency, working, onSave, onStop, onCancel }: { alias: string; shared: boolean; nodes: HubNode[]; allowedNodeIds: string[]; setAllowedNodeIds(value: string[]): void; concurrency: number; setConcurrency(value: number): void; working: boolean; onSave(): void; onStop(): void; onCancel(): void }) {
  const { t } = useI18n();
  return (
    <View style={styles.editor}>
      <Text style={styles.title}>{shared ? t("settings.models.manageShareTitle") : t("settings.models.shareTitle")}</Text>
      <Text style={styles.hint}>{t("settings.models.shareHint", { alias })}</Text>
      {nodes.map((node) => <Toggle key={node.node_id} label={presentHubNodeName(node, t("common.unnamedComputer"))} detail={node.online ? t("nodes.online") : t("settings.models.offlineGrantHint")} value={allowedNodeIds.includes(node.node_id)} onChange={(enabled) => setAllowedNodeIds(enabled ? [...allowedNodeIds, node.node_id] : allowedNodeIds.filter((id) => id !== node.node_id))} />)}
      {!nodes.length ? <Text style={styles.meta}>{t("settings.models.noOtherNodes")}</Text> : null}
      <Text style={styles.label}>{t("settings.models.maxRemoteConcurrency")}</Text>
      <View style={styles.choices}>{[1, 2, 4].map((value) => <AppPressable key={value} style={[styles.choice, concurrency === value && styles.choiceSelected]} onPress={() => setConcurrency(value)}><Text style={concurrency === value ? styles.choiceTextSelected : styles.choiceText}>{value}</Text></AppPressable>)}</View>
      <View style={styles.actions}><AppPressable style={styles.secondary} onPress={onCancel}><Text style={styles.secondaryText}>{t("common.cancel")}</Text></AppPressable><AppPressable disabled={working} style={styles.primarySmall} onPress={onSave}>{working ? <ActivityIndicator color={colors.white} /> : <Text style={styles.primaryText}>{t("settings.models.saveShare")}</Text>}</AppPressable></View>
      {shared ? <AppPressable disabled={working} style={styles.dangerButton} onPress={onStop}><Text style={styles.dangerText}>{t("settings.models.stopShareConfirm")}</Text></AppPressable> : null}
    </View>
  );
}

function Field({ label, value, onChange, placeholder, secure = false }: { label: string; value: string; onChange(value: string): void; placeholder: string; secure?: boolean }) {
  return <View style={styles.field}><Text style={styles.label}>{label}</Text><TextInput value={value} onChangeText={onChange} placeholder={placeholder} placeholderTextColor={colors.muted} secureTextEntry={secure} autoCapitalize="none" style={styles.input} /></View>;
}

function Toggle({ label, detail, value, onChange }: { label: string; detail: string; value: boolean; onChange(value: boolean): void }) {
  return <View style={styles.toggle}><View style={styles.flex}><Text style={styles.label}>{label}</Text><Text style={styles.meta}>{detail}</Text></View><Switch value={value} onValueChange={onChange} /></View>;
}

const DRIVER_LABEL_KEYS: Record<ModelDriver, string> = {
  llamacpp: "settings.models.driver.llamacpp",
  openai_compatible: "settings.models.driver.openaiCompatible",
  openai: "settings.models.driver.openai",
  anthropic: "settings.models.driver.anthropic",
  workspace_remote: "settings.models.driver.workspaceRemote",
};

function driverLabel(driver: ModelDriver, t: ReturnType<typeof useI18n>["t"]): string {
  return t(DRIVER_LABEL_KEYS[driver] as MessageKey);
}

const styles = StyleSheet.create({
  container: { padding: 17, gap: 13, paddingBottom: 56 },
  hero: { flexDirection: "row", alignItems: "center", gap: 12, padding: 16, borderRadius: 18, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line },
  heroIcon: { width: 48, height: 48, borderRadius: 15, alignItems: "center", justifyContent: "center", backgroundColor: colors.accentSoft },
  iconButton: { width: 42, height: 42, alignItems: "center", justifyContent: "center" },
  title: { color: colors.ink, fontSize: 19, fontWeight: "800" },
  sectionHeader: { gap: 4, paddingHorizontal: 2, paddingTop: 3 },
  sectionTitle: { color: colors.ink, fontSize: 16, fontWeight: "800" },
  hint: { color: colors.muted, fontSize: 13, lineHeight: 19 },
  flex: { flex: 1, minWidth: 0 },
  card: { padding: 15, gap: 11, borderRadius: 17, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line },
  row: { flexDirection: "row", alignItems: "center", gap: 11 },
  modelIcon: { width: 44, height: 44, borderRadius: 14, alignItems: "center", justifyContent: "center", backgroundColor: colors.surfaceMuted },
  modelIconShared: { backgroundColor: colors.accentSoft },
  cardTitle: { color: colors.ink, fontSize: 16, fontWeight: "800" },
  meta: { color: colors.muted, fontSize: 12, lineHeight: 18 },
  badge: { color: colors.accent, backgroundColor: colors.accentSoft, borderRadius: 999, paddingHorizontal: 9, paddingVertical: 5, fontSize: 11, fontWeight: "800" },
  healthy: { color: colors.accent, fontSize: 12, fontWeight: "700" },
  actions: { flexDirection: "row", gap: 9 },
  primary: { minHeight: 48, flexDirection: "row", gap: 8, borderRadius: 13, backgroundColor: colors.accent, alignItems: "center", justifyContent: "center" },
  primarySmall: { flex: 1, minHeight: 46, borderRadius: 12, backgroundColor: colors.accent, alignItems: "center", justifyContent: "center" },
  primaryText: { color: colors.white, fontWeight: "800" },
  secondary: { flex: 1, minHeight: 42, borderRadius: 12, borderWidth: 1, borderColor: colors.accent, alignItems: "center", justifyContent: "center" },
  secondaryText: { color: colors.accent, fontWeight: "800" },
  editor: { padding: 16, gap: 12, borderRadius: 18, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.accent },
  choices: { flexDirection: "row", flexWrap: "wrap", gap: 7 },
  choice: { paddingHorizontal: 11, paddingVertical: 8, borderRadius: 999, borderWidth: 1, borderColor: colors.line },
  choiceSelected: { borderColor: colors.accent, backgroundColor: colors.accentSoft },
  choiceText: { color: colors.muted, fontSize: 12, fontWeight: "700" },
  choiceTextSelected: { color: colors.accent, fontSize: 12, fontWeight: "800" },
  field: { gap: 6 },
  label: { color: colors.ink, fontWeight: "700" },
  input: { minHeight: 46, borderRadius: 12, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.background, color: colors.ink, paddingHorizontal: 12, paddingVertical: 10 },
  toggle: { minHeight: 54, flexDirection: "row", alignItems: "center", gap: 12, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.line, paddingTop: 9 },
  dangerButton: { minHeight: 44, alignItems: "center", justifyContent: "center" },
  dangerText: { color: colors.danger, fontWeight: "800" },
  message: { color: colors.ink, backgroundColor: colors.accentSoft, borderRadius: 13, padding: 13, lineHeight: 19 },
});
