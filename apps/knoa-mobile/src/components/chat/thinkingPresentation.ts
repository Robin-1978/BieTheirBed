export type ThinkingDisplayInfo = {
  cleanedText: string;
  isThinking: boolean;
  titleKey: "chat.thinking" | "chat.thoughtCompleted";
};

export function formatThinkingDisplay(
  reasoning: string,
  isThinking: boolean,
): ThinkingDisplayInfo | null {
  const cleaned = reasoning.trim();
  if (!cleaned) return null;

  return {
    cleanedText: cleaned,
    isThinking,
    titleKey: isThinking ? "chat.thinking" : "chat.thoughtCompleted",
  };
}
