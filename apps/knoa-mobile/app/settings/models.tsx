import { router } from "expo-router";
import * as Crypto from "expo-crypto";
import { useEffect, useState } from "react";
import { ActivityIndicator, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";

import type { ManagedConfig } from "@/api/models";
import { AppPressable } from "@/components/AppPressable";
import {
  loadWorkspaceResourceState,
  putWorkspaceModelDeployment,
  putWorkspaceModelResource,
  putWorkspaceResourceGrant,
  type WorkspaceResourceState,
} from "@/hub/hubClient";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";

type Driver = ManagedConfig["providers"][string]["driver"];

export default function ModelCenterScreen() {
  const gateway = useGateway();
  const [providerId, setProviderId] = useState("primary");
  const [driver, setDriver] = useState<Driver>("openai_compatible");
  const [endpoint, setEndpoint] = useState("");
  const [secretRef, setSecretRef] = useState("primary_api_key");
  const [secret, setSecret] = useState("");
  const [modelAlias, setModelAlias] = useState("primary_model");
  const [modelId, setModelId] = useState("");
  const [resourceId, setResourceId] = useState("personal_model");
  const [deploymentId, setDeploymentId] = useState("personal_model_deployment");
  const [callerNodeId, setCallerNodeId] = useState("");
  const [shareEnabled, setShareEnabled] = useState(false);
  const [workspaceState, setWorkspaceState] = useState<WorkspaceResourceState | null>(null);
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    void loadWorkspaceResourceState().then(setWorkspaceState).catch(() => undefined);
  }, []);

  async function createDraft() {
    if (!providerId.trim() || !modelAlias.trim()) return;
    setWorking(true);
    setMessage("");
    try {
      const draft = await gateway.runAuthenticated(async (client) => {
        let nextSecretVersion = 0;
        if (driver !== "workspace_remote" && secret.trim()) {
          const status = await client.writeSecret(secretRef.trim(), secret);
          nextSecretVersion = Math.ceil(status.rotated_at * 1000);
        }
        const created = await client.createConfigDraft();
        const document = JSON.parse(JSON.stringify(created.document)) as ManagedConfig;
        const existing = document.providers[providerId.trim()];
        document.providers[providerId.trim()] = {
          driver,
          server_url: driver === "llamacpp" ? endpoint.trim() : "",
          api_base: driver === "llamacpp" || driver === "workspace_remote" ? "" : endpoint.trim(),
          api_key_ref: driver === "llamacpp" || driver === "workspace_remote" ? "" : secretRef.trim(),
          api_key_env: "",
          remote_deployment_id: driver === "workspace_remote" ? deploymentId.trim() : "",
          direct_gateway_url: driver === "workspace_remote" ? endpoint.trim() : "",
          secret_version: nextSecretVersion
            ? Math.max(nextSecretVersion, (existing?.secret_version ?? 0) + 1)
            : existing?.secret_version ?? 0,
          requires_api_key: driver === "llamacpp" || driver === "workspace_remote" ? false : true,
          timeout_seconds: 120,
        };
        document.models[modelAlias.trim()] = {
          provider: providerId.trim(),
          model: modelId.trim(),
          supports_vision: null,
          context_window: null,
          thinking: null,
        };
        document.default_model = modelAlias.trim();
        if (driver !== "workspace_remote" && deploymentId.trim() && resourceId.trim()) {
          document.model_deployments[deploymentId.trim()] = {
            model_alias: modelAlias.trim(),
            resource_id: resourceId.trim(),
            display_name: modelId.trim() || modelAlias.trim(),
            enabled: true,
            share_enabled: shareEnabled,
            max_remote_concurrency: 1,
          };
        }
        for (const runtime of Object.values(document.agent_system.runtime_specs)) {
          if (runtime.implementation === "native" && runtime.model_binding.ownership === "platform") runtime.model_binding.model = modelAlias.trim();
        }
        return client.replaceConfigDraft(created.draft_id, document, created.draft_version);
      });
      setSecret("");
      router.push({ pathname: "/settings/system", params: { draftId: draft.draft_id } });
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "模型配置创建失败");
    } finally {
      setWorking(false);
    }
  }

  async function syncWorkspaceResource() {
    if (!gateway.nodeId || !resourceId.trim() || !deploymentId.trim() || !modelId.trim()) {
      setMessage("需要当前 Node、Resource ID、Deployment ID 和模型 ID");
      return;
    }
    setWorking(true);
    setMessage("");
    try {
      const material = JSON.stringify({
        resource_id: resourceId.trim(),
        revision: 1,
        driver,
        model_identity: modelId.trim(),
      });
      const digest = await Crypto.digestStringAsync(
        Crypto.CryptoDigestAlgorithm.SHA256,
        material,
      );
      await putWorkspaceModelResource({
        resource_id: resourceId.trim(),
        revision: 1,
        canonical_digest: digest,
        display_name: modelId.trim(),
        provider_protocol: driver === "anthropic" ? "anthropic" : "openai_compatible",
        model_identity: modelId.trim(),
        declared_capabilities: { streaming: true, tools: true },
      });
      await putWorkspaceModelDeployment({
        deployment_id: deploymentId.trim(),
        resource_id: resourceId.trim(),
        resource_revision: 1,
        target_node_id: gateway.nodeId,
        desired_revision: 1,
        enabled: true,
      });
      if (callerNodeId.trim()) {
        await putWorkspaceResourceGrant({
          grant_id: `grant_${callerNodeId.trim().slice(-16)}_${deploymentId.trim().slice(-16)}`,
          caller_node_id: callerNodeId.trim(),
          target_deployment_id: deploymentId.trim(),
          max_request_deadline: 600,
          expires_at: Date.now() / 1000 + 30 * 24 * 60 * 60,
        });
      }
      setWorkspaceState(await loadWorkspaceResourceState());
      setMessage("Workspace 资源、部署和授权已热更新；目标 Node 将自动发布健康观察");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Workspace 资源同步失败");
    } finally {
      setWorking(false);
    }
  }

  return (
    <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
      <View style={styles.section}>
        <Text style={styles.title}>Model Center</Text>
        <Text style={styles.hint}>Provider 管协议与凭据，Model 管模型 ID，Runtime 只绑定模型别名。Secret 写入后不可回显。</Text>
        <Text style={styles.label}>Provider driver</Text>
        <View style={styles.choices}>
          {(["openai_compatible", "openai", "anthropic", "llamacpp", "workspace_remote"] as Driver[]).map((value) => (
            <AppPressable key={value} style={[styles.choice, driver === value && styles.selected]} onPress={() => setDriver(value)}>
              <Text style={driver === value ? styles.selectedText : styles.choiceText}>{value}</Text>
            </AppPressable>
          ))}
        </View>
        <Field value={providerId} onChange={setProviderId} placeholder="Provider ID" />
        <Field value={endpoint} onChange={setEndpoint} placeholder={driver === "llamacpp" ? "http://127.0.0.1:8080" : driver === "workspace_remote" ? "可选 direct Gateway HTTPS URL" : "API Base URL"} />
        {driver !== "llamacpp" && driver !== "workspace_remote" ? <><Field value={secretRef} onChange={setSecretRef} placeholder="Secret reference" /><Field value={secret} onChange={setSecret} placeholder="API Key（写入后清空）" secure /></> : null}
        <Field value={modelAlias} onChange={setModelAlias} placeholder="Model alias" />
        <Field value={modelId} onChange={setModelId} placeholder="Provider model ID，可留空使用默认" />
        <Field value={resourceId} onChange={setResourceId} placeholder="Workspace Resource ID" />
        <Field value={deploymentId} onChange={setDeploymentId} placeholder={driver === "workspace_remote" ? "远程 Deployment ID" : "本机 Deployment ID"} />
        {driver !== "workspace_remote" ? (
          <View style={styles.choices}>
            <AppPressable style={[styles.choice, shareEnabled && styles.selected]} onPress={() => setShareEnabled(!shareEnabled)}>
              <Text style={shareEnabled ? styles.selectedText : styles.choiceText}>{shareEnabled ? "允许 Workspace 调用" : "仅本机使用"}</Text>
            </AppPressable>
          </View>
        ) : null}
        <AppPressable style={styles.primary} disabled={working} onPress={() => void createDraft()}>
          {working ? <ActivityIndicator color="#fff" /> : <Text style={styles.primaryText}>写入 Secret 并创建配置草稿</Text>}
        </AppPressable>
        {message ? <Text style={styles.error}>{message}</Text> : null}
      </View>
      <View style={styles.section}>
        <Text style={styles.title}>Workspace Resource Fabric</Text>
        <Text style={styles.hint}>共享定义与授权归 Workspace；模型凭据、数据和执行只留在目标 Node。填写 Caller Node 可同时授予该 Node 调用权。</Text>
        <Field value={callerNodeId} onChange={setCallerNodeId} placeholder="Caller Node ID（可选）" />
        <AppPressable style={styles.primary} disabled={working || driver === "workspace_remote"} onPress={() => void syncWorkspaceResource()}>
          <Text style={styles.primaryText}>同步本机部署到 Workspace</Text>
        </AppPressable>
        {workspaceState?.deployments.map((deployment) => {
          const observation = workspaceState.observations.find((item) => item.deployment_id === deployment.deployment_id);
          const grants = workspaceState.grants.filter((item) => item.target_deployment_id === deployment.deployment_id).length;
          return (
            <View key={deployment.deployment_id} style={styles.resource}>
              <Text style={styles.label}>{deployment.deployment_id}</Text>
              <Text style={styles.hint}>{deployment.resource_id} · Node {deployment.target_node_id}</Text>
              <Text style={observation?.health === "healthy" ? styles.healthy : styles.error}>
                {observation ? `${observation.health} · capacity ${observation.available_capacity}` : "等待目标 Node 观察"} · {grants} grants
              </Text>
            </View>
          );
        })}
      </View>
    </ScrollView>
  );
}

