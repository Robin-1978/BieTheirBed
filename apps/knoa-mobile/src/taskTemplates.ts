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
  { id: "project-maintenance", titleKey: "taskTemplates.projectTitle", detailKey: "taskTemplates.projectDetail", goalKey: "taskTemplates.projectGoal", connectionKey: "taskTemplates.projectConnection", permissionKey: "taskTemplates.projectPermission", durationKey: "taskTemplates.projectDuration", resultKey: "taskTemplates.projectResult", failureKey: "taskTemplates.projectFailure", notificationKey: "taskTemplates.projectNotification" },
  { id: "computer-health", titleKey: "taskTemplates.healthTitle", detailKey: "taskTemplates.healthDetail", goalKey: "taskTemplates.healthGoal", connectionKey: "taskTemplates.healthConnection", permissionKey: "taskTemplates.healthPermission", durationKey: "taskTemplates.healthDuration", resultKey: "taskTemplates.healthResult", failureKey: "taskTemplates.healthFailure", notificationKey: "taskTemplates.healthNotification" },
  { id: "folder-organizer", titleKey: "taskTemplates.folderTitle", detailKey: "taskTemplates.folderDetail", goalKey: "taskTemplates.folderGoal", connectionKey: "taskTemplates.folderConnection", permissionKey: "taskTemplates.folderPermission", durationKey: "taskTemplates.folderDuration", resultKey: "taskTemplates.folderResult", failureKey: "taskTemplates.folderFailure", notificationKey: "taskTemplates.folderNotification" },
  { id: "service-monitor", titleKey: "taskTemplates.serviceTitle", detailKey: "taskTemplates.serviceDetail", goalKey: "taskTemplates.serviceGoal", connectionKey: "taskTemplates.serviceConnection", permissionKey: "taskTemplates.servicePermission", durationKey: "taskTemplates.serviceDuration", resultKey: "taskTemplates.serviceResult", failureKey: "taskTemplates.serviceFailure", notificationKey: "taskTemplates.serviceNotification" },
  { id: "work-summary", titleKey: "taskTemplates.summaryTitle", detailKey: "taskTemplates.summaryDetail", goalKey: "taskTemplates.summaryGoal", connectionKey: "taskTemplates.summaryConnection", permissionKey: "taskTemplates.summaryPermission", durationKey: "taskTemplates.summaryDuration", resultKey: "taskTemplates.summaryResult", failureKey: "taskTemplates.summaryFailure", notificationKey: "taskTemplates.summaryNotification" },
  { id: "media-review", titleKey: "taskTemplates.mediaTitle", detailKey: "taskTemplates.mediaDetail", goalKey: "taskTemplates.mediaGoal", connectionKey: "taskTemplates.mediaConnection", permissionKey: "taskTemplates.mediaPermission", durationKey: "taskTemplates.mediaDuration", resultKey: "taskTemplates.mediaResult", failureKey: "taskTemplates.mediaFailure", notificationKey: "taskTemplates.mediaNotification" },
  { id: "document-digest", titleKey: "taskTemplates.documentTitle", detailKey: "taskTemplates.documentDetail", goalKey: "taskTemplates.documentGoal", connectionKey: "taskTemplates.documentConnection", permissionKey: "taskTemplates.documentPermission", durationKey: "taskTemplates.documentDuration", resultKey: "taskTemplates.documentResult", failureKey: "taskTemplates.documentFailure", notificationKey: "taskTemplates.documentNotification" },
  { id: "research-brief", titleKey: "taskTemplates.researchTitle", detailKey: "taskTemplates.researchDetail", goalKey: "taskTemplates.researchGoal", connectionKey: "taskTemplates.researchConnection", permissionKey: "taskTemplates.researchPermission", durationKey: "taskTemplates.researchDuration", resultKey: "taskTemplates.researchResult", failureKey: "taskTemplates.researchFailure", notificationKey: "taskTemplates.researchNotification" },
];
