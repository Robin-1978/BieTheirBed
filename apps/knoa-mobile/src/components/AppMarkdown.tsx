import { memo } from "react";
import { View, type StyleProp, type ViewStyle } from "react-native";
import { useMarkdown, type MarkedStyles } from "react-native-marked";

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

const markdownStyles: MarkedStyles = {
  text: { fontSize: 16, lineHeight: 24 },
  strong: { fontSize: 16, lineHeight: 24, fontWeight: "700" },
  h1: { fontSize: 21, lineHeight: 29, marginVertical: 8 },
  h2: { fontSize: 19, lineHeight: 27, marginVertical: 8 },
  h3: { fontSize: 17, lineHeight: 25, marginVertical: 6 },
  h4: { fontSize: 16, lineHeight: 24, marginVertical: 6 },
};

export const AppMarkdown = memo(function AppMarkdown({ value, style }: { value: string; style?: StyleProp<ViewStyle> }) {
  const elements = useMarkdown(value, { theme, styles: markdownStyles });
  return <View style={[{ width: "100%", alignSelf: "stretch" }, style]}>{elements}</View>;
});
