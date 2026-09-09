import {
  RecordingPresets,
  requestRecordingPermissionsAsync,
  setAudioModeAsync,
  useAudioRecorder,
  useAudioRecorderState,
} from "expo-audio";
import * as Linking from "expo-linking";
import { useCallback, useState } from "react";

import type { GatewayClient } from "@/api/gatewayClient";

export interface UseVoiceRecorderOptions {
  runAuthenticated: <T>(operation: (client: GatewayClient) => Promise<T>) => Promise<T>;
  ensureConversation: () => Promise<string>;
  hasClient: boolean;
  onTranscription: (transcript: string) => void;
  showFeedback: (text: string, tone?: "info" | "success" | "warning" | "error") => void;
  t: (key: any, params?: any) => string;
}

export function useVoiceRecorder({
  runAuthenticated,
  ensureConversation,
  hasClient,
  onTranscription,
  showFeedback,
  t,
}: UseVoiceRecorderOptions) {
  const recorder = useAudioRecorder(RecordingPresets.HIGH_QUALITY);
  const recordingState = useAudioRecorderState(recorder, 250);
  const [transcribing, setTranscribing] = useState(false);

  const toggleRecording = useCallback(async () => {
    if (!hasClient || transcribing) return;
    if (recordingState.isRecording) {
      await recorder.stop();
      const uri = recorder.uri;
      if (!uri) return;
      setTranscribing(true);
      try {
        const sessionHandle = await ensureConversation();
        const response = await fetch(uri);
        const extension = uri.toLowerCase().endsWith(".webm") ? "webm" : "m4a";
        const bytes = await response.arrayBuffer();
        const artifact = await runAuthenticated((client) => client.uploadArtifact({
          sessionHandle,
          bytes,
          mediaType: extension === "webm" ? "audio/webm" : "audio/mp4",
          name: `voice-${Date.now()}.${extension}`,
          caption: t("chat.voiceCaption"),
        }));
        const transcript = await runAuthenticated((client) => client.transcribeArtifact(
          sessionHandle,
          artifact.artifact_id,
        ));
        onTranscription(transcript);
      } catch {
        showFeedback(t("chat.transcriptionFailed"), "error");
      } finally {
        await setAudioModeAsync({ allowsRecording: false }).catch(() => undefined);
        setTranscribing(false);
      }
      return;
    }

    const permission = await requestRecordingPermissionsAsync();
    if (!permission.granted) {
      showFeedback(permission.canAskAgain
        ? t("chat.microphoneRequired")
        : t("chat.microphoneDisabled"), "warning");
      if (!permission.canAskAgain) await Linking.openSettings();
      return;
    }
    try {
      await setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true });
      await recorder.prepareToRecordAsync();
      recorder.record();
    } catch {
      await setAudioModeAsync({ allowsRecording: false }).catch(() => undefined);
      showFeedback(t("chat.recordingFailed"), "error");
    }
  }, [ensureConversation, hasClient, onTranscription, recordingState.isRecording, recorder, runAuthenticated, showFeedback, t, transcribing]);

  return {
    recorder,
    recordingState,
    transcribing,
    toggleRecording,
  };
}
