import type { Task } from "@/api/models";
import type { AppIconName } from "./AppIcon";
import { estimateSavedMinutes } from "./taskBentoPresentation";

export interface ArtifactTypeInfo {
  label: string;
  icon: AppIconName;
  isVisual: boolean;
  isCode: boolean;
}

export function classifyArtifactType(name: string, mediaType: string, kind = ""): ArtifactTypeInfo {
  const lowerName = name.toLowerCase();
  const lowerType = mediaType.toLowerCase();

  if (kind === "image" || lowerType.startsWith("image/") || lowerName.endsWith(".png") || lowerName.endsWith(".jpg") || lowerName.endsWith(".jpeg") || lowerName.endsWith(".svg")) {
    return { label: "视觉图表", icon: "image", isVisual: true, isCode: false };
  }

  if (
    lowerName.endsWith(".ts") ||
    lowerName.endsWith(".tsx") ||
    lowerName.endsWith(".js") ||
    lowerName.endsWith(".py") ||
    lowerName.endsWith(".rs") ||
    lowerName.endsWith(".go") ||
    lowerName.endsWith(".diff") ||
    lowerName.endsWith(".patch") ||
    lowerType.includes("javascript") ||
    lowerType.includes("python")
  ) {
    return { label: "代码补丁", icon: "code", isVisual: false, isCode: true };
  }

  if (lowerName.endsWith(".md") || lowerName.endsWith(".txt") || lowerType.includes("markdown") || lowerType.includes("text/")) {
    return { label: "调研简报", icon: "file", isVisual: false, isCode: false };
  }

  return { label: "交付文件", icon: "folder", isVisual: false, isCode: false };
}

export function hostRelativePath(fileName: string, _artifactId: string): string {
  return `~/Downloads/knoa-artifacts/${fileName}`;
}

export function calculateTotalSavedHours(tasks: Task[]): number {
  const totalMinutes = tasks.reduce((sum, task) => sum + estimateSavedMinutes(task), 0);
  return Math.round((totalMinutes / 60) * 10) / 10;
}
