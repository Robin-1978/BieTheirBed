import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { useEffect } from "react";

import { installNotificationNavigation } from "@/notifications";
import { GatewayProvider } from "@/state/GatewayProvider";
import { colors } from "@/theme";

export default function RootLayout() {
  useEffect(() => {
    const subscription = installNotificationNavigation();
    return () => subscription.remove();
  }, []);
  return (
    <GatewayProvider>
      <StatusBar style="dark" />
      <Stack
        screenOptions={{
          headerStyle: { backgroundColor: colors.background },
          headerTintColor: colors.ink,
          contentStyle: { backgroundColor: colors.background },
        }}
      >
        <Stack.Screen name="index" options={{ headerShown: false }} />
        <Stack.Screen name="pair" options={{ title: "连接小诺" }} />
        <Stack.Screen name="tasks/index" options={{ title: "任务" }} />
        <Stack.Screen name="tasks/[id]" options={{ title: "任务详情" }} />
        <Stack.Screen name="capabilities" options={{ title: "能力与连接" }} />
      </Stack>
    </GatewayProvider>
  );
}
