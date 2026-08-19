import * as Crypto from "expo-crypto";
import { router, Stack } from "expo-router";
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
import { publishWorkspaceModelShare } from "@/models/workspaceModelSharing";
import {
  availableWorkspaceModels,
  type AvailableWorkspaceModel,
  workspaceModelIdentity,
  workspaceModelSupportsVision,
} from "@/models/workspaceModelConsumption";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";

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
      setMessage(error instanceof Error ? error.message : "模型配置加载失败");
    } finally {
      setWorking("");
    }
  }, [gateway.runAuthenticated]);

  useEffect(() => { void load(); }, [load]);

  const nodeName = useMemo(
    () => nodes.find((node) => node.node_id === gateway.nodeId)?.display_name || "当前 Node",
    [gateway.nodeId, nodes],
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
    if (!validation.valid) throw new Error(validation.issues[0]?.message || "配置检查失败");
    const result = await gateway.runAuthenticated((client) => client.publishConfigDraft(
      replaced.draft_id,
      replaced.draft_version,
      summary,
    ));
    if (result.state.apply_status === "failed") throw new Error(result.state.apply_error_code || "配置应用失败");
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
        throw new Error("模型内部名称创建后不可修改；可以修改显示模型 ID 和连接配置");
      }
      const next = upsertModel(document, { ...editor, secretVersion });
      await applyDocument(next, editor.originalAlias ? "更新模型配置" : "添加模型配置");
      setEditor(null);
      setMessage("模型配置已检查并生效");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "模型保存失败");
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
      await applyDocument(next, `添加 Workspace 模型 ${item.resource.display_name}`);
      Alert.alert(
        "模型已添加",
        `${item.resource.display_name} 已加入 ${nodeName}，现在可以选择给哪个 Knoa Agent 使用。`,
        [
          { text: "稍后" },
          { text: "配置 Agent", onPress: () => router.push("/settings/agents") },
        ],
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Workspace 模型添加失败");
    } finally {
      setWorking("");
    }
  }

  function beginShare(alias: string) {
    if (!document) return;
    if (!workspace) {
      setMessage("当前 Workspace 控制面不可用，暂时不能修改共享设置");
      return;
    }
    const deployment = deploymentForModel(document, alias)?.[1];
    const deploymentId = deploymentForModel(document, alias)?.[0];
    const activeGrants = workspace?.grants.filter(
      (grant) => grant.target_deployment_id === deploymentId && grant.revoked_at === null,
    ) ?? [];
    setAllowedNodeIds(activeGrants.map((grant) => grant.caller_node_id));
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
      if (!model) throw new Error("模型不存在");
      const provider = document.providers[model.provider];
      if (!provider) throw new Error("模型 Provider 不存在");
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
      });
      const applied = await applyDocument(next, enabled ? "共享模型到 Workspace" : "停止共享模型");
      const state = await publishWorkspaceModelShare({
        state: workspace,
        nodeId: gateway.nodeId,
        resourceId,
        deploymentId,
        displayName,
        modelIdentity: model.model || sharingAlias,
        driver: provider.driver,
        supportsVision: Boolean(model.supports_vision),
        maxRemoteConcurrency: concurrency,
        allowedNodeIds,
        enabled,
      });
      setDocument(applied);
      setWorkspace(state);
      setSharingAlias("");
      setMessage(enabled ? "模型已共享到 Workspace" : "模型已停止共享");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "共享设置失败");
    } finally {
      setWorking("");
    }
  }

  return (
    <>
      <Stack.Screen options={{ title: "模型" }} />
      <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
        <View style={styles.hero}>
          <View style={styles.heroIcon}><AppIcon name="agent" color={colors.accent} size={27} /></View>
          <View style={styles.flex}>
            <Text style={styles.title}>{nodeName} 的模型</Text>
            <Text style={styles.hint}>模型默认只在当前 Node 使用。共享时，模型和密钥仍留在这里，Workspace 只管理授权。</Text>
          </View>
          <AppPressable accessibilityLabel="刷新" onPress={() => void load()} style={styles.iconButton}><AppIcon name="refresh" color={colors.muted} size={20} /></AppPressable>
        </View>

        {working === "load" ? <ActivityIndicator color={colors.accent} /> : null}
        {availableRemoteModels.length ? <View style={styles.sectionHeader}><Text style={styles.sectionTitle}>Workspace 可用模型</Text><Text style={styles.meta}>承载 Node 决定共享范围；是否添加、给哪个 Agent 使用由当前 Node 决定。</Text></View> : null}
        {availableRemoteModels.map((item) => {
          const busy = working === `attach:${item.deployment.deployment_id}`;
          return (
            <View key={item.deployment.deployment_id} style={styles.card}>
              <View style={styles.row}>
                <View style={[styles.modelIcon, styles.modelIconShared]}><AppIcon name="share" color={colors.accent} size={23} /></View>
                <View style={styles.flex}>
                  <Text style={styles.cardTitle}>{item.resource.display_name}</Text>
                  <Text style={styles.meta}>来自 {item.providerNode?.display_name || "另一个 Node"} · 已授权当前 Node</Text>
                </View>
              </View>
              <Text style={item.health === "healthy" && item.providerNode?.online ? styles.healthy : styles.meta}>
                {item.providerNode?.online ? item.health === "healthy" ? "服务健康" : "等待模型服务状态" : "承载 Node 当前离线，仍可先添加配置"}
              </Text>
              {item.attachedAlias ? (
                <View style={styles.actions}>
                  <Text style={styles.healthy}>已添加</Text>
                  <AppPressable style={styles.secondary} onPress={() => router.push("/settings/agents")}><Text style={styles.secondaryText}>配置 Agent</Text></AppPressable>
                </View>
              ) : (
                <AppPressable disabled={Boolean(working)} style={styles.primary} onPress={() => void attachRemoteModel(item)}>
                  {busy ? <ActivityIndicator color={colors.white} /> : <Text style={styles.primaryText}>添加到 {nodeName}</Text>}
                </AppPressable>
              )}
            </View>
          );
        })}
        {document ? <View style={styles.sectionHeader}><Text style={styles.sectionTitle}>{nodeName} 已配置模型</Text></View> : null}
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
                  <Text style={styles.meta}>{driverLabel(provider.driver)} · {providerEndpoint(provider) || "Workspace 远程服务"}</Text>
                </View>
                {document.default_model === alias ? <Text style={styles.badge}>默认</Text> : null}
              </View>
              <Text style={provider.driver === "workspace_remote" || shared ? styles.healthy : styles.meta}>
                {provider.driver === "workspace_remote" ? "来自 Workspace · 推理由承载 Node 执行" : shared ? `已共享 · ${observation?.health === "healthy" ? "健康" : "等待 Node 状态"}` : "仅当前 Node 使用"}
              </Text>
              <View style={styles.actions}>
                {provider.driver === "workspace_remote" ? (
                  <AppPressable style={styles.secondary} onPress={() => router.push("/settings/agents")}><Text style={styles.secondaryText}>配置 Agent</Text></AppPressable>
                ) : (
                  <>
                    <AppPressable style={styles.secondary} onPress={() => beginEdit(alias)}><Text style={styles.secondaryText}>编辑</Text></AppPressable>
                    <AppPressable style={styles.secondary} onPress={() => beginShare(alias)}><Text style={styles.secondaryText}>{shared ? "管理共享" : "共享"}</Text></AppPressable>
                  </>
                )}
              </View>
            </View>
          );
        }) : null}

        {!editor && !sharingAlias ? (
          <AppPressable style={styles.primary} onPress={beginCreate}>
            <AppIcon name="plus" color={colors.white} size={20} /><Text style={styles.primaryText}>添加模型</Text>
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
            onStop={() => Alert.alert("停止共享", "其他 Node 将不能继续调用这个模型。", [
              { text: "取消", style: "cancel" },
              { text: "停止共享", style: "destructive", onPress: () => void saveSharing(false) },
            ])}
            onCancel={() => setSharingAlias("")}
          />
        ) : null}
        {message ? <Text style={styles.message}>{message}</Text> : null}
      </ScrollView>
    </>
  );
}

