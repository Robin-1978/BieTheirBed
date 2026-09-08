import React from "react";
import { StyleSheet, View } from "react-native";

import { AppMarkdown } from "@/components/AppMarkdown";
import { spacing } from "@/theme";
import type { CardMarkdownBlock as CardMarkdownBlockType } from "../types";

export function CardMarkdownBlock({ block }: { block: CardMarkdownBlockType }) {
  if (!block.content) return null;
  return (
    <View style={styles.container}>
      <AppMarkdown value={block.content} />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginVertical: spacing.xsmall,
  },
});
