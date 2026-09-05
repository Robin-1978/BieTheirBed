import type { MemoryRecord } from "@/api/models";

export type MemoryFilter = "all" | "core" | "relevant";

export function filterMemories(
  items: MemoryRecord[],
  filter: MemoryFilter,
): MemoryRecord[] {
  if (filter === "all") return items;
  return items.filter((item) => item.importance === filter);
}

export function formatConfidencePercent(confidence: number): string {
  const clamped = Math.max(0, Math.min(1, confidence));
  return `${Math.round(clamped * 100)}%`;
}

export function categoryDisplayName(category: string): string {
  const map: Record<string, string> = {
    environment: "环境信息",
    workflow: "工作流习惯",
    preference: "个人偏好",
    device: "设备特征",
  };
  return map[category] || category;
}
