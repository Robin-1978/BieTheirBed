import * as Application from "expo-application";
import * as Notifications from "expo-notifications";
import * as Linking from "expo-linking";
import { router } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { AppIcon } from "@/components/AppIcon";
import { useI18n, type LanguageMode } from "@/i18n";
import { useGateway } from "@/state/GatewayProvider";
import { useThemePreference, type ThemeMode } from "@/state/ThemeProvider";
import { registerPush, sendTestNotification } from "@/notifications";
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
  effect: string;
  risk: string;
  requires_confirmation: boolean;
};

type Diagnostic = {
  status: Record<string, unknown> | null;
  extensions: Extension[];
  tools: Descriptor[];
  audit: Array<Record<string, unknown>>;
};

export default function CapabilitiesScreen() {
  const gateway = useGateway();
  const theme = useThemePreference();
  const i18n = useI18n();
  const [diagnostic, setDiagnostic] = useState<Diagnostic>({
    status: null,
    extensions: [],
    tools: [],
    audit: [],
  });
  const [notificationStatus, setNotificationStatus] = useState("检查中");
  const [advanced, setAdvanced] = useState(false);
  const [working, setWorking] = useState("");
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    const permission = await Notifications.getPermissionsAsync();
    setNotificationStatus(permission.status === "granted" ? "已允许" : "未允许");
    if (!gateway.client || !gateway.sessionHandle) return;
    try {
      const [runtime, inventory, deviceAudit] = await gateway.runAuthenticated((client) => Promise.all([
        client.runtimeStatus(gateway.sessionHandle),
        client.tools(gateway.sessionHandle),
        client.deviceAudit(),
      ]));
      const runtimeResult = runtime.result as Record<string, unknown>;
      const inventoryResult = inventory.result as Record<string, unknown>;
      setDiagnostic({
        status: (runtimeResult.details as Record<string, unknown>) ?? {},
        extensions: (runtimeResult.extensions as Extension[]) ?? [],
        tools: (inventoryResult.descriptors as Descriptor[]) ?? [],
        audit: (deviceAudit.events as Array<Record<string, unknown>>) ?? [],
      });
    } catch {
      setMessage("诊断信息暂时无法读取，连接操作仍可使用");
    }
  }, [gateway.client, gateway.runAuthenticated, gateway.sessionHandle]);

  useEffect(() => {
    void load();
  }, [load]);

  async function runAction(name: string, action: () => Promise<void>, success: string) {
    if (working) return;
    setWorking(name);
    setMessage("");
    try {
      await action();
      setMessage(success);
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "操作失败，请稍后重试");
    } finally {
      setWorking("");
    }
  }

  function confirmRemoval() {
    Alert.alert(
      "移除此设备？",
      "服务端将撤销这台手机的访问权限，并清除本机连接。之后需要重新扫描二维码才能使用。",
      [
        { text: "取消", style: "cancel" },
        {
          text: "移除设备",
          style: "destructive",
          onPress: () => void runAction("remove", async () => {
            await gateway.removeConnection();
            router.replace("/pair");
          }, "设备已移除"),
        },
      ],
    );
  }

  const serviceLabel = gateway.status === "ready"
    ? "运行正常"
    : gateway.status === "booting"
      ? "正在连接"
      : gateway.status === "error"
        ? "连接异常"
        : "尚未配对";

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <Section title={i18n.t("settings.appearance")}>
        <Text style={styles.themeHint}>{i18n.t("settings.appearanceHint")}</Text>
        <View accessibilityRole="radiogroup" style={styles.themeChoices}>
          <ThemeChoice label={i18n.t("settings.theme.system")} mode="system" selected={theme.mode === "system"} onPress={theme.setMode} />
          <ThemeChoice label={i18n.t("settings.theme.light")} mode="light" selected={theme.mode === "light"} onPress={theme.setMode} />
          <ThemeChoice label={i18n.t("settings.theme.dark")} mode="dark" selected={theme.mode === "dark"} onPress={theme.setMode} />
        </View>
      </Section>

      <Section title={i18n.t("settings.language")}>
        <Text style={styles.themeHint}>{i18n.t("settings.languageHint")}</Text>
        <View accessibilityRole="radiogroup" style={styles.themeChoices}>
          <LanguageChoice label={i18n.t("settings.language.system")} mode="system" selected={i18n.mode === "system"} onPress={i18n.setMode} />
          <LanguageChoice label={i18n.t("settings.language.zh")} mode="zh-CN" selected={i18n.mode === "zh-CN"} onPress={i18n.setMode} />
          <LanguageChoice label={i18n.t("settings.language.en")} mode="en-US" selected={i18n.mode === "en-US"} onPress={i18n.setMode} />
        </View>
      </Section>

      <Section title="连接状态">
        <Metric label="小诺服务" value={serviceLabel} tone={gateway.status === "error" ? "danger" : "normal"} />
        <Metric label="服务地址" value={gateway.gatewayUrl || "—"} />
        <Metric label="设备 ID" value={gateway.deviceId || "—"} compact />
        <Metric label="最近连接" value={formatTime(gateway.lastConnectedAt)} />
        {gateway.error ? <Text style={styles.error}>{gateway.error}</Text> : null}
      </Section>

      <Section title="设备与应用">
        <Metric label="通知" value={notificationStatus} />
        <Metric label="应用版本" value={`${Application.nativeApplicationVersion ?? "开发版"} (${Application.nativeBuildVersion ?? "—"})`} />
        <Metric label="本地身份" value={gateway.deviceId ? "安全存储中" : "无"} />
        <Action
          label="启用或修复通知"
          detail="先说明用途，再由系统询问权限并注册此设备"
          busy={working === "notifications"}
          onPress={() => void runAction("notifications", async () => {
            if (!gateway.client) throw new Error("请先连接小诺");
            const enabled = await registerPush(gateway.client, true);
            if (!enabled) {
              const permission = await Notifications.getPermissionsAsync();
              if (!permission.canAskAgain) await Linking.openSettings();
              throw new Error("通知权限尚未允许");
            }
          }, "通知已启用")}
        />
        <Action
          label="发送测试通知"
          detail="立即在本机显示一条测试消息"
          busy={working === "test-notification"}
          onPress={() => void runAction("test-notification", sendTestNotification, "测试通知已发送")}
        />
      </Section>

      <Section title="连接操作">
        <Action
          label="重新连接"
          detail="网络恢复后重新检查服务"
          busy={working === "reconnect"}
          onPress={() => void runAction("reconnect", gateway.reconnect, "已重新连接")}
        />
        <Action
          label="重新认证"
          detail="重新建立安全会话，不改变配对关系"
          busy={working === "reauth"}
          onPress={() => void runAction("reauth", gateway.reauthenticate, "认证已更新")}
        />
        <Action
          label="重新配对"
          detail="连接另一台电脑或新的服务"
          onPress={() => router.push("/pair")}
        />
        <Action
          label="移除此设备"
          detail="撤销服务端权限并清除本机连接"
          danger
          busy={working === "remove"}
          onPress={confirmRemoval}
        />
        {message ? <Text style={styles.message}>{message}</Text> : null}
      </Section>

      <Pressable
        accessibilityRole="button"
        accessibilityLabel={advanced ? "收起高级诊断" : "展开高级诊断"}
        style={styles.advancedToggle}
        onPress={() => setAdvanced((value) => !value)}
      >
        <Text style={styles.advancedTitle}>高级诊断</Text>
        <Text style={styles.advancedHint}>{advanced ? "收起" : "查看工具、扩展和审计信息"}</Text>
      </Pressable>

      {advanced ? (
        <>
          <Section title="运行统计">
            {diagnostic.status ? (
              <>
                <Metric label="模型调用" value={diagnostic.status.model_calls} />
                <Metric label="工具调用" value={diagnostic.status.tool_calls} />
                <Metric label="Token" value={diagnostic.status.total_tokens} />
                <Metric label="缓存 Token" value={diagnostic.status.cached_tokens} />
              </>
            ) : <ActivityIndicator color={colors.accent} />}
          </Section>
          <Section title="Skill 与 MCP">
            {diagnostic.extensions.length ? diagnostic.extensions.map((extension) => (
              <View key={`${extension.kind}:${extension.extension_id}`} style={styles.item}>
                <Text style={styles.itemTitle}>{extension.extension_id}</Text>
                <Text style={styles.meta}>{extension.kind.toUpperCase()} · {extension.state}</Text>
                {extension.detail ? <Text style={styles.detail}>{extension.detail}</Text> : null}
              </View>
            )) : <Text style={styles.empty}>没有导入扩展</Text>}
          </Section>
          <Section title={`可用工具 · ${diagnostic.tools.length}`}>
            {diagnostic.tools.map((tool) => (
              <View key={tool.name} style={styles.item}>
                <Text style={styles.itemTitle}>{tool.name}</Text>
                <Text style={styles.meta}>{tool.origin_kind} · {tool.effect} · {tool.risk}</Text>
                {tool.requires_confirmation ? <Text style={styles.confirm}>执行前需要确认</Text> : null}
              </View>
            ))}
          </Section>
          <Section title="本设备审计">
            {diagnostic.audit.slice(-20).reverse().map((event) => (
              <View key={String(event.event_id)} style={styles.item}>
                <Text style={styles.itemTitle}>{String(event.event_type)}</Text>
                <Text style={styles.meta}>{String(event.detail_code || "")}</Text>
              </View>
            ))}
          </Section>
        </>
      ) : null}
    </ScrollView>
  );
}

