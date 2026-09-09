import * as Clipboard from "expo-clipboard";
import { useCallback, useRef, useState } from "react";

import type { ClipboardSuggestion } from "@/components/chat";

export interface UseClipboardSuggestionOptions {
  text: string;
  showFeedback?: (text: string, tone?: "info" | "success" | "warning" | "error") => void;
  copiedMessageText?: string;
}

export function useClipboardSuggestion({
  text,
  showFeedback,
  copiedMessageText = "已复制",
}: UseClipboardSuggestionOptions) {
  const lastDismissedClipboardRef = useRef("");
  const [clipboardSuggestion, setClipboardSuggestion] = useState<ClipboardSuggestion | null>(null);

  const copyMessage = useCallback(async (content: string) => {
    if (!content.trim()) return;
    lastDismissedClipboardRef.current = content.trim();
    await Clipboard.setStringAsync(content);
    if (showFeedback) {
      showFeedback(copiedMessageText, "success");
    }
  }, [copiedMessageText, showFeedback]);

  const checkClipboard = useCallback(async () => {
    try {
      const hasStr = await Clipboard.hasStringAsync();
      if (!hasStr) return;
      const content = (await Clipboard.getStringAsync())?.trim();
      if (!content || content.length < 4 || content.length > 2000) return;
      if (content === lastDismissedClipboardRef.current) return;
      if (text.trim().includes(content)) return;

      const isUrl = /^https?:\/\/[^\s]+$/i.test(content);
      const isCode = content.includes("\n") && (
        content.includes("Error") ||
        content.includes("error") ||
        content.includes("Exception") ||
        content.includes("failed") ||
        content.includes("function")
      );
      setClipboardSuggestion({
        text: content,
        kind: isUrl ? "url" : isCode ? "code" : "text",
      });
    } catch {
      // ignore clipboard permission error
    }
  }, [text]);

  const dismissSuggestion = useCallback(() => {
    if (clipboardSuggestion) {
      lastDismissedClipboardRef.current = clipboardSuggestion.text;
    }
    setClipboardSuggestion(null);
  }, [clipboardSuggestion]);

  return {
    clipboardSuggestion,
    copyMessage,
    checkClipboard,
    dismissSuggestion,
  };
}
