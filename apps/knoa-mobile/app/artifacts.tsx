import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, RefreshControl, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { File, Paths } from "expo-file-system";
import * as Sharing from "expo-sharing";
import { useLocalSearchParams } from "expo-router";

import { AppPressable } from "@/components/AppPressable";
import { useI18n } from "@/i18n";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";
import { saveArtifactFile } from "@/api/saveArtifactFile";
import { assistantArtifactItems, resolveAssistantArtifactFile } from "@/api/chatArtifacts";

export default function ArtifactsScreen() {
  const gateway = useGateway();
  const { t } = useI18n();
  const [sessionHandle, setSessionHandle] = useState("");
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<"" | "image" | "file">("");
  const [items, setItems] = useState<Array<{ artifact_id: string; name: string; media_type: string; size: number; kind: string }>>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [opening, setOpening] = useState("");
  const params = useLocalSearchParams<{ sessionHandle?: string }>();

  const search = useCallback(async () => {
    if (!gateway.client || !sessionHandle.trim()) return;
    setLoading(true);
    setError("");
    try {
      const result = await gateway.runAuthenticated((client) => client.searchArtifacts({ sessionHandle: sessionHandle.trim(), query, kind: kind || undefined }));
      setItems(result.artifacts);
    } catch {
      setError(t("artifacts.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [gateway.client, gateway.runAuthenticated, kind, query, sessionHandle, t]);

  useEffect(() => {
    const firstSession = params.sessionHandle?.trim() || gateway.sessionHandle || "";
    if (firstSession && !sessionHandle) setSessionHandle(firstSession);
  }, [gateway.sessionHandle, params.sessionHandle, sessionHandle]);

  async function openArtifact(item: (typeof items)[number], save = false) {
    if (!sessionHandle.trim() || opening) return;
    setOpening(item.artifact_id);
    setError("");
    try {
      const artifactItem = assistantArtifactItems([item])[0];
      if (!artifactItem) throw new Error("artifact_not_found");
      const resolved = await resolveAssistantArtifactFile({
        artifact: item,
        key: item.artifact_id,
        displayName: item.name,
        cacheFileName: artifactItem.cacheFileName,
        isImage: item.kind === "image",
      }, {
        cachedUri: (name) => {
          const file = new File(Paths.document, `artifact-${name}`);
          return file.exists ? file.uri : null;
        },
        download: (artifactId) => gateway.runAuthenticated((client) => client.downloadArtifact(sessionHandle.trim(), artifactId)),
        write: (name, bytes) => {
          const file = new File(Paths.document, `artifact-${name}`);
          file.create({ overwrite: true, intermediates: true });
          file.write(bytes);
          return file.uri;
        },
      });
      if (save) {
        await saveArtifactFile(resolved, {
          saveDialog: t("artifact.save"),
          saveToFile: t("artifact.saveToFile"),
          cancelled: t("artifact.saveCancelled"),
          saved: t("artifact.savedFile"),
        });
      } else {
        await Sharing.shareAsync(resolved.uri, { mimeType: resolved.mediaType });
      }
    } catch {
      setError(t("artifacts.openFailed"));
    } finally {
      setOpening("");
    }
  }

  return (
    <ScrollView contentContainerStyle={styles.container} refreshControl={<RefreshControl refreshing={loading} onRefresh={() => void search()} />}>
      <View style={styles.hero}><Text style={styles.title}>{t("artifacts.title")}</Text><Text style={styles.meta}>{t("artifacts.detail")}</Text></View>
      <TextInput value={sessionHandle} onChangeText={setSessionHandle} placeholder={t("artifacts.sessionPlaceholder")} placeholderTextColor={colors.muted} style={styles.input} autoCapitalize="none" />
      <View style={styles.filters}>
        {(["", "image", "file"] as const).map((value) => <AppPressable key={value || "all"} style={[styles.filter, kind === value && styles.filterActive]} onPress={() => setKind(value)}><Text style={[styles.filterText, kind === value && styles.filterTextActive]}>{value === "" ? t("artifacts.all") : value === "image" ? t("artifacts.images") : t("artifacts.files")}</Text></AppPressable>)}
      </View>
      <View style={styles.searchRow}><TextInput value={query} onChangeText={setQuery} placeholder={t("artifacts.searchPlaceholder")} placeholderTextColor={colors.muted} style={[styles.input, styles.flex]} onSubmitEditing={() => void search()} /><AppPressable style={styles.button} onPress={() => void search()}><Text style={styles.buttonText}>{t("artifacts.search")}</Text></AppPressable></View>
      {loading ? <ActivityIndicator color={colors.accent} /> : null}
      {error ? <Text style={styles.error}>{error}</Text> : null}
      {!loading && !items.length ? <Text style={styles.empty}>{t("artifacts.empty")}</Text> : null}
      {items.map((item) => <View key={item.artifact_id} style={styles.card}><Text style={styles.name}>{item.name}</Text><Text style={styles.meta}>{item.kind} · {item.media_type} · {formatBytes(item.size)}</Text><Text style={styles.id}>{item.artifact_id}</Text><View style={styles.actions}><AppPressable style={styles.action} disabled={opening === item.artifact_id} onPress={() => void openArtifact(item)}><Text style={styles.actionText}>{t("artifacts.open")}</Text></AppPressable><AppPressable style={styles.action} disabled={opening === item.artifact_id} onPress={() => void openArtifact(item, true)}><Text style={styles.actionText}>{t("artifacts.save")}</Text></AppPressable></View></View>)}
    </ScrollView>
  );
}

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

const styles = StyleSheet.create({
  container: { padding: 17, gap: 13, paddingBottom: 52 },
  hero: { padding: 16, gap: 5, borderRadius: 18, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line },
  title: { color: colors.ink, fontSize: 20, fontWeight: "800" },
  meta: { color: colors.muted, fontSize: 12, lineHeight: 18 },
  input: { minHeight: 44, borderWidth: 1, borderColor: colors.line, borderRadius: 12, paddingHorizontal: 12, color: colors.ink, backgroundColor: colors.surface },
  searchRow: { flexDirection: "row", gap: 8, alignItems: "center" },
  flex: { flex: 1 },
  button: { minHeight: 44, paddingHorizontal: 14, borderRadius: 12, justifyContent: "center", backgroundColor: colors.accent },
  buttonText: { color: colors.white, fontWeight: "800" },
  error: { color: colors.danger },
  empty: { color: colors.muted, textAlign: "center", padding: 22 },
  card: { padding: 14, borderRadius: 15, gap: 4, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.line },
  name: { color: colors.ink, fontWeight: "800" },
  id: { color: colors.muted, fontSize: 10 },
  filters: { flexDirection: "row", gap: 8 },
  filter: { minHeight: 36, paddingHorizontal: 12, justifyContent: "center", borderRadius: 18, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.surface },
  filterActive: { borderColor: colors.accent, backgroundColor: colors.accentSoft },
  filterText: { color: colors.muted, fontSize: 12, fontWeight: "700" },
  filterTextActive: { color: colors.accent },
  actions: { flexDirection: "row", gap: 8, marginTop: 4 },
  action: { minHeight: 36, paddingHorizontal: 12, justifyContent: "center", borderRadius: 10, borderWidth: 1, borderColor: colors.accent },
  actionText: { color: colors.accent, fontWeight: "800", fontSize: 12 },
});
