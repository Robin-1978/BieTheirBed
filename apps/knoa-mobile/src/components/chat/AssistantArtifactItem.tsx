import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Image,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";

import type { AssistantArtifactItem as AssistantArtifactItemType, ResolvedArtifactFile } from "@/api/chatArtifacts";
import { AppPressable } from "@/components/AppPressable";
import { useI18n } from "@/i18n";
import { colors, radii, spacing } from "@/theme";

export type AssistantArtifactItemProps = {
  item: AssistantArtifactItemType;
  onLoad(item: AssistantArtifactItemType): Promise<ResolvedArtifactFile>;
  onOpen(item: AssistantArtifactItemType): Promise<void>;
  onSave(item: AssistantArtifactItemType): Promise<void>;
};

export function AssistantArtifactItem({
  item,
  onLoad,
  onOpen,
  onSave,
}: AssistantArtifactItemProps) {
  const { t } = useI18n();
  const [previewUri, setPreviewUri] = useState("");
  const [loading, setLoading] = useState(item.isImage);
  const [failed, setFailed] = useState(false);
  const [opening, setOpening] = useState(false);
  const [saving, setSaving] = useState(false);
  const request = useRef(0);

  const loadPreview = useCallback(async () => {
    if (!item.isImage) return;
    const currentRequest = ++request.current;
    setLoading(true);
    setFailed(false);
    try {
      const resolved = await onLoad(item);
      if (request.current === currentRequest) setPreviewUri(resolved.uri);
    } catch {
      if (request.current === currentRequest) setFailed(true);
    } finally {
      if (request.current === currentRequest) setLoading(false);
    }
  }, [item, onLoad]);

  useEffect(() => {
    void loadPreview();
    return () => { request.current += 1; };
  }, [loadPreview]);

  const open = useCallback(async () => {
    if (failed) {
      await loadPreview();
      return;
    }
    setOpening(true);
    try {
      await onOpen(item);
    } finally {
      setOpening(false);
    }
  }, [failed, item, loadPreview, onOpen]);

  const save = useCallback(async () => {
    setSaving(true);
    try {
      await onSave(item);
    } finally {
      setSaving(false);
    }
  }, [item, onSave]);

  if (item.isImage) {
    return (
      <Pressable
        accessibilityLabel={failed ? t("chat.reloadArtifact", { name: item.displayName }) : t("chat.openArtifact", { name: item.displayName })}
        accessibilityRole="button"
        disabled={opening}
        onPress={() => void open()}
        style={styles.generatedImageCard}
      >
        {previewUri && !failed ? (
          <Image
            onError={() => {
              setPreviewUri("");
              setFailed(true);
            }}
            resizeMode="contain"
            source={{ uri: previewUri }}
            style={styles.generatedImage}
          />
        ) : (
          <View style={styles.generatedImageState}>
            {loading ? <ActivityIndicator color={colors.accent} size="small" /> : null}
            <Text style={failed ? styles.generatedArtifactError : styles.generatedArtifactHint}>
              {failed ? t("chat.imageRetry") : t("chat.imageLoading")}
            </Text>
          </View>
        )}
        <View style={styles.generatedArtifactCaption}>
          <Text style={styles.generatedArtifactName} numberOfLines={1}>{item.displayName}</Text>
          {opening ? <ActivityIndicator color={colors.accent} size="small" /> : null}
        </View>
      </Pressable>
    );
  }

  return (
    <View style={styles.generatedFile}>
      <View style={styles.generatedFileBadge}>
        <Text style={styles.generatedFileBadgeText}>{t("execution.attachment")}</Text>
      </View>
      <Text style={styles.generatedArtifactName} numberOfLines={2}>{item.displayName}</Text>
      <AppPressable
        accessibilityLabel={t("chat.openOrShare", { name: item.displayName })}
        disabled={opening || saving}
        onPress={() => void open()}
        style={styles.fileAction}
      >
        {opening ? <ActivityIndicator color={colors.accent} size="small" /> : <Text style={styles.fileActionText}>{t("execution.open")}</Text>}
      </AppPressable>
      <AppPressable
        accessibilityLabel={t("chat.saveArtifact", { name: item.displayName })}
        disabled={opening || saving}
        onPress={() => void save()}
        style={styles.fileAction}
      >
        {saving ? <ActivityIndicator color={colors.accent} size="small" /> : <Text style={styles.fileActionText}>{t("execution.save")}</Text>}
      </AppPressable>
    </View>
  );
}

const styles = StyleSheet.create({
  generatedImageCard: {
    width: "100%",
    borderRadius: radii.medium,
    backgroundColor: colors.background,
    borderWidth: 1,
    borderColor: colors.line,
    overflow: "hidden",
  },
  generatedImage: {
    width: "100%",
    height: 180,
    backgroundColor: "#000000",
  },
  generatedImageState: {
    width: "100%",
    height: 140,
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.small,
  },
  generatedArtifactError: { color: colors.danger, fontSize: 12 },
  generatedArtifactHint: { color: colors.muted, fontSize: 12 },
  generatedArtifactCaption: {
    paddingHorizontal: spacing.medium,
    paddingVertical: spacing.small,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  generatedArtifactName: {
    color: colors.ink,
    fontSize: 12,
    fontWeight: "700",
    flex: 1,
  },
  generatedFile: {
    width: "100%",
    borderRadius: radii.medium,
    backgroundColor: colors.background,
    borderWidth: 1,
    borderColor: colors.line,
    padding: spacing.medium,
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.small,
  },
  generatedFileBadge: {
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: radii.small,
    backgroundColor: colors.accentSoft,
  },
  generatedFileBadgeText: {
    color: colors.accent,
    fontSize: 10,
    fontWeight: "800",
  },
  fileAction: {
    paddingHorizontal: spacing.small,
    paddingVertical: 4,
    borderRadius: radii.small,
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.surface,
  },
  fileActionText: {
    color: colors.ink,
    fontSize: 11,
    fontWeight: "700",
  },
});
