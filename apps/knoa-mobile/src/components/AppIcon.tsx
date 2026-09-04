import Ionicons from "@expo/vector-icons/Ionicons";
import type { ColorValue, OpaqueColorValue } from "react-native";
import { View } from "react-native";

export type AppIconName =
  | "agent"
  | "alert"
  | "archive"
  | "arrow-down"
  | "camera"
  | "chat"
  | "check"
  | "chevron-down"
  | "chevron-up"
  | "chevron-left"
  | "chevron-right"
  | "clock"
  | "code"
  | "desktop"
  | "edit"
  | "eye"
  | "file"
  | "folder"
  | "globe"
  | "history"
  | "image"
  | "keyboard"
  | "mic"
  | "more"
  | "new-topic"
  | "node"
  | "pause"
  | "play"
  | "plus"
  | "pulse"
  | "refresh"
  | "restore"
  | "save"
  | "send"
  | "settings"
  | "share"
  | "stop"
  | "tasks"
  | "timer"
  | "trash"
  | "user"
  | "workspace"
  | "x";

const glyphs: Record<AppIconName, React.ComponentProps<typeof Ionicons>["name"]> = {
  agent: "sparkles-outline",
  alert: "alert-circle-outline",
  archive: "archive-outline",
  "arrow-down": "arrow-down",
  camera: "camera-outline",
  chat: "chatbubble-ellipses-outline",
  check: "checkmark",
  "chevron-down": "chevron-down",
  "chevron-up": "chevron-up",
  "chevron-left": "chevron-back",
  "chevron-right": "chevron-forward",
  clock: "time-outline",
  code: "code-slash-outline",
  desktop: "desktop-outline",
  edit: "create-outline",
  eye: "eye-outline",
  file: "document-outline",
  folder: "folder-outline",
  globe: "globe-outline",
  image: "image-outline",
  pulse: "pulse-outline",
  timer: "timer-outline",
  history: "albums-outline",
  keyboard: "chatbox-outline",
  mic: "mic-outline",
  more: "ellipsis-horizontal",
  "new-topic": "chatbubble-outline",
  node: "desktop-outline",
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
  user: "person-circle-outline",
  workspace: "grid-outline",
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
