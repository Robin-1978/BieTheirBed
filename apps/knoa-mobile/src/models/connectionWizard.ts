export type BusinessConnectionKind = "jira" | "gitlab" | "feishu" | "dingtalk" | "custom";

export type BusinessConnectionDescriptor = {
  kind: BusinessConnectionKind;
  titleKey: string;
  detailKey: string;
  defaultServerId: string;
  capabilities: string[];
  requiresPublicCallback: boolean;
};

export const BUSINESS_CONNECTIONS: BusinessConnectionDescriptor[] = [
  { kind: "jira", titleKey: "connections.jira", detailKey: "connections.jiraDetail", defaultServerId: "jira", capabilities: ["待办查询", "Issue 状态", "评论"], requiresPublicCallback: false },
  { kind: "gitlab", titleKey: "connections.gitlab", detailKey: "connections.gitlabDetail", defaultServerId: "gitlab", capabilities: ["Pipeline", "Merge Request", "项目文件"], requiresPublicCallback: false },
  { kind: "feishu", titleKey: "connections.feishu", detailKey: "connections.feishuDetail", defaultServerId: "feishu", capabilities: ["消息", "文件", "审批"], requiresPublicCallback: false },
  { kind: "dingtalk", titleKey: "connections.dingtalk", detailKey: "connections.dingtalkDetail", defaultServerId: "dingtalk", capabilities: ["Stream 消息", "文件", "审批"], requiresPublicCallback: false },
  { kind: "custom", titleKey: "connections.custom", detailKey: "connections.customDetail", defaultServerId: "", capabilities: [], requiresPublicCallback: false },
];

export function connectionDescriptor(kind: BusinessConnectionKind): BusinessConnectionDescriptor {
  return BUSINESS_CONNECTIONS.find((item) => item.kind === kind) ?? BUSINESS_CONNECTIONS[BUSINESS_CONNECTIONS.length - 1]!;
}
