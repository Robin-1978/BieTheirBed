export type ActionCardLevel = "info" | "success" | "warning" | "critical";

export type ActionCardStatus = "pending" | "approved" | "rejected" | "executed" | "expired";

export type ActionCardSource = {
  plugin_name: string;
  agent_name?: string;
  created_at: number;
  expires_at?: number;
};

export type CardMarkdownBlock = {
  type: "markdown";
  content: string;
};

export type CardKeyValueItem = {
  key: string;
  value: string;
  style?: "default" | "bold" | "code" | "badge" | "muted";
};

export type CardKeyValueBlock = {
  type: "key_value";
  items: CardKeyValueItem[];
};

export type CardCodeDiffBlock = {
  type: "code_diff";
  filename: string;
  language?: string;
  unified_diff: string;
};

export type CardCalloutBlock = {
  type: "callout";
  level: "info" | "warning" | "error";
  text: string;
};

export type CardArtifactBlock = {
  type: "artifact_link";
  name: string;
  url: string;
  size_bytes?: number;
  mime_type?: string;
};

export type ActionCardBlock =
  | CardMarkdownBlock
  | CardKeyValueBlock
  | CardCodeDiffBlock
  | CardCalloutBlock
  | CardArtifactBlock;

export type ActionCardInput = {
  id: string;
  label: string;
  input_type: "text" | "textarea" | "select" | "switch";
  placeholder?: string;
  default_value?: string | boolean;
  required?: boolean;
  options?: Array<{ label: string; value: string }>;
};

export type ActionCardButton = {
  id: string;
  label: string;
  style?: "primary" | "secondary" | "danger" | "outline";
  action_type?: "invoke_tool" | "approve" | "reject" | "dismiss" | "open_url";
  tool_name?: string;
  arguments?: Record<string, unknown>;
  url?: string;
  confirm_dialog?: {
    title: string;
    message: string;
    confirm_text?: string;
    cancel_text?: string;
  };
  include_form_inputs?: boolean;
};

export type ActionCard = {
  schema_version: string;
  card_id: string;
  title: string;
  subtitle?: string;
  level: ActionCardLevel;
  status: ActionCardStatus;
  source: ActionCardSource;
  blocks: ActionCardBlock[];
  inputs?: ActionCardInput[];
  actions?: ActionCardButton[];
  metadata?: Record<string, unknown>;
};

export type ActionCardInvocation = {
  card_id: string;
  action_id: string;
  action_type: string;
  tool_name?: string;
  arguments?: Record<string, unknown>;
};
