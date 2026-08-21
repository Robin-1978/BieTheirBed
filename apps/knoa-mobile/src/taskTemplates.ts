import type { MessageKey } from "@/i18n";

export type TaskTemplate = {
  id: string;
  titleKey: MessageKey;
  detailKey: MessageKey;
  goalKey: MessageKey;
  connectionKey: MessageKey;
  permissionKey: MessageKey;
  durationKey: MessageKey;
  resultKey: MessageKey;
  failureKey: MessageKey;
  notificationKey: MessageKey;
};

export const TASK_TEMPLATES: TaskTemplate[] = [
  { id: "gitlab-pipeline", titleKey: "taskTemplates.gitlabTitle", detailKey: "taskTemplates.gitlabDetail", goalKey: "taskTemplates.gitlabGoal", connectionKey: "taskTemplates.gitlabConnection", permissionKey: "taskTemplates.gitlabPermission", durationKey: "taskTemplates.gitlabDuration", resultKey: "taskTemplates.gitlabResult", failureKey: "taskTemplates.gitlabFailure", notificationKey: "taskTemplates.gitlabNotification" },
  { id: "jira-summary", titleKey: "taskTemplates.jiraTitle", detailKey: "taskTemplates.jiraDetail", goalKey: "taskTemplates.jiraGoal", connectionKey: "taskTemplates.jiraConnection", permissionKey: "taskTemplates.jiraPermission", durationKey: "taskTemplates.jiraDuration", resultKey: "taskTemplates.jiraResult", failureKey: "taskTemplates.jiraFailure", notificationKey: "taskTemplates.jiraNotification" },
  { id: "folder-organizer", titleKey: "taskTemplates.folderTitle", detailKey: "taskTemplates.folderDetail", goalKey: "taskTemplates.folderGoal", connectionKey: "taskTemplates.folderConnection", permissionKey: "taskTemplates.folderPermission", durationKey: "taskTemplates.folderDuration", resultKey: "taskTemplates.folderResult", failureKey: "taskTemplates.folderFailure", notificationKey: "taskTemplates.folderNotification" },
  { id: "service-monitor", titleKey: "taskTemplates.serviceTitle", detailKey: "taskTemplates.serviceDetail", goalKey: "taskTemplates.serviceGoal", connectionKey: "taskTemplates.serviceConnection", permissionKey: "taskTemplates.servicePermission", durationKey: "taskTemplates.serviceDuration", resultKey: "taskTemplates.serviceResult", failureKey: "taskTemplates.serviceFailure", notificationKey: "taskTemplates.serviceNotification" },
  { id: "work-summary", titleKey: "taskTemplates.summaryTitle", detailKey: "taskTemplates.summaryDetail", goalKey: "taskTemplates.summaryGoal", connectionKey: "taskTemplates.summaryConnection", permissionKey: "taskTemplates.summaryPermission", durationKey: "taskTemplates.summaryDuration", resultKey: "taskTemplates.summaryResult", failureKey: "taskTemplates.summaryFailure", notificationKey: "taskTemplates.summaryNotification" },
  { id: "media-review", titleKey: "taskTemplates.mediaTitle", detailKey: "taskTemplates.mediaDetail", goalKey: "taskTemplates.mediaGoal", connectionKey: "taskTemplates.mediaConnection", permissionKey: "taskTemplates.mediaPermission", durationKey: "taskTemplates.mediaDuration", resultKey: "taskTemplates.mediaResult", failureKey: "taskTemplates.mediaFailure", notificationKey: "taskTemplates.mediaNotification" },
  { id: "approval-event", titleKey: "taskTemplates.approvalTitle", detailKey: "taskTemplates.approvalDetail", goalKey: "taskTemplates.approvalGoal", connectionKey: "taskTemplates.approvalConnection", permissionKey: "taskTemplates.approvalPermission", durationKey: "taskTemplates.approvalDuration", resultKey: "taskTemplates.approvalResult", failureKey: "taskTemplates.approvalFailure", notificationKey: "taskTemplates.approvalNotification" },
];
