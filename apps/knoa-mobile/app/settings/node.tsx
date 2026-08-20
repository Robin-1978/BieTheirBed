import { router, Stack } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Alert, ScrollView, StyleSheet, Text, View } from "react-native";

import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { transportDetail, transportLabel } from "@/api/transportPresentation";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";

type RuntimeDiagnostic = {
  modelCalls: unknown;
  toolCalls: unknown;
  totalTokens: unknown;
};

export default function NodeSettingsScreen() {
  const gateway = useGateway();
  const [diagnostic, setDiagnostic] = useState<RuntimeDiagnostic | null>(null);
  const [working, setWorking] = useState("");
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    if (!gateway.sessionHandle) return;
    try {
      const runtime = await gateway.runAuthenticated((client) => client.runtimeStatus(gateway.sessionHandle));
      const details = (runtime.result as { details?: Record<string, unknown> }).details ?? {};
      setDiagnostic({ modelCalls: details.model_calls ?? 0, toolCalls: details.tool_calls ?? 0, totalTokens: details.total_tokens ?? 0 });
    } catch {
      setDiagnostic(null);
    }
  }, [gateway.runAuthenticated, gateway.sessionHandle]);

  useEffect(() => { void load(); }, [load]);

  async function run(name: string, operation: () => Promise<void>, success: string) {
    setWorking(name); setMessage("");
    try { await operation(); setMessage(success); await load(); }
    catch (error) { setMessage(error instanceof Error ? error.message : "操作失败"); }
    finally { setWorking(""); }
  }

  function removeBinding() {
    Alert.alert("解除 App 与 Node 的绑定", "之后需要重新配对才能访问这台 Node。不会删除 Node 上的数据。", [
      { text: "取消", style: "cancel" },
      { text: "解除绑定", style: "destructive", onPress: () => void run("remove", async () => { await gateway.removeConnection(); router.replace("/account"); }, "绑定已解除") },
    ]);
  }

  const node = gateway.nodes.find((item) => item.nodeId === gateway.nodeId);
  return (
    <>
      <Stack.Screen options={{ title: "Node 设置与诊断" }} />
      <ScrollView contentContainerStyle={styles.container}>
        <View style={styles.card}>
          <View style={styles.header}><View style={styles.icon}><AppIcon name="node" color={colors.accent} size={25} /></View><View style={styles.flex}><Text style={styles.title}>{node?.displayName || "当前 Node"}</Text><Text style={gateway.status === "ready" ? styles.online : styles.offline}>{gateway.status === "ready" ? `已连接 · ${transportLabel(gateway.transportMode)}` : "未连接"}</Text></View></View>
          {gateway.status === "ready" ? <Metric label="当前真实传输" value={transportLabel(gateway.transportMode)} /> : null}
          {gateway.status === "ready" ? <Text style={styles.transportDetail}>{transportDetail(gateway.transportMode)}</Text> : null}
          <Metric label="Gateway" value={gateway.gatewayUrl || "—"} />
          <Metric label="Node ID" value={gateway.nodeId || "—"} compact />
        </View>

        <View style={styles.card}>
          <Text style={styles.sectionTitle}>连接</Text>
          <Action title="重新连接" detail="重新选择 Direct/P2P 或 Relay 传输" busy={working === "reconnect"} onPress={() => void run("reconnect", gateway.reconnect, "Node 已重新连接")} />
          <Action title="重新认证" detail="刷新当前 App 的 Node 会话凭据" busy={working === "reauth"} onPress={() => void run("reauth", gateway.reauthenticate, "认证已刷新")} />
          <Action title="修复配对" detail="保留 Node，重新建立 App 信任" onPress={() => router.push("/pair")} />
        </View>

        <View style={styles.card}>
          <Text style={styles.sectionTitle}>运行概览</Text>
          {diagnostic ? <><Metric label="模型调用" value={diagnostic.modelCalls} /><Metric label="Tool 调用" value={diagnostic.toolCalls} /><Metric label="Token" value={diagnostic.totalTokens} /></> : <Text style={styles.meta}>暂时无法读取 Runtime 统计。</Text>}
          <Action title="高级系统配置" detail="Agent 策略、运行限制、草稿、预检与发布" onPress={() => router.push("/settings/system")} />
        </View>

        <AppPressable disabled={Boolean(working)} style={styles.remove} onPress={removeBinding}>{working === "remove" ? <ActivityIndicator color={colors.danger} /> : <Text style={styles.removeText}>解除 App 与 Node 的绑定</Text>}</AppPressable>
        {message ? <Text style={styles.message}>{message}</Text> : null}
      </ScrollView>
    </>
  );
}

function Metric({ label, value, compact = false }: { label: string; value: unknown; compact?: boolean }) { return <View style={styles.metric}><Text style={styles.meta}>{label}</Text><Text numberOfLines={compact ? 1 : 2} style={[styles.metricValue, compact && styles.compact]}>{String(value)}</Text></View>; }
function Action({ title, detail, busy = false, onPress }: { title: string; detail: string; busy?: boolean; onPress(): void }) { return <AppPressable disabled={busy} style={styles.action} onPress={onPress}><View style={styles.flex}><Text style={styles.actionTitle}>{title}</Text><Text style={styles.meta}>{detail}</Text></View>{busy ? <ActivityIndicator color={colors.accent} /> : <AppIcon name="chevron-right" color={colors.muted} size={18} />}</AppPressable>; }
const styles = StyleSheet.create({
  container: { padding: 17, gap: 13, paddingBottom: 52 }, card: { padding: 16, gap: 10, borderRadius: 18, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line }, header: { flexDirection: "row", alignItems: "center", gap: 12 }, icon: { width: 48, height: 48, alignItems: "center", justifyContent: "center", borderRadius: 15, backgroundColor: colors.accentSoft }, flex: { flex: 1, minWidth: 0 }, title: { color: colors.ink, fontSize: 19, fontWeight: "800" }, online: { color: colors.accent, fontSize: 12, fontWeight: "700" }, offline: { color: colors.muted, fontSize: 12, fontWeight: "700" }, sectionTitle: { color: colors.ink, fontSize: 17, fontWeight: "800" }, metric: { flexDirection: "row", justifyContent: "space-between", gap: 12, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.line, paddingTop: 10 }, meta: { color: colors.muted, fontSize: 12, lineHeight: 18 }, transportDetail: { color: colors.muted, fontSize: 12, lineHeight: 18 }, metricValue: { flex: 1, color: colors.ink, fontWeight: "700", textAlign: "right" }, compact: { fontFamily: "monospace", fontSize: 11 }, action: { minHeight: 62, flexDirection: "row", alignItems: "center", gap: 11, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.line }, actionTitle: { color: colors.ink, fontWeight: "800" }, remove: { minHeight: 48, alignItems: "center", justifyContent: "center" }, removeText: { color: colors.danger, fontWeight: "800" }, message: { color: colors.ink, backgroundColor: colors.accentSoft, borderRadius: 13, padding: 13 },
});
