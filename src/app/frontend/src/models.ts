export type UserProfile = {
  id: string;
  email: string;
  name: string;
  picture?: string | null;
  defaultModelId?: string;
  preferences?: Record<string, unknown>;
};

export type ModelOption = {
  id: string;
  name: string;
  description: string;
};

export type AttachmentRef = {
  id: string;
  filename: string;
  contentType: string;
  sizeBytes: number;
  indexingStatus: "pending" | "indexed" | "failed";
};

export type ConversationSummary = {
  id: string;
  title: string;
  modelId: string;
  createdAt: string;
  updatedAt: string;
};

export type RunSummary = {
  id: string;
  status: RunStatus;
  modelId: string;
};

export type ChatRunRequest = {
  modelId: string;
  content: string;
  attachments: string[];
};

export type HumanInputField = {
  id: string;
  label: string;
  kind: string;
  description?: string;
  placeholder?: string;
  required?: boolean;
  constraints?: Record<string, unknown>;
  options?: { label: string; value: string }[];
};

export type HumanInputAction = {
  id: string;
  label: string;
};

export type HumanInputRequest = {
  requestId: string;
  runId: string;
  title: string;
  description?: string;
  fields: HumanInputField[];
  actions: HumanInputAction[];
};

export type HumanInputSubmission = {
  requestId: string;
  runId: string;
  actionId: string;
  values: Record<string, unknown>;
};

export type HumanInputResponse = HumanInputSubmission & {
  actionLabel: string;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  attachments?: string[];
  activity?: string[];
  incomplete?: boolean;
  humanInput?: {
    request: HumanInputRequest;
    response?: HumanInputResponse;
  };
  humanInputResponse?: HumanInputResponse;
};

export type RunStatus =
  | "queued"
  | "running"
  | "waiting_for_input"
  | "completed"
  | "cancelled"
  | "failed";

export type RunEvent =
  {
    type:
      | "status"
      | "assistant_delta"
      | "tool_call"
      | "tool_result"
      | "human_input_request"
      | "completed"
      | "cancelled"
      | "error";
    conversationId: string;
    runId: string;
    sequence: number;
    correlationId?: string | null;
    messageId?: string | null;
    payload: Record<string, unknown> & { status?: RunStatus; delta?: string; name?: string; message?: string };
  };

export type ApiError = {
  detail: string;
  code?: string;
  requestId?: string;
};