function LanguageChoice({ label, mode, selected, onPress }: { label: string; mode: LanguageMode; selected: boolean; onPress(mode: LanguageMode): Promise<void> }) {
  return (
    <Pressable
      accessibilityRole="radio"
      accessibilityState={{ checked: selected }}
      onPress={() => void onPress(mode)}
      style={({ pressed }) => [styles.themeChoice, selected && styles.themeChoiceSelected, pressed && styles.pressed]}
    >
      <View style={[styles.radio, selected && styles.radioSelected]}>
        {selected ? <View style={styles.radioDot} /> : null}
      </View>
      <Text style={[styles.themeLabel, selected && styles.themeLabelSelected]}>{label}</Text>
    </Pressable>
  );
}

function ThemeChoice({ label, mode, selected, onPress }: { label: string; mode: ThemeMode; selected: boolean; onPress(mode: ThemeMode): Promise<void> }) {
  return (
    <Pressable
      accessibilityRole="radio"
      accessibilityState={{ checked: selected }}
      onPress={() => void onPress(mode)}
      style={({ pressed }) => [styles.themeChoice, selected && styles.themeChoiceSelected, pressed && styles.pressed]}
    >
      <View style={[styles.radio, selected && styles.radioSelected]}>
        {selected ? <View style={styles.radioDot} /> : null}
      </View>
      <Text style={[styles.themeLabel, selected && styles.themeLabelSelected]}>{label}</Text>
    </Pressable>
  );
}

