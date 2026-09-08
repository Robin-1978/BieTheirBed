import type { ChatTurnSnapshot } from "@/api/models";
import type { ActionCard } from "./types";

const ACTION_CARD_FENCE_REGEX = /```(?:action_card|json)\s*\n([\s\S]*?)\n```/g;

/**
 * Validates if an object conforms to the minimum ActionCard protocol contract.
 */
export function isActionCardLike(obj: unknown): obj is ActionCard {
  if (!obj || typeof obj !== "object" || Array.isArray(obj)) return false;
  const card = obj as Partial<ActionCard>;
  return (
    typeof card.card_id === "string" &&
    card.card_id.length > 0 &&
    typeof card.title === "string" &&
    Array.isArray(card.blocks) &&
    typeof card.level === "string"
  );
}

/**
 * Extracts all valid ActionCard instances from a chat turn.
 * Sources include:
 * 1. Dedicated `turn.action_cards` array if present.
 * 2. Structured tool execution results in `turn.tool_steps`.
 * 3. Embedded JSON code fences in `turn.final_output` or `turn.content`.
 */
export function extractActionCards(turn: ChatTurnSnapshot): ActionCard[] {
  const cards: ActionCard[] = [];
  const seenIds = new Set<string>();

  const register = (candidate: unknown) => {
    if (isActionCardLike(candidate) && !seenIds.has(candidate.card_id)) {
      seenIds.add(candidate.card_id);
      cards.push(candidate);
    }
  };

  // 1. Direct turn.action_cards field if populated by platform
  const directCards = (turn as Record<string, unknown>).action_cards;
  if (Array.isArray(directCards)) {
    for (const item of directCards) {
      register(item);
    }
  }

  // 2. Scan tool_steps for action card deliverables
  if (Array.isArray(turn.tool_steps)) {
    for (const step of turn.tool_steps) {
      if (!step || typeof step !== "object") continue;
      const result = (step as Record<string, unknown>).tool_result;
      if (result && typeof result === "object") {
        register(result);
        register((result as Record<string, unknown>).action_card);
        register((result as Record<string, unknown>).card);
      }
    }
  }

  // 3. Scan conversational markdown content for embedded action_card JSON fences
  const textPayloads = [turn.final_output, turn.content].filter(Boolean);
  for (const text of textPayloads) {
    ACTION_CARD_FENCE_REGEX.lastIndex = 0;
    let match: RegExpExecArray | null;
    while ((match = ACTION_CARD_FENCE_REGEX.exec(text)) !== null) {
      const jsonRaw = match[1]?.trim();
      if (!jsonRaw || (!jsonRaw.startsWith("{") && !jsonRaw.endsWith("}"))) continue;
      try {
        const parsed = JSON.parse(jsonRaw);
        register(parsed);
      } catch {
        // Not a valid JSON payload, skip
      }
    }
  }

  return cards;
}

/**
 * Strips raw action_card code blocks from text so the conversational
 * text bubble remains clean and natural, letting the ActionCardView
 * render the interactive card underneath.
 */
export function stripActionCardMarkdownBlocks(content: string): string {
  if (!content) return "";
  return content.replace(/```action_card\s*\n[\s\S]*?\n```/g, "").trim();
}
