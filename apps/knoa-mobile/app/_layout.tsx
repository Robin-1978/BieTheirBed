import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { useEffect } from "react";
import { KeyboardProvider } from "react-native-keyboard-controller";

import { installNotificationNavigation } from "@/notifications";
import { GatewayProvider } from "@/state/GatewayProvider";
import { colors } from "@/theme";

export default function RootLayout() {
  useEffect(() => {
    const subscription = installNotificationNavigation();
    return () => subscription.remove();
  }, []);
  return (
    <KeyboardProvider>
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
          <Stack.Screen name="chat" options={{ title: "小诺" }} />
          <Stack.Screen name="tasks/index" options={{ title: "任务" }} />
          <Stack.Screen name="tasks/[id]" options={{ title: "运行详情" }} />
          <Stack.Screen name="capabilities" options={{ title: "能力与连接" }} />
          <Stack.Screen name="capture" options={{ title: "拍照" }} />
          <Stack.Screen name="update" options={{ title: "版本与更新" }} />
        </Stack>
      </GatewayProvider>
    </KeyboardProvider>
  );
}
