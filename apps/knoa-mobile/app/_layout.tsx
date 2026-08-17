import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { KeyboardProvider } from "react-native-keyboard-controller";
import { GestureHandlerRootView } from "react-native-gesture-handler";

import { HeaderActions } from "@/components/HeaderActions";
import { NodeHeaderBack, NodeHeaderTitle } from "@/components/NodeHeader";
import { TaskReminderBanner } from "@/components/TaskReminderBanner";
import { I18nProvider, useI18n } from "@/i18n";
import { GatewayProvider } from "@/state/GatewayProvider";
import { TaskReminderProvider } from "@/state/TaskReminderProvider";
import { ThemeProvider, useThemePreference } from "@/state/ThemeProvider";
import { colors } from "@/theme";

export default function RootLayout() {
  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <KeyboardProvider>
        <I18nProvider>
          <ThemeProvider>
            <GatewayProvider>
              <TaskReminderProvider>
                <AppNavigator />
                <TaskReminderBanner />
              </TaskReminderProvider>
            </GatewayProvider>
          </ThemeProvider>
        </I18nProvider>
      </KeyboardProvider>
    </GestureHandlerRootView>
  );
}

function AppNavigator() {
  const { resolved: scheme } = useThemePreference();
  const { t } = useI18n();
  return (
    <>
        <StatusBar style={scheme === "dark" ? "light" : "dark"} />
        <Stack
          screenOptions={{
            headerStyle: { backgroundColor: colors.background },
            headerTintColor: colors.ink,
            contentStyle: { backgroundColor: colors.background },
          }}
        >
          <Stack.Screen name="index" options={{ headerShown: false }} />
          <Stack.Screen name="connect" options={{ headerShown: false }} />
          <Stack.Screen name="account/login" options={{ headerShown: false }} />
          <Stack.Screen name="account/index" options={{ title: "帐号与 Workspace" }} />
          <Stack.Screen name="workspaces/[workspaceId]/index" options={{ title: "Workspace" }} />
          <Stack.Screen name="node" options={{ title: "Node" }} />
          <Stack.Screen name="pair" options={{ title: t("nav.connect") }} />
          <Stack.Screen
            name="chat"
            options={{
              headerTitle: () => <NodeHeaderTitle />,
              headerLeft: () => <NodeHeaderBack />,
              animation: "none",
              headerRight: () => <HeaderActions current="chat" />,
            }}
          />
          <Stack.Screen name="conversations/index" options={{ title: t("nav.conversations") }} />
          <Stack.Screen
            name="tasks/index"
            options={{
              headerTitle: () => <NodeHeaderTitle />,
              headerLeft: () => <NodeHeaderBack />,
              animation: "none",
              headerRight: () => <HeaderActions current="tasks" />,
            }}
          />
          <Stack.Screen name="tasks/new" options={{ title: t("nav.newTask") }} />
          <Stack.Screen name="tasks/[id]" options={{ title: t("nav.taskDetails") }} />
          <Stack.Screen name="tasks/[id]/edit" options={{ title: t("nav.editTask") }} />
          <Stack.Screen name="task-executions/[id]" options={{ title: t("nav.executionDetails") }} />
          <Stack.Screen name="capabilities" options={{ title: t("nav.settings") }} />
          <Stack.Screen name="settings/app" options={{ title: "App 设置" }} />
          <Stack.Screen name="settings/system" options={{ title: t("nav.systemConfiguration") }} />
          <Stack.Screen name="settings/extensions" options={{ title: "Extension Center" }} />
          <Stack.Screen name="settings/models" options={{ title: "Model Center" }} />
          <Stack.Screen name="settings/nodes" options={{ title: "Node Center" }} />
          <Stack.Screen name="capture" options={{ title: t("nav.capture") }} />
          <Stack.Screen name="update" options={{ title: t("nav.update") }} />
        </Stack>
    </>
  );
}
