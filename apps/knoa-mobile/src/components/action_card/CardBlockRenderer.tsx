import React from "react";
import { StyleSheet, View } from "react-native";

import { CardArtifactBlock } from "./blocks/CardArtifactBlock";
import { CardCalloutBlock } from "./blocks/CardCalloutBlock";
import { CardCodeDiffBlock } from "./blocks/CardCodeDiffBlock";
import { CardKeyValueBlock } from "./blocks/CardKeyValueBlock";
import { CardMarkdownBlock } from "./blocks/CardMarkdownBlock";
import type { ActionCardBlock } from "./types";

export function CardBlockRenderer({ blocks }: { blocks: ActionCardBlock[] }) {
  if (!blocks || blocks.length === 0) return null;

  return (
    <View style={styles.container}>
      {blocks.map((block, index) => {
        const key = `${block.type}-${index}`;
        switch (block.type) {
          case "markdown":
            return <CardMarkdownBlock key={key} block={block} />;
          case "key_value":
            return <CardKeyValueBlock key={key} block={block} />;
          case "code_diff":
            return <CardCodeDiffBlock key={key} block={block} />;
          case "callout":
            return <CardCalloutBlock key={key} block={block} />;
          case "artifact_link":
            return <CardArtifactBlock key={key} block={block} />;
          default:
            return null;
        }
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    gap: 4,
  },
});
