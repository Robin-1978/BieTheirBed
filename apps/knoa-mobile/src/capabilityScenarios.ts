import type { AppIconName } from "@/components/AppIcon";
import type { MessageKey } from "@/i18n";

export type CapabilityScenario = {
  id: string;
  icon: AppIconName;
  titleKey: MessageKey;
  detailKey: MessageKey;
  promptKey: MessageKey;
};

export const CAPABILITY_SCENARIOS: CapabilityScenario[] = [
  {
    id: "file-organization",
    icon: "folder",
    titleKey: "capabilities.scenarios.fileOrganization.title",
    detailKey: "capabilities.scenarios.fileOrganization.detail",
    promptKey: "capabilities.scenarios.fileOrganization.prompt",
  },
  {
    id: "health-check",
    icon: "pulse",
    titleKey: "capabilities.scenarios.healthCheck.title",
    detailKey: "capabilities.scenarios.healthCheck.detail",
    promptKey: "capabilities.scenarios.healthCheck.prompt",
  },
  {
    id: "web-research",
    icon: "globe",
    titleKey: "capabilities.scenarios.webResearch.title",
    detailKey: "capabilities.scenarios.webResearch.detail",
    promptKey: "capabilities.scenarios.webResearch.prompt",
  },
  {
    id: "monitor",
    icon: "eye",
    titleKey: "capabilities.scenarios.monitor.title",
    detailKey: "capabilities.scenarios.monitor.detail",
    promptKey: "capabilities.scenarios.monitor.prompt",
  },
  {
    id: "image-docs",
    icon: "image",
    titleKey: "capabilities.scenarios.imageDocs.title",
    detailKey: "capabilities.scenarios.imageDocs.detail",
    promptKey: "capabilities.scenarios.imageDocs.prompt",
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