function ModelEditor({ editor, setEditor, working, onSave, onCancel }: { editor: Editor; setEditor(value: Editor): void; working: boolean; onSave(): Promise<void>; onCancel(): void }) {
  const needsSecret = !["llamacpp", "workspace_remote"].includes(editor.driver);
  return (
    <View style={styles.editor}>
      <Text style={styles.title}>{editor.originalAlias ? "编辑模型" : "添加模型"}</Text>
      <Text style={styles.label}>连接类型</Text>
      <View style={styles.choices}>{(["llamacpp", "openai_compatible", "openai", "anthropic"] as ModelDriver[]).map((driver) => <AppPressable key={driver} style={[styles.choice, editor.driver === driver && styles.choiceSelected]} onPress={() => setEditor({ ...editor, driver })}><Text style={editor.driver === driver ? styles.choiceTextSelected : styles.choiceText}>{driverLabel(driver)}</Text></AppPressable>)}</View>
      <Field label="模型名称" value={editor.modelId} onChange={(modelId) => setEditor({ ...editor, modelId })} placeholder="例如 Qwen 3.5 4B" />
      <Field label={editor.driver === "llamacpp" ? "本地服务地址" : "API 地址"} value={editor.endpoint} onChange={(endpoint) => setEditor({ ...editor, endpoint })} placeholder={editor.driver === "llamacpp" ? "http://127.0.0.1:8192" : "https://api.example.com/v1"} />
      {needsSecret ? <Field label="API Key" value={editor.secret} onChange={(secret) => setEditor({ ...editor, secret })} placeholder={editor.originalAlias ? "留空表示不修改" : "输入 API Key"} secure /> : null}
      <Toggle label="支持图片" detail="模型能够接收图片输入" value={editor.supportsVision} onChange={(supportsVision) => setEditor({ ...editor, supportsVision })} />
      <Toggle label="设为默认模型" detail="只影响之后的新调用，不修改 Agent 的显式绑定" value={editor.setAsDefault} onChange={(setAsDefault) => setEditor({ ...editor, setAsDefault })} />
      <View style={styles.actions}><AppPressable style={styles.secondary} onPress={onCancel}><Text style={styles.secondaryText}>取消</Text></AppPressable><AppPressable disabled={working} style={styles.primarySmall} onPress={() => void onSave()}>{working ? <ActivityIndicator color={colors.white} /> : <Text style={styles.primaryText}>检查并保存</Text>}</AppPressable></View>
    </View>
  );
}

