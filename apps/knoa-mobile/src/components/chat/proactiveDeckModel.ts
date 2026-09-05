export type ActionIconName = "folder" | "pulse" | "code" | "globe";

export interface ActionItem {
  key: string;
  icon: ActionIconName;
  titleKey: "chat.deckActionClean" | "chat.deckActionHealth" | "chat.deckActionGit" | "chat.deckActionBriefing";
  descKey: "chat.deckActionCleanDesc" | "chat.deckActionHealthDesc" | "chat.deckActionGitDesc" | "chat.deckActionBriefingDesc";
  prompt: string;
  taskTitle: string;
}

export const DECK_ACTIONS: ActionItem[] = [
  {
    key: "clean",
    icon: "folder",
    titleKey: "chat.deckActionClean",
    descKey: "chat.deckActionCleanDesc",
    prompt: "请扫描我的电脑桌面与下载文件夹，检查是否有重复、杂乱的文件或截图，并给出分类整理方案",
    taskTitle: "自动整理桌面与下载文件",
  },
  {
    key: "health",
    icon: "pulse",
    titleKey: "chat.deckActionHealth",
    descKey: "chat.deckActionHealthDesc",
    prompt: "请检查本机系统健康状态，包括磁盘剩余空间、CPU使用率、内存占用以及是否有异常服务",
    taskTitle: "电脑系统健康与磁盘巡检",
  },
  {
    key: "git",
    icon: "code",
    titleKey: "chat.deckActionGit",
    descKey: "chat.deckActionGitDesc",
    prompt: "请检查当前工作目录的 Git 状态，拉取最新代码并运行自动化测试套件",
    taskTitle: "代码仓库拉取与测试验证",
  },
  {
    key: "briefing",
    icon: "globe",
    titleKey: "chat.deckActionBriefing",
    descKey: "chat.deckActionBriefingDesc",
    prompt: "请搜集今日 AI、前沿科技与行业热点要闻，整理并排版成一份结构化的晨间早报",
    taskTitle: "今日前沿技术与热点早报",
  },
];
