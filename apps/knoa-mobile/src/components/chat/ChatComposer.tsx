import { router } from "expo-router";
import { useState } from "react";
import {
  ActivityIndicator,
  Image,
  Modal,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import {
  RecordingPresets,
  useAudioRecorder,
  useAudioRecorderState,
} from "expo-audio";

import type { InputMode, PendingAttachment } from "./types";
import { attachmentStatusLabel } from "./types";
import { AppIcon, type AppIconName } from "@/components/AppIcon";
import { AppPressable } from "@/components/AppPressable";
import { MAX_ATTACHMENTS, pickAttachments } from "@/media/attachmentPicker";
import { useI18n } from "@/i18n";
import { colors, radii, spacing, shadows, typography } from "@/theme";

export type ChatComposerProps = {
  text: string;
  onTextChange(text: string): void;
  inputMode: InputMode;
  onInputModeChange(mode: InputMode): void;
  attachments: PendingAttachment[];
  onAttachmentsChange(attachments: PendingAttachment[]): void;
  onRetryAttachment(index: number): void;
  canSend: boolean;
  sending: boolean;
  validatingInput: boolean;
  cancelling: boolean;
  showStopAction: boolean;
  stoppingResponse: boolean;
  onSend(): void;
  onStop(): void;
  onToggleRecording(): Promise<void>;
  recordingState: ReturnType<typeof useAudioRecorderState>;
  transcribing: boolean;
  nodeRouteParams: Record<string, string>;
};

export function ChatComposer({
  text,
  onTextChange,
  inputMode,
  onInputModeChange,
  attachments,
  onAttachmentsChange,
  onRetryAttachment,
  canSend,
  sending,
  validatingInput,
  cancelling,
  showStopAction,
  stoppingResponse,
  onSend,
  onStop,
  onToggleRecording,
  recordingState,
  transcribing,
  nodeRouteParams,
}: ChatComposerProps) {
  const { t } = useI18n();
  const [actionsOpen, setActionsOpen] = useState(false);

  async function chooseFile() {
    const prepared = await pickAttachments(attachments.length);
    if (prepared.length) {
      onAttachmentsChange([...attachments, ...prepared].slice(0, MAX_ATTACHMENTS));
    }
  }

  const primaryDisabled = showStopAction
    ? stoppingResponse || cancelling
    : inputMode === "voice"
      ? transcribing
      : !canSend || sending || validatingInput;

  return (
    <>
      {/* 附件缩略条 */}
      {attachments.length ? (
        <View style={styles.attachmentStrip}>
          {attachments.map((item, index) => (
            <View key={`${item.uri}:${index}`} style={styles.attachment}>
              {item.mediaType.startsWith("image/") ? (
                <Image source={{ uri: item.uri }} style={styles.thumbnail} />
              ) : null}
              <Pressable
                disabled={item.status !== "failed"}
                onPress={() => onRetryAttachment(index)}
                style={styles.attachmentCopy}
              >
                <Text style={styles.attachmentName} numberOfLines={1}>{item.name}</Text>
                {item.status ? (
                  <Text style={[styles.attachmentStatus, item.status === "failed" && styles.attachmentFailed]}>
                    {attachmentStatusLabel(item.status, t)}
                  </Text>
                ) : null}
              </Pressable>
              <AppPressable
                accessibilityLabel={t("chat.removeAttachment", { name: item.name })}
                onPress={() => onAttachmentsChange(attachments.filter((_, i) => i !== index))}
                style={styles.removeAction}
              >
                <AppIcon name="x" color={colors.muted} size={17} />
              </AppPressable>
            </View>
          ))}
        </View>
      ) : null}

      {/* 底部输入控制栏 */}
      <View style={styles.composer}>
        <AppPressable
          accessibilityLabel={t("chat.add")}
          onPress={() => setActionsOpen(true)}
          style={styles.roundAction}
        >
          <AppIcon name="plus" color={colors.accent} />
        </AppPressable>

        <View style={styles.inputShell}>
          <AppPressable
            accessibilityLabel={inputMode === "text" ? t("chat.switchVoice") : t("chat.switchText")}
            disabled={recordingState.isRecording || transcribing}
            onPress={() => onInputModeChange(inputMode === "text" ? "voice" : "text")}
            style={styles.inputMode}
          >
            <AppIcon name={inputMode === "text" ? "mic" : "keyboard"} color={colors.muted} size={20} />
          </AppPressable>
          <TextInput
            editable={inputMode === "text"}
            style={styles.input}
            value={text}
            onChangeText={onTextChange}
            placeholder={inputMode === "text" ? t("chat.placeholder") : t("chat.voicePlaceholder")}
            placeholderTextColor={colors.muted}
            multiline
          />
        </View>

        <AppPressable
          accessibilityLabel={
            showStopAction
              ? t("chat.stop")
              : inputMode === "voice"
                ? recordingState.isRecording ? t("chat.stopRecording") : t("chat.startRecording")
                : t("chat.send")
          }
          onPress={() => {
            if (showStopAction) onStop();
            else if (inputMode === "voice") void onToggleRecording();
            else onSend();
          }}
          disabled={primaryDisabled}
          style={[
            styles.primaryAction,
            recordingState.isRecording && styles.primaryRecording,
            stoppingResponse && styles.primaryStopping,
            primaryDisabled && styles.sendDisabled,
          ]}
        >
          {sending || validatingInput || transcribing || cancelling ? (
            <ActivityIndicator color={colors.onAccent} size="small" />
          ) : stoppingResponse ? (
            <AppIcon name="stop" color="white" size={17} />
          ) : recordingState.isRecording ? (
            <View style={styles.recordingContent}>
              <AppIcon name="stop" color="white" size={17} />
              <Text style={styles.recordingTime}>{Math.round(recordingState.durationMillis / 1000)}s</Text>
            </View>
          ) : inputMode === "text" ? (
            <AppIcon name="send" color={colors.onAccent} size={19} />
          ) : (
            <AppIcon name="mic" color={colors.onAccent} />
          )}
        </AppPressable>
      </View>

      {/* 媒体添加弹出面板 */}
      <Modal
        animationType="fade"
        onRequestClose={() => setActionsOpen(false)}
        transparent
        visible={actionsOpen}
      >
        <View style={styles.modalRoot}>
          <Pressable style={styles.backdrop} onPress={() => setActionsOpen(false)} />
          <View style={styles.actionSheet}>
            <View style={styles.sheetHandle} />
            <Text style={styles.sheetTitle}>{t("chat.addContent")}</Text>
            <View style={styles.sheetActions}>
              <MediaAction
                icon="camera"
                label={t("chat.camera")}
                onPress={() => {
                  setActionsOpen(false);
                  router.push({ pathname: "/capture", params: nodeRouteParams });
                }}
              />
              <MediaAction
                icon="file"
                label={t("chat.file")}
                onPress={() => {
                  setActionsOpen(false);
                  void chooseFile();
                }}
              />
            </View>
          </View>
        </View>
      </Modal>
    </>
  );
}

function MediaAction({
  icon,
  label,
  onPress,
}: {
  icon: AppIconName;
  label: string;
  onPress(): void;
}) {
  return (
    <AppPressable accessibilityLabel={label} onPress={onPress} style={styles.mediaAction}>
      <View style={styles.mediaIcon}>
        <AppIcon name={icon} color={colors.accent} size={24} />
      </View>
      <Text style={styles.mediaLabel}>{label}</Text>
    </AppPressable>
  );
}

const styles = StyleSheet.create({
  attachmentStrip: {
    paddingHorizontal: spacing.large,
    paddingTop: spacing.small,
    gap: spacing.small,
    flexDirection: "row",
    flexWrap: "wrap",
    backgroundColor: colors.surface,
  },
  attachment: {
    maxWidth: "100%",
    borderRadius: radii.medium,
    backgroundColor: colors.background,
    borderWidth: 1,
    borderColor: colors.line,
    paddingHorizontal: spacing.small,
    paddingVertical: 6,
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.small,
  },
  thumbnail: {
    width: 28,
    height: 28,
    borderRadius: radii.small,
  },
  attachmentCopy: {
    flexShrink: 1,
    gap: 2,
  },
  attachmentName: {
    color: colors.ink,
    fontSize: 12,
    fontWeight: "700",
  },
  attachmentStatus: {
    color: colors.muted,
    fontSize: 11,
  },
  attachmentFailed: {
    color: colors.danger,
    fontWeight: "700",
  },
  removeAction: {
    width: 28,
    height: 28,
    alignItems: "center",
    justifyContent: "center",
  },
  composer: {
    flexDirection: "row",
    alignItems: "flex-end",
    gap: spacing.small,
    paddingHorizontal: spacing.large,
    paddingVertical: spacing.small,
    backgroundColor: colors.surface,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.line,
  },
  roundAction: {
    width: 44,
    height: 44,
    borderRadius: 22,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.background,
    borderWidth: 1,
    borderColor: colors.line,
  },
  inputShell: {
    flex: 1,
    minHeight: 44,
    maxHeight: 120,
    borderRadius: 22,
    backgroundColor: colors.background,
    borderWidth: 1,
    borderColor: colors.line,
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: spacing.small,
  },
  inputMode: {
    width: 32,
    height: 32,
    alignItems: "center",
    justifyContent: "center",
    marginRight: 4,
  },
  input: {
    flex: 1,
    color: colors.ink,
    fontSize: 14,
    paddingVertical: 10,
    paddingRight: 6,
  },
  primaryAction: {
    width: 44,
    height: 44,
    borderRadius: 22,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.accent,
  },
  primaryRecording: {
    backgroundColor: colors.danger,
    width: 72,
  },
  primaryStopping: {
    backgroundColor: colors.muted,
  },
  sendDisabled: {
    opacity: 0.45,
  },
  recordingContent: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
  },
  recordingTime: {
    color: "white",
    fontSize: 11,
    fontWeight: "800",
  },
  modalRoot: {
    flex: 1,
    justifyContent: "flex-end",
  },
  backdrop: {
    ...StyleSheet.absoluteFill,
    backgroundColor: "rgba(0, 0, 0, 0.4)",
  },
  actionSheet: {
    backgroundColor: colors.surface,
    borderTopLeftRadius: radii.large,
    borderTopRightRadius: radii.large,
    padding: spacing.large,
    gap: spacing.medium,
  },
  sheetHandle: {
    width: 36,
    height: 4,
    borderRadius: 2,
    backgroundColor: colors.line,
    alignSelf: "center",
  },
  sheetTitle: {
    color: colors.ink,
    fontSize: 14,
    fontWeight: "800",
    textAlign: "center",
  },
  sheetActions: {
    flexDirection: "row",
    justifyContent: "space-around",
    paddingVertical: spacing.small,
  },
  mediaAction: {
    alignItems: "center",
    gap: 8,
  },
  mediaIcon: {
    width: 56,
    height: 56,
    borderRadius: 28,
    backgroundColor: colors.accentSoft,
    alignItems: "center",
    justifyContent: "center",
  },
  mediaLabel: {
    color: colors.ink,
    fontSize: 12,
    fontWeight: "700",
  },
});
