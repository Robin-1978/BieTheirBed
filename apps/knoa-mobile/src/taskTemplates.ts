import type { MessageKey } from "@/i18n";

export type TaskTemplate = {
  id: string;
  titleKey: MessageKey;
  detailKey: MessageKey;
  goalKey: MessageKey;
};

export const TASK_TEMPLATES: TaskTemplate[] = [
  { id: "gitlab-pipeline", titleKey: "taskTemplates.gitlabTitle", detailKey: "taskTemplates.gitlabDetail", goalKey: "taskTemplates.gitlabGoal" },
  { id: "jira-summary", titleKey: "taskTemplates.jiraTitle", detailKey: "taskTemplates.jiraDetail", goalKey: "taskTemplates.jiraGoal" },
  { id: "folder-organizer", titleKey: "taskTemplates.folderTitle", detailKey: "taskTemplates.folderDetail", goalKey: "taskTemplates.folderGoal" },
  { id: "service-monitor", titleKey: "taskTemplates.serviceTitle", detailKey: "taskTemplates.serviceDetail", goalKey: "taskTemplates.serviceGoal" },
  { id: "work-summary", titleKey: "taskTemplates.summaryTitle", detailKey: "taskTemplates.summaryDetail", goalKey: "taskTemplates.summaryGoal" },
  { id: "media-review", titleKey: "taskTemplates.mediaTitle", detailKey: "taskTemplates.mediaDetail", goalKey: "taskTemplates.mediaGoal" },
];
