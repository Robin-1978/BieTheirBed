import * as SecureStore from "expo-secure-store";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type PropsWithChildren } from "react";

export type LanguageMode = "system" | "zh-CN" | "en-US";
type MessageParams = Record<string, string | number>;

const STORAGE_KEY = "knoa.ui.language.v1";

const zh = {
  "app.name": "小诺",
  "nav.connect": "连接小诺",
  "nav.conversations": "会话记录",
  "nav.newTask": "新建任务",
  "nav.taskDetails": "任务详情",
  "nav.editTask": "编辑任务",
  "nav.executionDetails": "执行详情",
  "nav.settings": "设置与状态",
  "nav.capture": "拍照",
  "nav.update": "版本与更新",
  "header.chat": "对话",
  "header.tasks": "任务",
  "settings.appearance": "外观",
  "settings.appearanceHint": "选择后立即应用到整个 App。",
  "settings.theme.system": "跟随系统",
  "settings.theme.light": "暖色浅色",
  "settings.theme.dark": "深色",
  "settings.language": "语言",
  "settings.languageHint": "界面语言可跟随手机，也可以单独指定。",
  "settings.language.system": "跟随系统",
  "settings.language.zh": "简体中文",
  "settings.language.en": "English",
  "splash.waking": "正在唤醒小诺",
  "splash.unavailable": "暂时连接不上小诺",
  "splash.restoring": "正在恢复你的安全连接和会话",
  "common.reconnect": "重新连接",
  "common.settings": "设置与状态",
  "chat.subtitle": "随时告诉我你想做什么",
  "chat.newTopic": "新话题",
  "chat.history": "会话记录",
  "chat.empty": "你好，我是小诺。",
  "chat.placeholder": "和小诺说点什么…",
  "chat.voicePlaceholder": "语音转写会出现在这里",
  "chat.add": "添加照片或文件",
  "chat.send": "发送",
  "chat.stop": "停止回复",
  "chat.startRecording": "开始录音",
  "chat.stopRecording": "停止录音",
  "chat.switchVoice": "切换到语音输入",
  "chat.switchText": "切换到文字输入",
  "chat.loadOlder": "加载更早消息",
  "chat.camera": "拍照",
  "chat.file": "文件",
  "chat.addContent": "添加内容",
  "chat.disconnected": "暂时没有连接到小诺",
  "chat.reconnecting": "正在重新建立安全连接",
  "chat.retryConnection": "重连",
  "tasks.title": "任务",
  "tasks.description": "管理目标、启动方式和每次执行结果",
  "tasks.new": "创建新任务",
  "tasks.filter.current": "当前",
  "tasks.filter.active": "启用",
  "tasks.filter.paused": "已暂停",
  "tasks.filter.archived": "已归档",
  "tasks.emptyTitle": "这里还没有任务",
  "tasks.emptyBody": "创建任务后，可以反复执行并保留每次结果。",
  "tasks.executions": "执行 {count} 次",
  "tasks.reload": "重新加载",
} as const;

const en: Partial<Record<keyof typeof zh, string>> = {
  "app.name": "Knoa",
  "nav.connect": "Connect to Knoa",
  "nav.conversations": "Conversations",
  "nav.newTask": "New Task",
  "nav.taskDetails": "Task Details",
  "nav.editTask": "Edit Task",
  "nav.executionDetails": "Execution Details",
  "nav.settings": "Settings & Status",
  "nav.capture": "Camera",
  "nav.update": "Version & Updates",
  "header.chat": "Chat",
  "header.tasks": "Tasks",
  "settings.appearance": "Appearance",
  "settings.appearanceHint": "Changes apply throughout the app immediately.",
  "settings.theme.system": "System",
  "settings.theme.light": "Warm Light",
  "settings.theme.dark": "Dark",
  "settings.language": "Language",
  "settings.languageHint": "Follow your phone language or choose one for the app.",
  "settings.language.system": "System",
  "settings.language.zh": "简体中文",
  "settings.language.en": "English",
  "splash.waking": "Waking Knoa",
  "splash.unavailable": "Knoa is unavailable",
  "splash.restoring": "Restoring your secure connection and conversation",
  "common.reconnect": "Reconnect",
  "common.settings": "Settings & Status",
  "chat.subtitle": "Tell me what you want to do",
  "chat.newTopic": "New topic",
  "chat.history": "Conversation history",
  "chat.empty": "Hi, I'm Knoa.",
  "chat.placeholder": "Message Knoa…",
  "chat.voicePlaceholder": "Your transcript will appear here",
  "chat.add": "Add a photo or file",
  "chat.send": "Send",
  "chat.stop": "Stop response",
  "chat.startRecording": "Start recording",
  "chat.stopRecording": "Stop recording",
  "chat.switchVoice": "Switch to voice input",
  "chat.switchText": "Switch to keyboard",
  "chat.loadOlder": "Load earlier messages",
  "chat.camera": "Camera",
  "chat.file": "File",
  "chat.addContent": "Add content",
  "chat.disconnected": "Knoa is not connected",
  "chat.reconnecting": "Re-establishing a secure connection",
  "chat.retryConnection": "Reconnect",
  "tasks.title": "Tasks",
  "tasks.description": "Manage goals, triggers, and execution results",
  "tasks.new": "Create a task",
  "tasks.filter.current": "Current",
  "tasks.filter.active": "Active",
  "tasks.filter.paused": "Paused",
  "tasks.filter.archived": "Archived",
  "tasks.emptyTitle": "No tasks yet",
  "tasks.emptyBody": "Create a task to run it repeatedly and keep every result.",
  "tasks.executions": "{count} runs",
  "tasks.reload": "Reload",
};

type MessageKey = keyof typeof zh;
type I18nValue = {
  mode: LanguageMode;
  locale: "zh-CN" | "en-US";
  t(key: MessageKey, params?: MessageParams): string;
  setMode(mode: LanguageMode): Promise<void>;
};

const systemLocale = (): "zh-CN" | "en-US" => Intl.DateTimeFormat().resolvedOptions().locale.toLowerCase().startsWith("zh") ? "zh-CN" : "en-US";
const I18nContext = createContext<I18nValue | null>(null);

export function I18nProvider({ children }: PropsWithChildren) {
  const [mode, setStoredMode] = useState<LanguageMode>("system");
  const locale = mode === "system" ? systemLocale() : mode;
  const t = useCallback((key: MessageKey, params: MessageParams = {}) => {
    const template = locale === "en-US" ? en[key] ?? zh[key] : zh[key];
    return Object.entries(params).reduce((value, [name, replacement]) => value.replaceAll(`{${name}}`, String(replacement)), template);
  }, [locale]);

  useEffect(() => {
    let active = true;
    void SecureStore.getItemAsync(STORAGE_KEY).then((stored) => {
      if (active && (stored === "system" || stored === "zh-CN" || stored === "en-US")) setStoredMode(stored);
    });
    return () => { active = false; };
  }, []);

  const setMode = useCallback(async (next: LanguageMode) => {
    setStoredMode(next);
    await SecureStore.setItemAsync(STORAGE_KEY, next);
  }, []);

  const value = useMemo(() => ({ mode, locale, t, setMode }), [locale, mode, setMode, t]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  const value = useContext(I18nContext);
  if (!value) throw new Error("I18nProvider is missing");
  return value;
}
