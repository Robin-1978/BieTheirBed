import Ionicons from "@expo/vector-icons/Ionicons";
import type { ColorValue, OpaqueColorValue } from "react-native";

export type AppIconName =
  | "archive"
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
  archive: "archive-outline",
  camera: "camera-outline",
  chat: "chatbubble-ellipses-outline",
  check: "checkmark",
  "chevron-right": "chevron-forward",
  clock: "time-outline",
  edit: "create-outline",
  file: "document-outline",
  history: "time-outline",
  keyboard: "keypad-outline",
  mic: "mic-outline",
  "new-topic": "add-circle",
  pause: "pause",
  play: "play",
  plus: "add",
  refresh: "refresh",
  restore: "arrow-undo-outline",
  save: "save-outline",
  send: "arrow-up",
  settings: "settings",
  share: "share-outline",
  stop: "stop",
  tasks: "checkbox-outline",
  trash: "trash-outline",
  x: "close",
};

export function AppIcon({ name, color, size = 22 }: { name: AppIconName; color: ColorValue; size?: number }) {
  return <Ionicons name={glyphs[name]} color={color as string | OpaqueColorValue} size={size} />;
}
