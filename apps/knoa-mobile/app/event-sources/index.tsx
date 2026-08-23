import * as Crypto from "expo-crypto";
import { router } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Alert, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";

import type { EventSource, EventSourceEvent, MCPResourceCatalogItem } from "@/api/models";
import { AppIcon } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { useI18n } from "@/i18n";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";

export default function EventSourcesScreen() {
  const gateway = useGateway();
  const { t } = useI18n();
  const [sources, setSources] = useState<EventSource[]>([]);
  const [mcpResources, setMcpResources] = useState<MCPResourceCatalogItem[]>([]);
  const [events, setEvents] = useState<Record<string, EventSourceEvent[]>>({});
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [kind, setKind] = useState<"webhook" | "mcp_resource">("webhook");
  const [title, setTitle] = useState("");
  const [goal, setGoal] = useState("");
  const [selectedResourceKey, setSelectedResourceKey] = useState("");
  const [oneTimeSecret, setOneTimeSecret] = useState("");

  const refresh = useCallback(async () => {
    if (!gateway.client) return;
    try {
      const [nextSources, nextResources] = await Promise.all([
        gateway.runAuthenticated((client) => client.listEventSources()),
        gateway.runAuthenticated((client) => client.listMcpResources()),
      ]);
      setSources(nextSources);
      setMcpResources(nextResources);
      setError("");
    } catch {
      setError(t("eventSources.loadFailed"));
    }
  }, [gateway.client, gateway.runAuthenticated, t]);

  useEffect(() => { void refresh(); }, [refresh]);

  async function create() {
    if (!title.trim() || !goal.trim() || busy) return;
    const selectedResource = mcpResources.find((item) => resourceKey(item) === selectedResourceKey);
    if (kind === "mcp_resource" && !selectedResource) return;
    setBusy("create");
    try {
      const source = await gateway.runAuthenticated((client) => client.createEventSource({
        clientRequestId: Crypto.randomUUID(), kind, title: title.trim(), goal: goal.trim(),
        agentId: gateway.defaultAgentId || undefined,
        mcpServerId: selectedResource?.server_id,
        resourceUriPrefix: selectedResource?.uri,
      }));
      setSources((current) => [source, ...current]);
      setOneTimeSecret(source.secret ?? "");
      setTitle(""); setGoal(""); setSelectedResourceKey("");
      setCreating(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("eventSources.createFailed"));
    } finally { setBusy(""); }
  }

  async function command(source: EventSource, action: "state" | "test" | "rotate" | "events" | "delete") {
    if (busy) return;
    setBusy(`${source.source_id}:${action}`);
    try {
      if (action === "state") {
        const updated = await gateway.runAuthenticated((client) => client.setEventSourceState(source.source_id, source.state === "active" ? "paused" : "active"));
        setSources((current) => current.map((item) => item.source_id === source.source_id ? updated : item));
      } else if (action === "test") {
        await gateway.runAuthenticated((client) => client.testEventSource(source.source_id));
        setEvents((current) => ({ ...current, [source.source_id]: [] }));
        await loadEvents(source.source_id);
      } else if (action === "rotate") {
        const result = await gateway.runAuthenticated((client) => client.rotateEventSourceSecret(source.source_id));
        setOneTimeSecret(result.secret);
        Alert.alert(t("eventSources.secretTitle"), t("eventSources.secretOnce"));
        await refresh();
      } else if (action === "events") {
        await loadEvents(source.source_id);
      } else {
        await gateway.runAuthenticated((client) => client.deleteEventSource(source.source_id));
        setSources((current) => current.filter((item) => item.source_id !== source.source_id));
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("eventSources.actionFailed"));
    } finally { setBusy(""); }
  }

  async function loadEvents(sourceId: string) {
    const list = await gateway.runAuthenticated((client) => client.eventSourceEvents(sourceId));
    setEvents((current) => ({ ...current, [sourceId]: list }));
  }

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <View style={styles.header}>
        <AppPressable onPress={() => router.back()} style={styles.iconButton}><AppIcon name="chevron-left" color={colors.ink} /></AppPressable>
        <View style={styles.headerCopy}><Text style={styles.heading}>{t("eventSources.title")}</Text><Text style={styles.hint}>{t("eventSources.description")}</Text></View>
        <AppPressable onPress={() => setCreating((value) => !value)} style={styles.primaryIcon}><AppIcon name="plus" color={colors.white} /></AppPressable>
      </View>
      {error ? <Text style={styles.error}>{error}</Text> : null}
      {oneTimeSecret ? <View style={styles.secret}><Text style={styles.cardTitle}>{t("eventSources.secretTitle")}</Text><Text selectable style={styles.secretValue}>{oneTimeSecret}</Text><Text style={styles.hint}>{t("eventSources.secretOnce")}</Text></View> : null}
      {creating ? (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>{t("eventSources.create")}</Text>
          <View style={styles.row}>
            <Choice selected={kind === "webhook"} label="Webhook" onPress={() => setKind("webhook")} />
            <Choice selected={kind === "mcp_resource"} label="MCP Resource" onPress={() => setKind("mcp_resource")} />
          </View>
          <TextInput value={title} onChangeText={setTitle} placeholder={t("eventSources.name")} placeholderTextColor={colors.muted} style={styles.input} />
          <TextInput multiline value={goal} onChangeText={setGoal} placeholder={t("eventSources.goal")} placeholderTextColor={colors.muted} style={[styles.input, styles.goal]} />
          {kind === "mcp_resource" ? (
            <View style={styles.resourceList}>
              <Text style={styles.label}>{t("eventSources.chooseResource")}</Text>
              {mcpResources.length ? mcpResources.map((resource) => (
                <ResourceChoice
                  key={resourceKey(resource)}
                  label={resource.name || t("eventSources.resourceFallback")}
                  detail={resource.description || resource.mime_type || t("eventSources.resourceReady")}
                  selected={selectedResourceKey === resourceKey(resource)}
                  onPress={() => setSelectedResourceKey(resourceKey(resource))}
                />
              )) : <Text style={styles.warning}>{t("eventSources.noResources")}</Text>}
            </View>
          ) : null}
          <AppPressable disabled={Boolean(busy) || !title.trim() || !goal.trim() || (kind === "mcp_resource" && !selectedResourceKey)} onPress={() => void create()} style={styles.primary}>{busy === "create" ? <ActivityIndicator color={colors.white} /> : <Text style={styles.primaryText}>{t("eventSources.createAction")}</Text>}</AppPressable>
        </View>
      ) : null}
      {sources.map((source) => (
        <View key={source.source_id} style={styles.card}>
          <View style={styles.between}><Text style={styles.cardTitle}>{source.display_name}</Text><Text style={styles.state}>{source.state === "active" ? t("eventSources.active") : t("eventSources.paused")}</Text></View>
          <Text style={styles.hint}>{source.kind === "webhook" ? "Webhook" : "MCP Resource"} · {t("eventSources.eventCount", { count: source.event_count })}</Text>
          {source.public_url ? <Text selectable style={styles.url}>{source.public_url}</Text> : source.kind === "webhook" ? <Text style={styles.warning}>{t("eventSources.selfHosted")}</Text> : null}
          <View style={styles.wrap}>
            <Small label={source.state === "active" ? t("eventSources.pause") : t("eventSources.resume")} onPress={() => void command(source, "state")} />
            <Small label={t("eventSources.test")} onPress={() => void command(source, "test")} />
            <Small label={t("eventSources.history")} onPress={() => void command(source, "events")} />
            {source.kind === "webhook" && source.route_id ? <Small label={t("eventSources.rotate")} onPress={() => void command(source, "rotate")} /> : null}
            <Small danger label={t("common.delete")} onPress={() => void command(source, "delete")} />
          </View>
          {events[source.source_id]?.map((event) => <Text key={event.trigger_event_id} style={styles.event}>{event.external_event_id} · {event.state}</Text>)}
        </View>
      ))}
    </ScrollView>
  );
}