function Section({ title, children }: React.PropsWithChildren<{ title: string }>) {
  return <View style={styles.section}><Text style={styles.sectionTitle}>{title}</Text>{children}</View>;
}

function Metric({ label, value, compact = false, tone = "normal" }: { label: string; value: unknown; compact?: boolean; tone?: "normal" | "danger" }) {
  return (
    <View style={styles.metric}>
      <Text style={styles.meta}>{label}</Text>
      <Text numberOfLines={compact ? 1 : 2} style={[styles.metricValue, compact && styles.compact, tone === "danger" && styles.error]}>{String(value ?? "—")}</Text>
    </View>
  );
}

function Action({ label, detail, onPress, danger = false, busy = false }: { label: string; detail: string; onPress(): void; danger?: boolean; busy?: boolean }) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      disabled={busy}
      style={({ pressed }) => [styles.action, pressed && styles.pressed]}
      onPress={onPress}
    >
      <View style={styles.actionCopy}>
        <Text style={[styles.actionTitle, danger && styles.danger]}>{label}</Text>
        <Text style={styles.meta}>{detail}</Text>
      </View>
      {busy ? <ActivityIndicator color={colors.accent} /> : <AppIcon name="chevron-right" color={danger ? colors.danger : colors.accent} size={19} />}
    </Pressable>
  );
}

function formatTime(timestamp: number): string {
  if (!timestamp) return "从未";
  return new Date(timestamp * 1000).toLocaleString("zh-CN", { hour12: false });
}

const styles = StyleSheet.create({
  container: { padding: 16, gap: 14, paddingBottom: 48 },
  section: { backgroundColor: colors.surface, borderRadius: 18, borderWidth: 1, borderColor: colors.line, padding: 16, gap: 12 },
  sectionTitle: { color: colors.ink, fontSize: 18, fontWeight: "700", marginBottom: 2 },
  themeHint: { color: colors.muted, fontSize: 13 },
  themeChoices: { flexDirection: "row", gap: 8 },
  themeChoice: { flex: 1, minHeight: 72, justifyContent: "center", alignItems: "center", gap: 8, borderRadius: 13, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.background },
  themeChoiceSelected: { borderColor: colors.accent, backgroundColor: colors.accentFaint },
  themeLabel: { color: colors.muted, fontSize: 13, fontWeight: "600", textAlign: "center" },
  themeLabelSelected: { color: colors.accent },
  radio: { width: 18, height: 18, borderRadius: 9, borderWidth: 1.5, borderColor: colors.lineStrong, alignItems: "center", justifyContent: "center" },
  radioSelected: { borderColor: colors.accent },
  radioDot: { width: 9, height: 9, borderRadius: 5, backgroundColor: colors.accent },
  metric: { flexDirection: "row", justifyContent: "space-between", alignItems: "flex-start", gap: 16 },
  metricValue: { color: colors.ink, fontWeight: "700", textAlign: "right", flexShrink: 1 },
  compact: { fontSize: 12 },
  action: { borderTopWidth: 1, borderTopColor: colors.line, paddingTop: 12, flexDirection: "row", alignItems: "center", gap: 12 },
  actionCopy: { flex: 1, gap: 3 },
  actionTitle: { color: colors.accent, fontWeight: "700", fontSize: 16 },
  pressed: { opacity: 0.65 },
  advancedToggle: { borderRadius: 18, borderWidth: 1, borderColor: colors.line, padding: 16, backgroundColor: colors.surface, gap: 4 },
  advancedTitle: { color: colors.ink, fontWeight: "700", fontSize: 16 },
  advancedHint: { color: colors.muted, fontSize: 13 },
  item: { borderTopWidth: 1, borderTopColor: colors.line, paddingTop: 10, gap: 3 },
  itemTitle: { color: colors.ink, fontWeight: "600" },
  meta: { color: colors.muted, fontSize: 13 },
  detail: { color: colors.ink },
  confirm: { color: colors.warning, fontSize: 13 },
  empty: { color: colors.muted },
  message: { color: colors.ink, lineHeight: 20 },
  error: { color: colors.danger },
  danger: { color: colors.danger },
});
