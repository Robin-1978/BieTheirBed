import { router, Stack } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, ScrollView, StyleSheet, Switch, Text, TextInput, View } from "react-native";

import { AppPressable } from "@/components/AppPressable";
import type { ManagedConfig } from "@/api/models";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";

export default function ExtensionCenterScreen() {
  const gateway = useGateway();
  const [kind, setKind] = useState<"skill" | "local_mcp" | "remote_mcp">("remote_mcp");
  const [source, setSource] = useState("");
  const [serverId, setServerId] = useState("");
  const [allowPrivate, setAllowPrivate] = useState(false);
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState("");
  const [document, setDocument] = useState<ManagedConfig | null>(null);

  const load = useCallback(async () => {
    try {
      const current = await gateway.runAuthenticated((client) => client.getConfigCurrent());
      setDocument(current.revision.document);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "能力加载失败");
    }
  }, [gateway.runAuthenticated]);

  useEffect(() => { void load(); }, [load]);

  async function inspectAndCreateDraft() {
    if (!source.trim() || (kind !== "skill" && !serverId.trim())) return;
    setWorking(true);
    setMessage("");
    try {
      const result = await gateway.runAuthenticated((client) => {
        if (kind === "skill") return client.importSkill(source.trim());
        if (kind === "local_mcp") return client.importLocalMcp(source.trim(), serverId.trim());
        return client.importRemoteMcp(serverId.trim(), source.trim(), allowPrivate);
      });
      router.push({ pathname: "/settings/system", params: { draftId: result.draft.draft_id } });
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "导入检查失败");
    } finally {
      setWorking(false);
    }
  }

  return (
    <>
    <Stack.Screen options={{ title: "MCP 与 Skill" }} />
    <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
      <View style={styles.section}>
        <Text style={styles.title}>当前 Node</Text>
        {Object.entries(document?.mcp_servers ?? {}).map(([id, server]) => <View key={`mcp:${id}`} style={styles.item}><View><Text style={styles.itemTitle}>{id}</Text><Text style={styles.hint}>MCP · {server.transport}</Text></View><Text style={server.enabled ? styles.enabled : styles.disabled}>{server.enabled ? "启用" : "停用"}</Text></View>)}
        {Object.entries(document?.skills ?? {}).map(([id, skill]) => <View key={`skill:${id}`} style={styles.item}><View><Text style={styles.itemTitle}>{id}</Text><Text style={styles.hint}>Skill · {skill.source || "已安装内容"}</Text></View><Text style={skill.enabled ? styles.enabled : styles.disabled}>{skill.enabled ? "启用" : "停用"}</Text></View>)}
        {!Object.keys(document?.mcp_servers ?? {}).length && !Object.keys(document?.skills ?? {}).length ? <Text style={styles.hint}>还没有安装 MCP 或 Skill。</Text> : null}
      </View>
      <View style={styles.section}>
        <Text style={styles.title}>添加能力</Text>
        <Text style={styles.hint}>Skill 是同步到当前 Node 的内容；MCP 是当前 Node 执行的服务。检查完成后仍需在配置草稿中预检并发布。</Text>
        <View style={styles.choices}>
          {(["remote_mcp", "local_mcp", "skill"] as const).map((value) => (
            <AppPressable key={value} style={[styles.choice, kind === value && styles.selected]} onPress={() => setKind(value)}>
              <Text style={kind === value ? styles.selectedText : styles.choiceText}>
                {value === "remote_mcp" ? "远程 MCP 服务" : value === "local_mcp" ? "本地 MCP 服务" : "Skill 内容"}
              </Text>
            </AppPressable>
          ))}
        </View>
        {kind !== "skill" ? (
          <TextInput value={serverId} onChangeText={setServerId} placeholder="扩展 ID，例如 github" placeholderTextColor={colors.muted} style={styles.input} autoCapitalize="none" />
        ) : null}
        <TextInput
          value={source}
          onChangeText={setSource}
          placeholder={kind === "remote_mcp" ? "https://mcp.example.com/mcp" : "Node 上的内容目录绝对路径"}
          placeholderTextColor={colors.muted}
          style={styles.input}
          autoCapitalize="none"
        />
        {kind === "remote_mcp" ? (
          <View style={styles.row}><Text style={styles.label}>允许显式局域网目标</Text><Switch value={allowPrivate} onValueChange={setAllowPrivate} /></View>
        ) : null}
        <AppPressable style={styles.primary} disabled={working} onPress={() => void inspectAndCreateDraft()}>
          {working ? <ActivityIndicator color="#fff" /> : <Text style={styles.primaryText}>检查权限并创建草稿</Text>}
        </AppPressable>
        {message ? <Text style={styles.error}>{message}</Text> : null}
      </View>
    </ScrollView>
    </>
  );
}

const styles = StyleSheet.create({
  container: { padding: 18, gap: 16, backgroundColor: colors.background },
  section: { backgroundColor: colors.surface, borderRadius: 18, padding: 16, gap: 12, borderWidth: 1, borderColor: colors.line },
  title: { color: colors.ink, fontSize: 18, fontWeight: "800" },
  hint: { color: colors.muted, fontSize: 13, lineHeight: 19 },
  choices: { flexDirection: "row", gap: 8, flexWrap: "wrap" },
  choice: { paddingHorizontal: 12, paddingVertical: 9, borderRadius: 12, backgroundColor: colors.background },
  selected: { backgroundColor: colors.accent },
  choiceText: { color: colors.ink, fontWeight: "700" },
  selectedText: { color: "#fff", fontWeight: "800" },
  input: { backgroundColor: colors.background, color: colors.ink, borderRadius: 12, paddingHorizontal: 13, paddingVertical: 12, borderWidth: 1, borderColor: colors.line },
  row: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  label: { color: colors.ink, fontWeight: "600" },
  primary: { minHeight: 46, backgroundColor: colors.accent, borderRadius: 13, alignItems: "center", justifyContent: "center" },
  primaryText: { color: "#fff", fontWeight: "800" },
  error: { color: colors.danger, fontSize: 13 },
  item: { minHeight: 58, flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.line },
  itemTitle: { color: colors.ink, fontWeight: "800" },
  enabled: { color: colors.accent, fontSize: 12, fontWeight: "800" },
  disabled: { color: colors.muted, fontSize: 12, fontWeight: "700" },
});