function Choice({ selected, label, onPress }: { selected: boolean; label: string; onPress(): void }) { return <AppPressable onPress={onPress} style={[styles.choice, selected && styles.choiceActive]}><Text style={[styles.choiceText, selected && styles.choiceTextActive]}>{label}</Text></AppPressable>; }
function ResourceChoice({ selected, label, detail, onPress }: { selected: boolean; label: string; detail: string; onPress(): void }) { return <AppPressable accessibilityRole="radio" accessibilityState={{ checked: selected }} onPress={onPress} style={[styles.resourceChoice, selected && styles.resourceChoiceActive]}><Text style={[styles.cardTitle, selected && styles.resourceChoiceText]}>{label}</Text><Text numberOfLines={2} style={styles.hint}>{detail}</Text></AppPressable>; }
function Small({ label, onPress, danger = false }: { label: string; onPress(): void; danger?: boolean }) { return <AppPressable onPress={onPress} style={styles.small}><Text style={[styles.smallText, danger && styles.danger]}>{label}</Text></AppPressable>; }

function resourceKey(resource: MCPResourceCatalogItem): string {
  return `${resource.server_id}\n${resource.uri}`;
}

const styles = StyleSheet.create({
  container: { padding: 16, paddingBottom: 48, gap: 12 }, header: { flexDirection: "row", alignItems: "center", gap: 10 }, headerCopy: { flex: 1 }, heading: { color: colors.ink, fontSize: 23, fontWeight: "800" }, hint: { color: colors.muted, lineHeight: 19 }, iconButton: { width: 40, height: 40, alignItems: "center", justifyContent: "center" }, primaryIcon: { width: 42, height: 42, borderRadius: 14, backgroundColor: colors.accent, alignItems: "center", justifyContent: "center" }, card: { padding: 15, borderRadius: 16, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.surface, gap: 9 }, secret: { padding: 15, borderRadius: 16, backgroundColor: colors.warningSoft, gap: 7 }, secretValue: { color: colors.ink, fontFamily: "monospace" }, cardTitle: { color: colors.ink, fontWeight: "800", fontSize: 16 }, label: { color: colors.ink, fontWeight: "700" }, input: { minHeight: 44, borderWidth: 1, borderColor: colors.line, borderRadius: 11, padding: 11, color: colors.ink }, goal: { minHeight: 100, textAlignVertical: "top" }, row: { flexDirection: "row", gap: 8 }, between: { flexDirection: "row", justifyContent: "space-between", gap: 10 }, wrap: { flexDirection: "row", flexWrap: "wrap", gap: 7 }, resourceList: { gap: 7 }, resourceChoice: { padding: 11, borderRadius: 11, borderWidth: 1, borderColor: colors.line, gap: 3 }, resourceChoiceActive: { borderColor: colors.accent, backgroundColor: colors.accentSoft }, resourceChoiceText: { color: colors.accent }, choice: { paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: colors.line }, choiceActive: { borderColor: colors.accent, backgroundColor: colors.accentSoft }, choiceText: { color: colors.muted }, choiceTextActive: { color: colors.accent, fontWeight: "700" }, primary: { minHeight: 45, borderRadius: 12, backgroundColor: colors.accent, alignItems: "center", justifyContent: "center" }, primaryText: { color: colors.white, fontWeight: "800" }, small: { paddingHorizontal: 10, paddingVertical: 7, borderRadius: 10, borderWidth: 1, borderColor: colors.line }, smallText: { color: colors.accent, fontWeight: "700", fontSize: 12 }, danger: { color: colors.danger }, state: { color: colors.accent, fontWeight: "700" }, warning: { color: colors.warning }, url: { color: colors.ink, fontSize: 12 }, event: { color: colors.muted, fontSize: 12 }, error: { color: colors.danger },
});
