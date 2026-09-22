import type { AppIconName } from "@/components/AppIcon";
import type { MessageKey } from "@/i18n";

export type CapabilityScenario = {
  id: string;
  icon: AppIconName;
  titleKey: MessageKey;
  detailKey: MessageKey;
  promptKey: MessageKey;
  /** 映射的任务模板：有则进任务创建（范围→预检→执行），无则进对话。 */
  taskTemplateId?: string;
  /** 依赖的内置 Skill 包：已加载配置里明确禁用时，卡片置灰并导向扩展中心。 */
  skillId?: string;
};

export const CAPABILITY_SCENARIOS: CapabilityScenario[] = [
  {
    id: "file-organization",
    icon: "folder",
    titleKey: "capabilities.scenarios.fileOrganization.title",
    detailKey: "capabilities.scenarios.fileOrganization.detail",
    promptKey: "capabilities.scenarios.fileOrganization.prompt",
    taskTemplateId: "folder-organizer",
    skillId: "file_organizer",
  },
  {
    id: "health-check",
    icon: "pulse",
    titleKey: "capabilities.scenarios.healthCheck.title",
    detailKey: "capabilities.scenarios.healthCheck.detail",
    promptKey: "capabilities.scenarios.healthCheck.prompt",
    taskTemplateId: "computer-health",
    skillId: "health_check",
  },
  {
    id: "web-research",
    icon: "globe",
    titleKey: "capabilities.scenarios.webResearch.title",
    detailKey: "capabilities.scenarios.webResearch.detail",
    promptKey: "capabilities.scenarios.webResearch.prompt",
    taskTemplateId: "research-brief",
    skillId: "research_report",
  },
  {
    id: "monitor",
    icon: "eye",
    titleKey: "capabilities.scenarios.monitor.title",
    detailKey: "capabilities.scenarios.monitor.detail",
    promptKey: "capabilities.scenarios.monitor.prompt",
    taskTemplateId: "service-monitor",
    skillId: "monitor",
  },
  {
    id: "image-docs",
    icon: "image",
    titleKey: "capabilities.scenarios.imageDocs.title",
    detailKey: "capabilities.scenarios.imageDocs.detail",
    promptKey: "capabilities.scenarios.imageDocs.prompt",
    taskTemplateId: "media-review",
    skillId: "image_doc",
  },
  {
    id: "desktop-control",
    icon: "desktop",
    titleKey: "capabilities.scenarios.desktopControl.title",
    detailKey: "capabilities.scenarios.desktopControl.detail",
    promptKey: "capabilities.scenarios.desktopControl.prompt",
  },
  {
    id: "task-automation",
    icon: "timer",
    titleKey: "capabilities.scenarios.taskAutomation.title",
    detailKey: "capabilities.scenarios.taskAutomation.detail",
    promptKey: "capabilities.scenarios.taskAutomation.prompt",
  },
  {
    id: "custom",
    icon: "code",
    titleKey: "capabilities.scenarios.custom.title",
    detailKey: "capabilities.scenarios.custom.detail",
    promptKey: "capabilities.scenarios.custom.prompt",
  },
];
