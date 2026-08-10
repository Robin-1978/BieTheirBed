import { useEffect, useState } from "react";
import { ActivityIndicator, ScrollView, StyleSheet, Text, View } from "react-native";

import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";

type Extension = {
  extension_id: string;
  kind: "skill" | "mcp";
  state: string;
  detail: string;
  tools: string[];
};

type Descriptor = {
  name: string;
  origin_kind: string;
  extension_id: string;
  effect: string;
  risk: string;
  requires_confirmation: boolean;
};

export default function CapabilitiesScreen() {
  const gateway = useGateway();
  const [status, setStatus] = useState<Record<string, unknown> | null>(null);
  const [extensions, setExtensions] = useState<Extension[]>([]);
  const [tools, setTools] = useState<Descriptor[]>([]);
  const [audit, setAudit] = useState<Array<Record<string, unknown>>>([]);

  useEffect(() => {
    if (!gateway.client) return;
    void gateway.runAuthenticated((client) => Promise.all([
      client.runtimeStatus(gateway.sessionHandle),
      client.tools(gateway.sessionHandle),
      client.deviceAudit(),
    ])).then(([runtime, inventory, deviceAudit]) => {
      const runtimeResult = runtime.result as Record<string, unknown>;
      const inventoryResult = inventory.result as Record<string, unknown>;
      setStatus(runtimeResult.details as Record<string, unknown>);
      setExtensions((runtimeResult.extensions as Extension[]) ?? []);
      setTools((inventoryResult.descriptors as Descriptor[]) ?? []);
      setAudit((deviceAudit.events as Array<Record<string, unknown>>) ?? []);
    });
  }, [gateway.client, gateway.runAuthenticated, gateway.sessionHandle]);

  if (!status) {
    return <View style={styles.loading}><ActivityIndicator color={colors.accent} /></View>;
  }

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <Section title="运行状态">
        <Metric label="模型调用" value={status.model_calls} />
        <Metric label="工具调用" value={status.tool_calls} />
        <Metric label="Token" value={status.total_tokens} />
        <Metric label="缓存 Token" value={status.cached_tokens} />
      </Section>
      <Section title="Skill 与 MCP">
        {extensions.length ? extensions.map((extension) => (
          <View key={`${extension.kind}:${extension.extension_id}`} style={styles.item}>
            <Text style={styles.itemTitle}>{extension.extension_id}</Text>
            <Text style={styles.meta}>{extension.kind.toUpperCase()} · {extension.state}</Text>
            {extension.detail ? <Text style={styles.detail}>{extension.detail}</Text> : null}
          </View>
        )) : <Text style={styles.empty}>没有导入扩展</Text>}
      </Section>
      <Section title={`可用工具 · ${tools.length}`}>
        {tools.map((tool) => (
          <View key={tool.name} style={styles.item}>
            <Text style={styles.itemTitle}>{tool.name}</Text>
            <Text style={styles.meta}>{tool.origin_kind} · {tool.effect} · {tool.risk}</Text>
            {tool.requires_confirmation ? <Text style={styles.confirm}>执行前需要确认</Text> : null}
          </View>
        ))}
      </Section>
      <Section title="本设备审计">
        {audit.slice(-20).reverse().map((event) => (
          <View key={String(event.event_id)} style={styles.audit}>
            <Text style={styles.itemTitle}>{String(event.event_type)}</Text>
            <Text style={styles.meta}>{String(event.detail_code || "")}</Text>
          </View>
        ))}
      </Section>
    </ScrollView>
  );
}

function Section({ title, children }: React.PropsWithChildren<{ title: string }>) {
  return <View style={styles.section}><Text style={styles.sectionTitle}>{title}</Text>{children}</View>;
}

function Metric({ label, value }: { label: string; value: unknown }) {
  return <View style={styles.metric}><Text style={styles.meta}>{label}</Text><Text style={styles.metricValue}>{String(value ?? "—")}</Text></View>;
}

const styles = StyleSheet.create({
  loading: { flex: 1, alignItems: "center", justifyContent: "center" },
  container: { padding: 16, gap: 14, paddingBottom: 48 },
  section: { backgroundColor: colors.surface, borderRadius: 18, borderWidth: 1, borderColor: colors.line, padding: 16, gap: 10 },
  sectionTitle: { color: colors.ink, fontSize: 18, fontWeight: "700", marginBottom: 4 },
  metric: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  metricValue: { color: colors.ink, fontWeight: "700" },
  item: { borderTopWidth: 1, borderTopColor: colors.line, paddingTop: 10, gap: 3 },
  itemTitle: { color: colors.ink, fontWeight: "600" },
  meta: { color: colors.muted, fontSize: 13 },
  detail: { color: colors.ink },
  confirm: { color: colors.warning, fontSize: 13 },
  audit: { borderTopWidth: 1, borderTopColor: colors.line, paddingTop: 9 },
  empty: { color: colors.muted },
});
