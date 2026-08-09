import { Redirect } from "expo-router";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";

import { useGateway } from "@/state/GatewayProvider";
import { colors } from "@/theme";

export default function Index() {
  const gateway = useGateway();
  if (gateway.status === "unpaired") return <Redirect href="/pair" />;
  if (gateway.status === "ready") return <Redirect href="/tasks" />;
  return (
    <View style={styles.container}>
      {gateway.status === "booting" ? <ActivityIndicator color={colors.accent} /> : null}
      <Text style={styles.title}>{gateway.status === "error" ? "暂时连接不上小诺" : "正在唤醒小诺"}</Text>
      {gateway.error ? <Text style={styles.detail}>{gateway.error}</Text> : null}
      {gateway.status === "error" ? (
        <Pressable style={styles.button} onPress={() => void gateway.reconnect()}>
          <Text style={styles.buttonText}>重新连接</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, alignItems: "center", justifyContent: "center", gap: 16, padding: 28 },
  title: { color: colors.ink, fontSize: 24, fontWeight: "600" },
  detail: { color: colors.muted, textAlign: "center" },
  button: { backgroundColor: colors.accent, paddingHorizontal: 22, paddingVertical: 12, borderRadius: 14 },
  buttonText: { color: "white", fontWeight: "600" },
});