function ShareEditor({ alias, shared, nodes, allowedNodeIds, setAllowedNodeIds, concurrency, setConcurrency, working, onSave, onStop, onCancel }: { alias: string; shared: boolean; nodes: HubNode[]; allowedNodeIds: string[]; setAllowedNodeIds(value: string[]): void; concurrency: number; setConcurrency(value: number): void; working: boolean; onSave(): void; onStop(): void; onCancel(): void }) {
  return (
    <View style={styles.editor}>
      <Text style={styles.title}>{shared ? "管理模型共享" : "共享模型到 Workspace"}</Text>
      <Text style={styles.hint}>{alias} 仍在当前 Node 执行。请选择可以调用它的其他 Node。</Text>
      {nodes.map((node) => <Toggle key={node.node_id} label={node.display_name} detail={node.online ? "在线" : "离线，授权会在上线后生效"} value={allowedNodeIds.includes(node.node_id)} onChange={(enabled) => setAllowedNodeIds(enabled ? [...allowedNodeIds, node.node_id] : allowedNodeIds.filter((id) => id !== node.node_id))} />)}
      {!nodes.length ? <Text style={styles.meta}>Workspace 中还没有其他 Node。模型可以先发布，之后再补充授权。</Text> : null}
      <Text style={styles.label}>最大远程并发</Text>
      <View style={styles.choices}>{[1, 2, 4].map((value) => <AppPressable key={value} style={[styles.choice, concurrency === value && styles.choiceSelected]} onPress={() => setConcurrency(value)}><Text style={concurrency === value ? styles.choiceTextSelected : styles.choiceText}>{value}</Text></AppPressable>)}</View>
      <View style={styles.actions}><AppPressable style={styles.secondary} onPress={onCancel}><Text style={styles.secondaryText}>取消</Text></AppPressable><AppPressable disabled={working} style={styles.primarySmall} onPress={onSave}>{working ? <ActivityIndicator color={colors.white} /> : <Text style={styles.primaryText}>保存共享</Text>}</AppPressable></View>
      {shared ? <AppPressable disabled={working} style={styles.dangerButton} onPress={onStop}><Text style={styles.dangerText}>停止共享</Text></AppPressable> : null}
    </View>
  );
}

function Field({ label, value, onChange, placeholder, secure = false }: { label: string; value: string; onChange(value: string): void; placeholder: string; secure?: boolean }) {
  return <View style={styles.field}><Text style={styles.label}>{label}</Text><TextInput value={value} onChangeText={onChange} placeholder={placeholder} placeholderTextColor={colors.muted} secureTextEntry={secure} autoCapitalize="none" style={styles.input} /></View>;
}

function Toggle({ label, detail, value, onChange }: { label: string; detail: string; value: boolean; onChange(value: boolean): void }) {
  return <View style={styles.toggle}><View style={styles.flex}><Text style={styles.label}>{label}</Text><Text style={styles.meta}>{detail}</Text></View><Switch value={value} onValueChange={onChange} /></View>;
}

function driverLabel(driver: ModelDriver): string {
  return ({ llamacpp: "本地 llama.cpp", openai_compatible: "OpenAI 兼容 API", openai: "OpenAI", anthropic: "Anthropic", workspace_remote: "Workspace 共享模型" })[driver];
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
