import { chat } from "./chat";
import { common } from "./common";
import { settings } from "./settings";
import { tasks } from "./tasks";
import { workspaces } from "./workspaces";

export const en = {
  ...common,
  ...chat,
  ...tasks,
  ...settings,
  ...workspaces,
} as const;
