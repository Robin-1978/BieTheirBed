import { memo, useCallback, useMemo, type ReactElement } from "react";
import { FlatList, type StyleProp, type ViewStyle } from "react-native";
import { useMarkdown, type MarkedStyles } from "react-native-marked";

import { colors } from "@/theme";
import { createKnoaRenderer } from "./KnoaMarkdownRenderer";

const theme = {
  colors: {
    background: "transparent",
    code: colors.surfaceMuted,
    link: colors.accent,
    text: colors.ink,
    border: colors.line,
  },
};

const markdownStyles: MarkedStyles = {
  text: { fontSize: 15, lineHeight: 23, color: colors.ink },
  strong: { fontSize: 15, lineHeight: 23, fontWeight: "700", color: colors.ink },
  h1: { fontSize: 20, lineHeight: 28, marginVertical: 8, fontWeight: "700", color: colors.ink },
  h2: { fontSize: 20, lineHeight: 26, marginVertical: 8, fontWeight: "700", color: colors.ink },
  h3: { fontSize: 16, lineHeight: 24, marginVertical: 6, fontWeight: "700", color: colors.ink },
  h4: { fontSize: 15, lineHeight: 23, marginVertical: 6, fontWeight: "600", color: colors.ink },
  blockquote: { borderLeftColor: colors.accent, borderLeftWidth: 3, paddingLeft: 10, marginVertical: 6 },
  codespan: { fontSize: 13, color: colors.accent, backgroundColor: colors.surfaceMuted },
  code: { backgroundColor: colors.surfaceMuted, borderRadius: 8, padding: 8 },
};

export const AppMarkdown = memo(function AppMarkdown({ value, style }: { value: string; style?: StyleProp<ViewStyle> }) {
  // Renderer 是有状态的（slugger 计数），每个实例独立，避免跨消息复用。
  const renderer = useMemo(() => createKnoaRenderer(), []);
  const elements = useMarkdown(value, { theme, styles: markdownStyles, renderer });
  const renderItem = useCallback(({ item }: { item: unknown }) => item as ReactElement, []);
  const keyExtractor = useCallback((_: unknown, index: number) => index.toString(), []);
  return (
    <FlatList
      data={elements}
      renderItem={renderItem}
      keyExtractor={keyExtractor}
      removeClippedSubviews={false}
      scrollEnabled={false}
      style={[{ width: "100%", alignSelf: "stretch", backgroundColor: "transparent" }, style]}
    />
  );
});
