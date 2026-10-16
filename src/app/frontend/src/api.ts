import type {
  AttachmentRef,
  ChatMessage,
  ConversationSummary,
  ModelOption,
  RunEvent,
  RunSummary,
  UserProfile,
} from "./models";

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");

export type Conversation = ConversationSummary & {
  messages: ChatMessage[];
  runs: RunSummary[];
};

export type CreatedRun = {
  runId: string;
  conversationId: string;
  userMessageId: string;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...init,
    credentials: "include",
  });
  if (!response.ok) {
    let detail = `Request failed (${response.status}).`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) {
        const messages = body.detail.flatMap((issue: unknown) => {
          if (
            typeof issue !== "object"
            || issue === null
            || !("msg" in issue)
            || typeof issue.msg !== "string"
          ) return [];
          const location = "loc" in issue && Array.isArray(issue.loc)
            ? issue.loc.filter((part): part is string | number => typeof part === "string" || typeof part === "number")
                .slice(1)
                .join(".")
            : "";
          return [location ? `${location}: ${issue.msg}` : issue.msg];
        });
        if (messages.length) detail = messages.join("; ");
      }
    } catch {
      // Keep the status-based error when the server did not return JSON.
    }
    throw new Error(detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  baseUrl: apiBaseUrl,

  currentUser: () => request<UserProfile>("/auth/me"),
  signOut: () => request<void>("/auth/logout", { method: "POST" }),
  listModels: () => request<ModelOption[]>("/api/models"),
  listConversations: () => request<ConversationSummary[]>("/api/conversations"),
  readConversation: (id: string) => request<Conversation>(`/api/conversations/${encodeURIComponent(id)}`),
  createConversation: (modelId: string) =>
    request<ConversationSummary>("/api/conversations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ modelId }),
    }),
  deleteConversation: (id: string, stopActiveRun = false) =>
    request<void>(
      `/api/conversations/${encodeURIComponent(id)}?stopActiveRun=${stopActiveRun}`,
      { method: "DELETE" },
    ),
  uploadAttachment: async (conversationId: string, file: File) => {
    const formData = new FormData();
    formData.set("file", file);
    return request<AttachmentRef>(
      `/api/conversations/${encodeURIComponent(conversationId)}/attachments`,
      { method: "POST", body: formData },
    );
  },
  createRun: (conversationId: string, modelId: string, content: string, attachments: string[]) =>
    request<CreatedRun>(`/api/conversations/${encodeURIComponent(conversationId)}/runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ modelId, content, attachments }),
    }),
  cancelRun: (runId: string) =>
    request<{ runId: string; status: string }>(`/api/runs/${encodeURIComponent(runId)}/cancel`, {
      method: "POST",
    }),
};

function delay(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    const timeout = window.setTimeout(resolve, milliseconds);
    signal.addEventListener(
      "abort",
      () => {
        window.clearTimeout(timeout);
        reject(new DOMException("Aborted", "AbortError"));
      },
      { once: true },
    );
  });
}

export async function streamRunEvents(
  runId: string,
  afterSequence: number,
  signal: AbortSignal,
  onEvent: (event: RunEvent) => void,
): Promise<void> {
  let cursor = afterSequence;
  let terminal = false;
  let retryDelay = 350;

  while (!terminal && !signal.aborted) {
    try {
      const response = await fetch(
        `${apiBaseUrl}/api/runs/${encodeURIComponent(runId)}/events?afterSequence=${cursor}`,
        { credentials: "include", signal, headers: { Accept: "text/event-stream" } },
      );
      if (!response.ok) {
        const body = (await response.json()) as { detail?: unknown };
        throw new Error(typeof body.detail === "string" ? body.detail : `Event stream failed (${response.status}).`);
      }
      if (!response.body) throw new Error("The event stream is not available in this browser.");

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      retryDelay = 350;
      while (!terminal) {
        const { value, done } = await reader.read();
        buffer = (buffer + decoder.decode(value, { stream: !done })).replace(/\r\n/g, "\n");
        let boundary = buffer.indexOf("\n\n");
        while (boundary >= 0) {
          const frame = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          const data = frame
            .split(/\r?\n/)
            .filter((line) => line.startsWith("data:"))
            .map((line) => line.slice(5).trim())
            .join("\n");
          if (data) {
            const event = JSON.parse(data) as RunEvent;
            if (event.sequence > cursor) {
              cursor = event.sequence;
              onEvent(event);
            }
            terminal = event.type === "completed" || event.type === "cancelled" || event.type === "error";
          }
          boundary = buffer.indexOf("\n\n");
        }
        if (done) break;
      }
      if (terminal) return;
    } catch (error) {
      if (signal.aborted || (error instanceof DOMException && error.name === "AbortError")) return;
      if (!(error instanceof TypeError)) throw error;
    }
    await delay(retryDelay, signal);
    retryDelay = Math.min(retryDelay * 2, 5_000);
  }
}
