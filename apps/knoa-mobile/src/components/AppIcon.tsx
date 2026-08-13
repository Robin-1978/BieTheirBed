import Ionicons from "@expo/vector-icons/Ionicons";
import type { ColorValue, OpaqueColorValue } from "react-native";
import { View } from "react-native";

export type AppIconName =
  | "agent"
  | "archive"
  | "arrow-down"
  | "camera"
  | "chat"
  | "check"
  | "chevron-right"
  | "clock"
  | "edit"
  | "file"
  | "history"
  | "keyboard"
  | "mic"
  | "new-topic"
  | "pause"
  | "play"
  | "plus"
  | "refresh"
  | "restore"
  | "save"
  | "send"
  | "settings"
  | "share"
  | "stop"
  | "tasks"
  | "trash"
  | "x";

const glyphs: Record<AppIconName, React.ComponentProps<typeof Ionicons>["name"]> = {
  agent: "sparkles-outline",
  archive: "archive-outline",
  "arrow-down": "arrow-down",
  camera: "camera-outline",
  chat: "chatbubble-ellipses-outline",
  check: "checkmark",
  "chevron-right": "chevron-forward",
  clock: "time-outline",
  edit: "create-outline",
  file: "document-outline",
  history: "albums-outline",
  keyboard: "chatbox-outline",
  mic: "mic-outline",
  "new-topic": "chatbubble-outline",
  pause: "pause",
  play: "play",
  plus: "add",
  refresh: "refresh",
  restore: "arrow-undo-outline",
  save: "save-outline",
  send: "arrow-up",
  settings: "settings-outline",
  share: "share-outline",
  stop: "stop",
  tasks: "checkbox-outline",
  trash: "trash-outline",
  x: "close",
};

export function AppIcon({ name, color, size = 22 }: { name: AppIconName; color: ColorValue; size?: number }) {
  if (name === "new-topic") {
    return (
      <View style={{ width: size, height: size }}>
        <Ionicons name="chatbubble-outline" color={color as string | OpaqueColorValue} size={size} />
        <View style={{ position: "absolute", right: -3, bottom: -2, width: Math.max(10, size * 0.5), height: Math.max(10, size * 0.5), borderRadius: size, alignItems: "center", justifyContent: "center", backgroundColor: "transparent" }}>
          <Ionicons name="add" color={color as string | OpaqueColorValue} size={Math.max(12, size * 0.62)} />
        </View>
      </View>
    );
  }
  return <Ionicons name={glyphs[name]} color={color as string | OpaqueColorValue} size={size} />;
}
