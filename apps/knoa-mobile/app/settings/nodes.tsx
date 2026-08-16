import { router } from "expo-router";
import { useEffect, useState } from "react";
import { ScrollView, StyleSheet, Text, TextInput, View } from "react-native";

import { AppPressable } from "@/components/AppPressable";
import { useGateway } from "@/state/GatewayProvider";
import {
  connectHub,
  createNodeEnrollmentGrant,
  listHubNodes,
  loadHubConnection,
  type HubNode,
} from "@/hub/hubClient";
import { colors } from "@/theme";

export default function NodeCenterScreen() {
  const gateway = useGateway();
  const [hubUrl, setHubUrl] = useState("");
  const [hubToken, setHubToken] = useState("");
  const [hubId, setHubId] = useState("");
  const [hubNodes, setHubNodes] = useState<HubNode[]>([]);
  const [message, setMessage] = useState("");
  const [nodeHub, setNodeHub] = useState<{ enrolled: boolean; relay_connected: boolean; last_error: string } | null>(null);

  useEffect(() => {
    void loadHubConnection().then(async (connection) => {
      if (!connection) return;
      setHubUrl(connection.url);
      setHubId(connection.hubId);
      setHubNodes(await listHubNodes());
    }).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (gateway.status !== "ready") return;
    void gateway.runAuthenticated((client) => client.hubStatus())
      .then(setNodeHub)
      .catch(() => undefined);
  }, [gateway.nodeId, gateway.status]);

  async function saveHub() {
    try {
      const connection = await connectHub(hubUrl, hubToken, "Knoa Mobile");
      setHubId(connection.hubId);
      setHubToken("");
      setHubNodes(await listHubNodes());
      setMessage("Hub 已连接，帐号令牌已写入安全存储");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Hub 连接失败");
    }
  }

  async function enrollCurrentNode() {
    try {
      const connection = await loadHubConnection();
      if (!connection) throw new Error("请先连接 Personal Hub");
      if (!gateway.nodeId) throw new Error("请先连接当前 Node");
      const grant = await createNodeEnrollmentGrant();
      await gateway.runAuthenticated((client) => client.enrollHub({
        hub_url: connection.url,
        hub_id: connection.hubId,
        hub_signing_public_key: connection.signingPublicKey,
        grant_id: grant.grant_id,
        grant_secret: grant.secret,
        challenge: grant.challenge,
        display_name: gateway.nodes.find((node) => node.nodeId === gateway.nodeId)?.displayName ?? "Knoa Node",
      }));
      setNodeHub(await gateway.runAuthenticated((client) => client.hubStatus()));
      setHubNodes(await listHubNodes());
      setMessage("当前 Node 已加入 Hub，outbound Relay 正在建立连接");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Node Hub enrollment 失败");
    }
  }
  return (
    <ScrollView contentContainerStyle={styles.container}>
      <View style={styles.section}>
        <Text style={styles.title}>Node Center</Text>
        <Text style={styles.hint}>AppInstallationIdentity 在本机唯一；每个 Node 保持独立绑定、会话、事件游标和固定公钥。</Text>
        {gateway.nodes.map((node) => {
          const active = node.nodeId === gateway.nodeId;
          return (
            <AppPressable key={node.nodeId} style={[styles.node, active && styles.active]} disabled={active} onPress={() => void gateway.switchNode(node.nodeId)}>
              <View style={styles.row}>
                <Text style={styles.nodeTitle}>{node.displayName}</Text>
                <Text style={active ? styles.activeLabel : styles.switchLabel}>{active ? "当前" : "切换"}</Text>
              </View>
              <Text style={styles.meta}>{node.nodeId}</Text>
              <Text style={styles.meta}>{node.gatewayUrl}</Text>
              <Text style={styles.key}>Pinned Ed25519 · {node.nodeSigningPublicKey.slice(0, 20)}…</Text>
            </AppPressable>
          );
        })}
        {!gateway.nodes.length ? <Text style={styles.hint}>尚未配对 Node。</Text> : null}
        <AppPressable style={styles.primary} onPress={() => router.push("/pair")}>
          <Text style={styles.primaryText}>扫描二维码添加 Node</Text>
        </AppPressable>
      </View>
      <View style={styles.section}>
        <Text style={styles.title}>Personal Hub</Text>
        <Text style={styles.hint}>可连接 Knoa Hosted 或同协议的自托管 Hub。App 优先 direct；direct 不可达时，经 Hub 不透明 Relay 与 Node 建立端到端加密会话。</Text>
        <TextInput value={hubUrl} onChangeText={setHubUrl} placeholder="https://hub.example.com" placeholderTextColor={colors.muted} autoCapitalize="none" style={styles.input} />
        <TextInput value={hubToken} onChangeText={setHubToken} placeholder="帐号令牌" placeholderTextColor={colors.muted} secureTextEntry autoCapitalize="none" style={styles.input} />
        <AppPressable style={styles.primary} onPress={() => void saveHub()}><Text style={styles.primaryText}>{hubId ? "更新 Hub 连接" : "连接 Hub"}</Text></AppPressable>
        {hubId ? <Text style={styles.key}>Hub · {hubId}</Text> : null}
        {hubId && gateway.nodeId ? (
          <AppPressable style={styles.primary} onPress={() => void enrollCurrentNode()}>
            <Text style={styles.primaryText}>{nodeHub?.enrolled ? "重新登记当前 Node" : "将当前 Node 加入 Hub"}</Text>
          </AppPressable>
        ) : null}
        {nodeHub?.enrolled ? (
          <Text style={styles.key}>Node Relay · {nodeHub.relay_connected ? "已连接" : nodeHub.last_error ? `重连中 (${nodeHub.last_error})` : "连接中"}</Text>
        ) : null}
        {message ? <Text style={styles.hint}>{message}</Text> : null}
        {hubNodes.map((node) => (
          <View key={node.node_id} style={styles.node}>
            <View style={styles.row}><Text style={styles.nodeTitle}>{node.display_name}</Text><Text style={node.online ? styles.activeLabel : styles.meta}>{node.online ? "在线" : "离线"}</Text></View>
            <Text style={styles.meta}>{node.node_id} · {node.platform} {node.version}</Text>
            <Text style={styles.key}>{gateway.nodes.some((item) => item.nodeId === node.node_id) ? "已建立本地信任绑定" : "需通过二维码或 owner 授权建立 NodeDeviceBinding"}</Text>
          </View>
        ))}
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 18, gap: 16, backgroundColor: colors.background },
  section: { backgroundColor: colors.surface, borderRadius: 18, padding: 16, gap: 12, borderWidth: 1, borderColor: colors.line },
  title: { color: colors.ink, fontSize: 19, fontWeight: "800" },
  hint: { color: colors.muted, fontSize: 13, lineHeight: 19 },
  node: { padding: 14, gap: 5, borderRadius: 14, backgroundColor: colors.background, borderWidth: 1, borderColor: colors.line },
  active: { borderColor: colors.accent },
  row: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  nodeTitle: { color: colors.ink, fontWeight: "800", fontSize: 16 },
  meta: { color: colors.muted, fontSize: 12 },
  key: { color: colors.accent, fontSize: 12 },
  activeLabel: { color: colors.accent, fontWeight: "800" },
  switchLabel: { color: colors.ink, fontWeight: "700" },
  primary: { backgroundColor: colors.accent, borderRadius: 13, padding: 14, alignItems: "center" },
  primaryText: { color: "#fff", fontWeight: "800" },
  input: { backgroundColor: colors.background, color: colors.ink, borderRadius: 12, paddingHorizontal: 13, paddingVertical: 12, borderWidth: 1, borderColor: colors.line },
});
