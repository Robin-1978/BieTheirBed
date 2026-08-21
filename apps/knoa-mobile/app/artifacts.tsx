import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, RefreshControl, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";

import { AppPressable } from "@/components/AppPressable";
import { useI18n } from "@/i18n";
import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";

export default function ArtifactsScreen() {
  const gateway = useGateway();
  const { t } = useI18n();
  const [sessionHandle, setSessionHandle] = useState("");
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<Array<{ artifact_id: string; name: string; media_type: string; size: number; kind: string }>>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const search = useCallback(async () => {
    if (!gateway.client || !sessionHandle.trim()) return;
    setLoading(true);
    setError("");
    try {
      const result = await gateway.runAuthenticated((client) => client.searchArtifacts({ sessionHandle: sessionHandle.trim(), query }));
      setItems(result.artifacts);
    } catch {
      setError(t("artifacts.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [gateway.client, gateway.runAuthenticated, query, sessionHandle, t]);

  useEffect(() => {
    const firstSession = gateway.sessionHandle ?? "";
    if (firstSession && !sessionHandle) setSessionHandle(firstSession);
  }, [gateway.sessionHandle, sessionHandle]);

  return (
    <ScrollView contentContainerStyle={styles.container} refreshControl={<RefreshControl refreshing={loading} onRefresh={() => void search()} />}>
      <View style={styles.hero}><Text style={styles.title}>{t("artifacts.title")}</Text><Text style={styles.meta}>{t("artifacts.detail")}</Text></View>
      <TextInput value={sessionHandle} onChangeText={setSessionHandle} placeholder={t("artifacts.sessionPlaceholder")} placeholderTextColor={colors.muted} style={styles.input} autoCapitalize="none" />
      <View style={styles.searchRow}><TextInput value={query} onChangeText={setQuery} placeholder={t("artifacts.searchPlaceholder")} placeholderTextColor={colors.muted} style={[styles.input, styles.flex]} onSubmitEditing={() => void search()} /><AppPressable style={styles.button} onPress={() => void search()}><Text style={styles.buttonText}>{t("artifacts.search")}</Text></AppPressable></View>
      {loading ? <ActivityIndicator color={colors.accent} /> : null}
      {error ? <Text style={styles.error}>{error}</Text> : null}
      {!loading && !items.length ? <Text style={styles.empty}>{t("artifacts.empty")}</Text> : null}
      {items.map((item) => <View key={item.artifact_id} style={styles.card}><Text style={styles.name}>{item.name}</Text><Text style={styles.meta}>{item.kind} · {item.media_type} · {formatBytes(item.size)}</Text><Text style={styles.id}>{item.artifact_id}</Text></View>)}
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
});