function Field({ value, onChange, placeholder, secure = false }: { value: string; onChange(value: string): void; placeholder: string; secure?: boolean }) {
  return <TextInput value={value} onChangeText={onChange} placeholder={placeholder} placeholderTextColor={colors.muted} secureTextEntry={secure} autoCapitalize="none" style={styles.input} />;
}

const styles = StyleSheet.create({
  container: { padding: 18, gap: 16, backgroundColor: colors.background },
  section: { backgroundColor: colors.surface, borderRadius: 18, padding: 16, gap: 12, borderWidth: 1, borderColor: colors.line },
  title: { color: colors.ink, fontSize: 19, fontWeight: "800" },
  hint: { color: colors.muted, fontSize: 13, lineHeight: 19 },
  label: { color: colors.ink, fontWeight: "700" },
  choices: { flexDirection: "row", gap: 8, flexWrap: "wrap" },
  choice: { paddingHorizontal: 10, paddingVertical: 8, borderRadius: 10, backgroundColor: colors.background },
  selected: { backgroundColor: colors.accent },
  choiceText: { color: colors.ink, fontWeight: "600" },
  selectedText: { color: "#fff", fontWeight: "800" },
  input: { backgroundColor: colors.background, color: colors.ink, borderRadius: 12, paddingHorizontal: 13, paddingVertical: 12, borderWidth: 1, borderColor: colors.line },
  primary: { minHeight: 48, backgroundColor: colors.accent, borderRadius: 13, alignItems: "center", justifyContent: "center" },
  primaryText: { color: "#fff", fontWeight: "800" },
  error: { color: colors.danger, fontSize: 13 },
  healthy: { color: colors.accent, fontSize: 13 },
  resource: { padding: 12, gap: 4, borderRadius: 12, backgroundColor: colors.background, borderWidth: 1, borderColor: colors.line },
});
