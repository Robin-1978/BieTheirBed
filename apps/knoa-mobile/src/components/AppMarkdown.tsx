import type { StyleProp, ViewStyle } from "react-native";
import Markdown from "react-native-marked";

import { colors } from "@/theme";

const theme = {
  colors: {
    background: "transparent",
    code: colors.background,
    link: colors.accent,
    text: colors.ink,
    border: colors.line,
  },
};

export function AppMarkdown({ value, style }: { value: string; style?: StyleProp<ViewStyle> }) {
  return (
    <Markdown
      value={value}
      theme={theme}
      flatListProps={{
        scrollEnabled: false,
        style: [{ width: "100%", alignSelf: "stretch", backgroundColor: "transparent" }, style],
      }}
    />
  );
}
