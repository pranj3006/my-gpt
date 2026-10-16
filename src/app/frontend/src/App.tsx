import { useEffect, useRef, useState } from "react";
import type { ChangeEvent, FormEvent, KeyboardEvent } from "react";
import type {
  AttachmentRef,
  ChatMessage,
  HumanInputField,
  HumanInputRequest,
  HumanInputSubmission,
  ModelOption,
  RunEvent,
  RunStatus,
  UserProfile,
} from "./models";
import { api, streamRunEvents } from "./api";
import {
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  CircleAlert,
  Check,
  CheckCircle2,
  ChevronDown,
  Clipboard,
  FileText,
  LogOut,
  Menu,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Search,
  Settings2,
  Sparkles,
  SquarePen,
  StopCircle,
  Trash2,
  X,
  Zap,
} from "lucide-react";

type Chat = {
  id: string;
  title: string;
  updatedAt: number;
  messages: ChatMessage[];
  runId?: string;
  runStatus?: RunStatus;
  runActivity?: string;
  pendingHumanInput?: HumanInputRequest;
};

type PendingAttachment = {
  key: string;
  file: File;
  conversationId?: string;
  status: "uploading" | "indexed" | "failed";
  attachment?: AttachmentRef;
  error?: string;
};

const suggestions = [
  { icon: "✳", title: "Make a plan", prompt: "Help me make a plan for the week ahead." },
  { icon: "⌘", title: "Write something", prompt: "Help me write a thoughtful introduction for a new project." },
  { icon: "⌁", title: "Explore an idea", prompt: "Explain a fascinating idea in a way that's easy to understand." },
  { icon: "⌕", title: "Get advice", prompt: "I have a decision to make. Can you help me think it through?" },
];

const activeStatuses: RunStatus[] = ["queued", "running", "waiting_for_input"];

function fromSummary(summary: { id: string; title: string; updatedAt: string }): Chat {
  return {
    id: summary.id,
    title: summary.title,
    updatedAt: new Date(summary.updatedAt).getTime(),
    messages: [],
  };
}

function isHumanInputRequest(value: unknown): value is HumanInputRequest {
  if (typeof value !== "object" || value === null) return false;
  const request = value as Record<string, unknown>;
  return (
    typeof request.requestId === "string"
    && typeof request.runId === "string"
    && typeof request.title === "string"
    && Array.isArray(request.fields)
    && Array.isArray(request.actions)
  );
}

function App() {
  const [chats, setChats] = useState<Chat[]>([]);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [user, setUser] = useState<UserProfile | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [authError, setAuthError] = useState("");
  const [activeId, setActiveId] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [selectedModel, setSelectedModel] = useState<ModelOption | null>(null);
  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const [isSending, setIsSending] = useState(false);
  const [deletingChatIds, setDeletingChatIds] = useState<Set<string>>(new Set());
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ message: string; kind: "error" | "success" } | null>(null);
  const [search, setSearch] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [accountView, setAccountView] = useState<"chat" | "profile">("chat");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const noticeTimer = useRef<number | undefined>(undefined);
  const streamControllers = useRef(new Map<string, AbortController>());
  const activeChat = chats.find((chat) => chat.id === activeId);
  const isEmptyChat = !activeChat || activeChat.messages.length === 0;
  const isGenerating = Boolean(activeChat?.runId && activeStatuses.includes(activeChat.runStatus ?? "running"));

  useEffect(() => {
    const controller = new AbortController();
    const callbackError = new URLSearchParams(window.location.search).get("auth_error");
    if (callbackError) {
      setAuthError(
        callbackError === "sign_in_cancelled"
          ? "Google sign-in was cancelled."
          : "Google sign-in couldn't be completed. Please try again.",
      );
      window.history.replaceState(null, "", window.location.pathname);
    }

    void api.currentUser().then(setUser).catch((error: unknown) => {
      if (error instanceof Error && error.message === "Not authenticated.") return;
      if (!controller.signal.aborted) {
        console.error("Unable to restore the authenticated session.", error);
        setAuthError("Couldn't connect to the sign-in service. Please try again later.");
      }
    }).finally(() => {
      if (!controller.signal.aborted) setAuthLoading(false);
    });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!user) {
      setChats([]);
      setModels([]);
      setSelectedModel(null);
      setActiveId(null);
      setHistoryLoading(false);
      return;
    }
    const controller = new AbortController();
    setHistoryLoading(true);
    void Promise.all([api.listConversations(), api.listModels()])
      .then(([conversationList, modelOptions]) => {
        if (controller.signal.aborted) return;
        setChats(conversationList.map(fromSummary));
        setModels(modelOptions);
        setSelectedModel(
          modelOptions.find((model) => model.id === user.defaultModelId) ?? modelOptions[0] ?? null,
        );
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        console.error("Unable to load conversations or model options.", error);
        showNotice(error instanceof Error ? error.message : "Couldn't load your chats.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setHistoryLoading(false);
      });
    return () => controller.abort();
  }, [user]);

  useEffect(
    () => () => {
      window.clearTimeout(noticeTimer.current);
      for (const controller of streamControllers.current.values()) controller.abort();
      streamControllers.current.clear();
    },
    [],
  );

  function showNotice(message: string, kind: "error" | "success" = "error") {
    setNotice({ message, kind });
    window.clearTimeout(noticeTimer.current);
    noticeTimer.current = window.setTimeout(() => setNotice(null), 4000);
  }

  function signIn() {
    window.location.assign(`${api.baseUrl}/auth/login`);
  }

  async function signOut() {
    try {
      await api.signOut();
      for (const controller of streamControllers.current.values()) controller.abort();
      streamControllers.current.clear();
      setUser(null);
      setAccountView("chat");
    } catch (error) {
      console.error("Unable to sign out.", error);
      showNotice(error instanceof Error ? error.message : "Couldn't sign out. Please try again.");
    }
  }

  function updateChat(id: string, updater: (chat: Chat) => Chat) {
    setChats((existing) => existing.map((chat) => (chat.id === id ? updater(chat) : chat)));
  }

  async function refreshConversation(chatId: string) {
    try {
      const conversation = await api.readConversation(chatId);
      setChats((existing) => {
        const refreshed: Chat = {
          id: conversation.id,
          title: conversation.title,
          updatedAt: new Date(conversation.updatedAt).getTime(),
          messages: conversation.messages,
          runId: conversation.runs.find((run) => activeStatuses.includes(run.status))?.id,
          runStatus: conversation.runs.find((run) => activeStatuses.includes(run.status))?.status
            ?? conversation.runs.at(-1)?.status,
        };
        return existing.some((item) => item.id === chatId)
          ? existing.map((item) => item.id === chatId ? refreshed : item)
          : [refreshed, ...existing];
      });
      const activeRun = conversation.runs.find((run) => activeStatuses.includes(run.status));
      if (activeRun) watchRun(chatId, activeRun.id, -1);
    } catch (error) {
      console.error("Unable to refresh conversation.", error);
      showNotice(error instanceof Error ? error.message : "Couldn't refresh the conversation.");
    }
  }

  function applyRunEvent(chatId: string, event: RunEvent) {
    updateChat(chatId, (chat) => {
      let messages = chat.messages;
      let runActivity = chat.runActivity;
      let runStatus = chat.runStatus;
      if (event.type === "status" && event.payload.status) {
        runStatus = event.payload.status;
        runActivity = event.payload.status === "queued"
          ? "Waiting to start"
          : event.payload.status === "waiting_for_input"
            ? "Waiting for your input"
            : activeStatuses.includes(event.payload.status)
              ? "Working…"
              : "";
        if (event.payload.status !== "waiting_for_input") {
          chat = { ...chat, pendingHumanInput: undefined };
        }
      } else if (event.type === "assistant_delta" && typeof event.payload.delta === "string") {
        const streamMessageId = `stream-${event.runId}`;
        const existing = messages.find((message) => message.id === streamMessageId);
        messages = existing
          ? messages.map((message) =>
              message.id === streamMessageId
                ? { ...message, content: message.content + event.payload.delta }
                : message,
            )
          : [...messages, { id: streamMessageId, role: "assistant", content: event.payload.delta }];
        runActivity = "";
      } else if (event.type === "human_input_request" && isHumanInputRequest(event.payload)) {
        chat = { ...chat, pendingHumanInput: event.payload };
        runStatus = "waiting_for_input";
        runActivity = "";
      } else if (event.type === "tool_call") {
        runActivity = `Using ${typeof event.payload.name === "string" ? event.payload.name : "a tool"}`;
      } else if (event.type === "tool_result") {
        runActivity = `Finished ${typeof event.payload.name === "string" ? event.payload.name : "tool"}`;
      } else if (event.type === "completed") {
        runStatus = "completed";
        runActivity = "";
        chat = { ...chat, pendingHumanInput: undefined };
      } else if (event.type === "cancelled") {
        runStatus = "cancelled";
        runActivity = "";
        chat = { ...chat, pendingHumanInput: undefined };
      } else if (event.type === "error") {
        runStatus = "failed";
        runActivity = "";
        chat = { ...chat, pendingHumanInput: undefined };
        if (typeof event.payload.message === "string") showNotice(event.payload.message);
      }
      return { ...chat, messages, runStatus, runActivity, updatedAt: Date.now() };
    });
    if (event.type === "completed" || event.type === "cancelled" || event.type === "error") {
      void refreshConversation(chatId);
    }
  }

  function watchRun(chatId: string, runId: string, afterSequence: number) {
    if (streamControllers.current.has(runId)) return;
    const controller = new AbortController();
    streamControllers.current.set(runId, controller);
    void streamRunEvents(runId, afterSequence, controller.signal, (event) => applyRunEvent(chatId, event))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        console.error("The run event stream failed.", error);
        showNotice(error instanceof Error ? error.message : "The response stream stopped unexpectedly.");
      })
      .finally(() => {
        if (streamControllers.current.get(runId) === controller) {
          streamControllers.current.delete(runId);
        }
      });
  }

  async function startNewChat() {
    if (!selectedModel) {
      showNotice("No chat model is currently available.");
      return;
    }
    try {
      const created = await api.createConversation(selectedModel.id);
      setChats((existing) => [fromSummary(created), ...existing.filter((chat) => chat.id !== created.id)]);
      setActiveId(created.id);
      setInput("");
      setAttachments([]);
      setAccountView("chat");
      setMobileSidebarOpen(false);
      requestAnimationFrame(() => textareaRef.current?.focus());
    } catch (error) {
      console.error("Unable to create conversation.", error);
      showNotice(error instanceof Error ? error.message : "Couldn't create a new chat.");
    }
  }

  async function openChat(id: string) {
    setActiveId(id);
    setAccountView("chat");
    setMobileSidebarOpen(false);
    setInput("");
    setAttachments([]);
    await refreshConversation(id);
  }

  async function deleteChat(id: string) {
    const chat = chats.find((item) => item.id === id);
    if (!chat || !window.confirm(
      `Delete "${chat.title}"? Any active response will be stopped. This cannot be undone.`,
    )) return;
    setDeletingChatIds((current) => new Set(current).add(id));
    try {
      await api.deleteConversation(id, true);
      if (chat.runId) {
        streamControllers.current.get(chat.runId)?.abort();
        streamControllers.current.delete(chat.runId);
      }
      setChats((existing) => existing.filter((chat) => chat.id !== id));
      if (activeId === id) setActiveId(null);
      showNotice("Conversation deleted.", "success");
    } catch (error) {
      console.error("Unable to delete conversation.", error);
      showNotice(error instanceof Error ? error.message : "Couldn't delete the conversation.");
    } finally {
      setDeletingChatIds((current) => {
        const updated = new Set(current);
        updated.delete(id);
        return updated;
      });
    }
  }

  async function stopGeneration() {
    if (!activeChat?.runId) return;
    try {
      await api.cancelRun(activeChat.runId);
    } catch (error) {
      console.error("Unable to cancel run.", error);
      showNotice(error instanceof Error ? error.message : "Couldn't stop this response.");
    }
  }

  async function respondToHumanInput(chatId: string, submission: HumanInputSubmission) {
    const response = await api.submitHumanInput(submission);
    updateChat(chatId, (chat) => ({
      ...chat,
      pendingHumanInput: undefined,
      runStatus: response.status === "cancelled" ? "cancelled" : "running",
      runActivity: response.status === "cancelled" ? "" : "Working…",
    }));
  }

  async function sendMessage(event?: FormEvent) {
    event?.preventDefault();
    const content = input.trim();
    if (
      (!content && attachments.length === 0)
      || attachments.some((attachment) => attachment.status !== "indexed" || !attachment.attachment)
      || isGenerating
      || isSending
      || !selectedModel
    ) return;
    setIsSending(true);
    try {
      let chatId = activeId;
      if (!chatId) {
        const created = await api.createConversation(selectedModel.id);
        chatId = created.id;
        setActiveId(chatId);
        setChats((existing) => [fromSummary(created), ...existing.filter((chat) => chat.id !== created.id)]);
      }
      const indexedAttachments = attachments.flatMap((attachment) =>
        attachment.attachment ? [attachment.attachment] : [],
      );
      const run = await api.createRun(
        chatId,
        selectedModel.id,
        content,
        indexedAttachments.map((attachment) => attachment.id),
      );
      const userMessage: ChatMessage = {
        id: run.userMessageId,
        role: "user",
        content: content || "",
        attachments: indexedAttachments.map((attachment) => attachment.filename),
      };
      updateChat(chatId, (chat) => ({
        ...chat,
        title: chat.messages.length === 0 && content ? content.slice(0, 40) : chat.title,
        updatedAt: Date.now(),
        messages: [...chat.messages, userMessage],
        runId: run.runId,
        runStatus: "queued",
        runActivity: "Waiting to start",
      }));
      setInput("");
      setAttachments([]);
      if (textareaRef.current) textareaRef.current.style.height = "auto";
      watchRun(chatId, run.runId, -1);
    } catch (error) {
      console.error("Unable to send message.", error);
      showNotice(error instanceof Error ? error.message : "Couldn't send your message. Your draft is still here.");
    } finally {
      setIsSending(false);
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void sendMessage();
    }
  }

  function handleFiles(event: ChangeEvent<HTMLInputElement>) {
    const selected = Array.from(event.target.files ?? []);
    if (selected.length) {
      const pending = selected.map((file) => ({
        key: crypto.randomUUID(),
        file,
        status: "uploading" as const,
      }));
      setAttachments((current) => [...current, ...pending]);
      void (async () => {
        try {
          let conversationId = activeId;
          if (!conversationId) {
            if (!selectedModel) throw new Error("No chat model is currently available.");
            const created = await api.createConversation(selectedModel.id);
            conversationId = created.id;
            setChats((existing) => [fromSummary(created), ...existing.filter((chat) => chat.id !== created.id)]);
            setActiveId(created.id);
          }
          const targetConversationId = conversationId;
          setAttachments((current) =>
            current.map((item) =>
              pending.some((entry) => entry.key === item.key)
                ? { ...item, conversationId: targetConversationId }
                : item,
            ),
          );
          await Promise.all(pending.map(async ({ key, file }) => {
            try {
              const attachment = await api.uploadAttachment(targetConversationId, file);
              if (attachment.indexingStatus !== "indexed") {
                throw new Error("The document was uploaded but could not be indexed.");
              }
              setAttachments((current) => current.map((item) =>
                item.key === key ? { ...item, status: "indexed", attachment, error: undefined } : item,
              ));
            } catch (error) {
              const message = error instanceof Error ? error.message : "The document could not be indexed.";
              setAttachments((current) => current.map((item) =>
                item.key === key ? { ...item, status: "failed", error: message } : item,
              ));
              showNotice(message);
            }
          }));
        } catch (error) {
          const message = error instanceof Error ? error.message : "Couldn't prepare document upload.";
          setAttachments((current) => current.map((item) =>
            pending.some((entry) => entry.key === item.key)
              ? { ...item, status: "failed", error: message }
              : item,
          ));
          showNotice(message);
        }
      })();
    }
    event.target.value = "";
  }

  async function retryAttachment(key: string) {
    const pending = attachments.find((item) => item.key === key);
    if (!pending || pending.status !== "failed") return;
    if (!selectedModel) {
      showNotice("No chat model is currently available.");
      return;
    }
    setAttachments((current) => current.map((item) =>
      item.key === key ? { ...item, status: "uploading", error: undefined } : item,
    ));
    try {
      let conversationId = pending.conversationId ?? activeId;
      if (!conversationId) {
        const created = await api.createConversation(selectedModel.id);
        conversationId = created.id;
        setChats((existing) => [fromSummary(created), ...existing.filter((chat) => chat.id !== created.id)]);
        setActiveId(created.id);
      }
      const attachment = await api.uploadAttachment(conversationId, pending.file);
      if (attachment.indexingStatus !== "indexed") {
        throw new Error("The document was uploaded but could not be indexed.");
      }
      setAttachments((current) => current.map((item) =>
        item.key === key
          ? { ...item, conversationId, status: "indexed", attachment, error: undefined }
          : item,
      ));
    } catch (error) {
      const message = error instanceof Error ? error.message : "The document could not be indexed.";
      setAttachments((current) => current.map((item) =>
        item.key === key ? { ...item, status: "failed", error: message } : item,
      ));
      showNotice(message);
    }
  }

  async function copyMessage(message: ChatMessage) {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopiedId(message.id);
      window.setTimeout(() => setCopiedId(null), 1600);
    } catch (error) {
      console.error("Unable to copy message.", error);
      showNotice("Couldn't copy that message.");
    }
  }

  function formatMessage(content: string) {
    return content.split(/(\*\*[^*]+\*\*|\n)/g).map((part, index) => {
      if (part === "\n") return <br key={index} />;
      if (part.startsWith("**") && part.endsWith("**")) return <strong key={index}>{part.slice(2, -2)}</strong>;
      return part;
    });
  }

  const visibleChats = chats
    .filter((chat) => chat.title.toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => b.updatedAt - a.updatedAt);

  if (authLoading) {
    return <main className="login-page"><p className="auth-loading">Checking your sign-in…</p></main>;
  }

  if (!user) {
    return (
      <main className="login-page">
        <div className="login-glow login-glow-one" />
        <div className="login-glow login-glow-two" />
        <header className="login-brand"><div className="brand-mark"><Sparkles size={19} /></div><span>my gpt</span></header>
        <section className="login-card">
          <div className="login-orb"><Sparkles size={26} strokeWidth={1.6} /></div>
          <p className="eyebrow">A LITTLE SPACE TO THINK</p>
          <h1>Good ideas<br />start with a question.</h1>
          <p className="login-copy">Meet your thoughtful AI companion. Pick up where you left off, or see where a new conversation takes you.</p>
          <button className="google-button" onClick={signIn}><GoogleMark /><span>Continue with Google</span><ArrowRight size={17} className="google-arrow" /></button>
          {authError && <p className="auth-error" role="alert">{authError}</p>}
          <p className="login-disclaimer">Sign in to save your conversations and make this space your own.</p>
          <p className="demo-note"><span /> Secure sign-in with Google</p>
        </section>
        <footer className="login-footer"><span>Made for curious minds</span><span>Private by design · Your chats are tied to your account</span></footer>
      </main>
    );
  }

  return (
    <main className={`app-shell ${sidebarOpen ? "" : "sidebar-collapsed"}`}>
      {mobileSidebarOpen && <button aria-label="Close navigation" className="mobile-scrim" onClick={() => setMobileSidebarOpen(false)} />}
      <aside className={`sidebar ${sidebarOpen ? "" : "sidebar-hidden"} ${mobileSidebarOpen ? "mobile-open" : ""}`}>
        <div className="sidebar-top">
          <button className="wordmark" onClick={() => void startNewChat()} aria-label="My GPT home"><div className="brand-mark"><Sparkles size={16} /></div><span>my gpt</span></button>
          <button className="icon-button sidebar-toggle" aria-label="Collapse sidebar" title="Collapse sidebar" onClick={() => setSidebarOpen(false)}><PanelLeftClose size={18} /></button>
        </div>
        <div className="sidebar-actions">
          <button className="new-chat-button" onClick={() => void startNewChat()}><SquarePen size={17} /><span>New chat</span><span className="shortcut">⌘ K</span></button>
          <button className={`sidebar-action ${searchOpen ? "selected" : ""}`} onClick={() => { setSearchOpen((open) => !open); setSearch(""); }}><Search size={17} /><span>Search chats</span></button>
          {searchOpen && <div className="search-input-wrap"><Search size={15} /><input autoFocus placeholder="Search conversations" value={search} onChange={(event) => setSearch(event.target.value)} /></div>}
        </div>
        <div className="history-label"><span>Your conversations</span><span>{historyLoading ? "Loading…" : ""}</span></div>
        <nav className="chat-list" aria-label="Chat history">
          {visibleChats.length === 0 ? (
            <p className="empty-history">{search ? "No matching conversations" : historyLoading ? "Loading your conversations…" : "Your conversations will show up here."}</p>
          ) : visibleChats.map((chat) => (
            <div key={chat.id} className={`chat-history-item ${activeId === chat.id ? "active" : ""}`}>
              <button className="chat-history-open" onClick={() => void openChat(chat.id)}><MessageSquare size={15} /><span>{chat.title}</span></button>
              <button className="chat-delete-button" aria-label={`Delete ${chat.title}`} title="Delete conversation" disabled={deletingChatIds.has(chat.id)} onClick={() => void deleteChat(chat.id)}><Trash2 size={14} /></button>
            </div>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="upgrade-card"><div className="upgrade-icon"><Sparkles size={15} /></div><div><strong>Your private chat history</strong><span>Saved to your account</span></div><ArrowRight size={14} /></div>
          <button className={`profile-trigger ${accountView === "profile" ? "selected" : ""}`} onClick={() => { setAccountView("profile"); setMobileSidebarOpen(false); }}>
            <div className="avatar">{user.name.trim().charAt(0).toUpperCase()}</div><div className="profile-info"><strong>{user.name}</strong><span>{user.email}</span></div><Settings2 size={16} className="profile-chevron" />
          </button>
        </div>
      </aside>

      <section className="main-panel">
        <header className="topbar">
          <div className="topbar-left">
            {!sidebarOpen && <button className="icon-button reopen-sidebar" aria-label="Open sidebar" title="Open sidebar" onClick={() => setSidebarOpen(true)}><PanelLeftOpen size={18} /></button>}
            <button className="mobile-menu icon-button" aria-label="Open navigation" onClick={() => setMobileSidebarOpen(true)}><Menu size={19} /></button>
            <div className="model-select-wrap">
              <button className="model-select" onClick={() => setModelMenuOpen((open) => !open)} aria-expanded={modelMenuOpen} disabled={!models.length}>
                <span>{selectedModel?.name ?? "Loading models…"}</span><ChevronDown size={15} />
              </button>
              {modelMenuOpen && <><button className="click-away" aria-label="Close model menu" onClick={() => setModelMenuOpen(false)} /><div className="model-menu">{models.map((model) => <button key={model.id} className={`model-option ${selectedModel?.id === model.id ? "chosen" : ""}`} onClick={() => { setSelectedModel(model); setModelMenuOpen(false); }}><span className="model-option-icon">{model.id.includes("mini") ? <Zap size={16} /> : <Sparkles size={16} />}</span><span className="model-option-text"><strong>{model.name}</strong><span>{model.description}</span></span>{selectedModel?.id === model.id && <Check size={16} />}</button>)}</div></>}
            </div>
          </div>
          <div className="topbar-right"><span className="demo-badge"><span /> Connected</span></div>
        </header>

        {accountView === "profile" ? (
          <div className="profile-page">
            <button className="back-link" onClick={() => setAccountView("chat")}><ArrowLeft size={16} /> Back to chat</button>
            <div className="profile-heading"><div className="profile-avatar-large">{user.name.trim().charAt(0).toUpperCase()}</div><div><p className="eyebrow">YOUR ACCOUNT</p><h1>Profile</h1><p>Manage your personal details and preferences.</p></div></div>
            <div className="profile-card"><div className="profile-card-title"><h2>Personal details</h2></div><div className="profile-row"><span>Name</span><strong>{user.name}</strong></div><div className="profile-row"><span>Email</span><strong>{user.email}</strong></div></div>
            <div className="profile-card"><div className="profile-card-title"><h2>Preferences</h2><Settings2 size={17} /></div><div className="profile-row"><span>Default model</span><strong>{selectedModel?.name ?? user.defaultModelId ?? "Not set"}</strong></div><div className="profile-row"><span>Plan</span><strong>Free</strong></div></div>
            <button className="signout-button" onClick={() => void signOut()}><LogOut size={16} /> Sign out</button>
          </div>
        ) : (
          <>
            <div className={`conversation ${isEmptyChat ? "conversation-empty" : ""}`} key={activeId ?? "new"}>
              {isEmptyChat ? (
                <div className="welcome-content">
                  <div className="welcome-spark"><Sparkles size={19} /></div>
                  <p className="eyebrow welcome-eyebrow">A FRESH PERSPECTIVE</p>
                  <h1>What’s on your mind<br /><span>today?</span></h1>
                  <p className="welcome-subtitle">A good question can take you anywhere.</p>
                  <div className="suggestion-grid">{suggestions.map((suggestion) => <button key={suggestion.title} className="suggestion-card" onClick={() => { setInput(suggestion.prompt); textareaRef.current?.focus(); }}><span className="suggestion-symbol">{suggestion.icon}</span><span>{suggestion.title}</span><ArrowRight size={14} /></button>)}</div>
                </div>
              ) : (
                <div className="messages">
                  {activeChat?.messages.map((message) => (
                    <article className={`message message-${message.role}`} key={message.id}>
                      {message.role === "assistant" && <div className="assistant-mark"><Sparkles size={15} /></div>}
                      <div className="message-body">
                        {message.attachments?.map((name) => <div className="attachment-chip attached" key={name}><FileText size={14} /><span>{name}</span></div>)}
                        {message.content && <div className="message-content">{formatMessage(message.content)}{message.id.startsWith("stream-") && isGenerating && <span className="stream-cursor" />}</div>}
                        {message.incomplete && <div className="thinking-row"><span className="thinking-dot" /><span>Response {activeChat?.runStatus === "cancelled" ? "stopped" : "interrupted"}</span></div>}
                        {message.role === "assistant" && !message.id.startsWith("stream-") && <div className="message-tools"><button aria-label="Copy response" title="Copy response" onClick={() => void copyMessage(message)}>{copiedId === message.id ? <Check size={15} /> : <Clipboard size={15} />}</button></div>}
                      </div>
                    </article>
                  ))}
                  {activeChat?.pendingHumanInput && activeChat.runId && (
                    <article className="message message-assistant">
                      <div className="assistant-mark"><Sparkles size={15} /></div>
                      <div className="message-body">
                        <HumanInputCard
                          request={activeChat.pendingHumanInput}
                          disabled={activeChat.runStatus !== "waiting_for_input"}
                          onSubmit={(submission) => respondToHumanInput(activeChat.id, submission)}
                        />
                      </div>
                    </article>
                  )}
                  {isGenerating && activeChat?.runStatus !== "waiting_for_input" && <article className="message message-assistant"><div className="assistant-mark"><Sparkles size={15} /></div><div className="message-body"><div className="thinking-row"><span className="thinking-dot" /><span>{activeChat?.runActivity || "Working…"}</span></div></div></article>}
                  {(activeChat?.runStatus === "failed" || activeChat?.runStatus === "cancelled") && <p className="run-result-status" role="status">{activeChat.runStatus === "failed" ? "The response did not complete. You can try again." : "The response was stopped."}</p>}
                  <div className="messages-bottom" />
                </div>
              )}
            </div>
            <div className="composer-area">
              <form className="composer" onSubmit={(event) => void sendMessage(event)}>
                {attachments.length > 0 && (
                  <div className="attachment-list">
                    {attachments.map((item) => (
                      <div
                        className={`attachment-chip attachment-${item.status}`}
                        key={item.key}
                        title={item.error}
                      >
                        <FileText size={14} />
                        <span>{item.file.name}</span>
                        <span className="attachment-state" role={item.status === "failed" ? "alert" : "status"}>
                          {item.status === "uploading" ? "Indexing…" : item.status === "indexed" ? "Ready" : "Index failed"}
                        </span>
                        {item.status === "failed" && (
                          <button type="button" aria-label={`Retry indexing ${item.file.name}`} onClick={() => void retryAttachment(item.key)}>
                            Retry
                          </button>
                        )}
                        <button
                          type="button"
                          aria-label={`Remove ${item.file.name}`}
                          onClick={() => setAttachments((current) => current.filter((attachment) => attachment.key !== item.key))}
                        >
                          <X size={13} />
                        </button>
                      </div>
                    ))}
                  </div>
                )}
                <textarea ref={textareaRef} value={input} onChange={(event) => { setInput(event.target.value); event.target.style.height = "auto"; event.target.style.height = `${Math.min(event.target.scrollHeight, 180)}px`; }} onKeyDown={handleKeyDown} placeholder="Ask anything..." rows={1} aria-label="Message" disabled={isSending || isGenerating} />
                <div className="composer-controls">
                  <div className="composer-tools">
                    <input ref={fileInputRef} type="file" multiple accept=".pdf,.docx,.txt,.md,.py,.csv" className="visually-hidden" onChange={handleFiles} />
                    <button type="button" className="composer-tool attach-button" title="Attach files" aria-label="Attach files" onClick={() => fileInputRef.current?.click()} disabled={isSending || isGenerating}><Plus size={19} /><span className="attach-label">Attach</span></button>
                    <span className="composer-divider" />
                    <span className="composer-tool web-tool" title="Tools run on the server"><span>Server tools enabled</span></span>
                  </div>
                  <div className="composer-submit-group"><span className="composer-hint">Shift + Enter for a new line</span>{isGenerating ? <button type="button" className="send-button stop-button" aria-label="Stop generating" title="Stop generating" onClick={() => void stopGeneration()}><StopCircle size={17} /></button> : <button type="submit" className="send-button" aria-label="Send message" disabled={isSending || attachments.some((attachment) => attachment.status !== "indexed") || !selectedModel || (!input.trim() && attachments.length === 0)}>{isSending ? <span className="sending-spinner" /> : <ArrowUp size={19} />}</button>}</div>
                </div>
              </form>
              <p className="composer-disclaimer">My GPT can make mistakes. Check important information.</p>
            </div>
          </>
        )}
      </section>
      {notice && (
        <div role={notice.kind === "error" ? "alert" : "status"} className={`toast toast-${notice.kind}`}>
          {notice.kind === "error" ? <CircleAlert size={16} /> : <CheckCircle2 size={16} />}
          {notice.message}
        </div>
      )}
    </main>
  );
}

function HumanInputCard({
  request,
  disabled,
  onSubmit,
}: {
  request: HumanInputRequest;
  disabled: boolean;
  onSubmit: (submission: HumanInputSubmission) => Promise<void>;
}) {
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  function updateValue(fieldId: string, value: unknown) {
    setValues((current) => ({ ...current, [fieldId]: value }));
    setErrors((current) => ({ ...current, [fieldId]: "" }));
    setFormError("");
  }

  async function submit(actionId: string) {
    if (disabled || submitting) return;
    const nextValues: Record<string, unknown> = {};
    const nextErrors: Record<string, string> = {};
    const action = request.actions.find((item) => item.id === actionId);
    const cancels = action !== undefined
      && (
        ["cancel", "stop", "abort"].includes(action.id.trim().toLowerCase())
        || /^(cancel|stop)\b/i.test(action.label.trim())
      );
    for (const field of cancels ? [] : request.fields) {
      const kind = field.kind.trim().toLowerCase().replaceAll("-", "_");
      const raw = values[field.id];
      const empty = raw === undefined
        || raw === null
        || raw === ""
        || (Array.isArray(raw) && raw.length === 0);
      if (empty) {
        if (field.required) nextErrors[field.id] = `${field.label} is required.`;
        continue;
      }

      if (kind === "number") {
        const number = typeof raw === "number" ? raw : Number(raw);
        if (!Number.isFinite(number)) nextErrors[field.id] = `${field.label} must be a number.`;
        else nextValues[field.id] = number;
      } else if (kind === "json" || (kind === "multi_select" || kind === "multiselect") && !(field.options?.length)
        || ![
        "text", "textarea", "date", "time", "datetime", "datetime_local",
        "boolean", "single_choice", "select", "multi_select", "multiselect",
      ].includes(kind)) {
        if (typeof raw !== "string") nextValues[field.id] = raw;
        else {
          try {
            nextValues[field.id] = JSON.parse(raw) as unknown;
          } catch {
            nextErrors[field.id] = `${field.label} must be valid JSON.`;
          }
        }
      } else {
        if (field.required && typeof raw === "string" && !raw.trim()) {
          nextErrors[field.id] = `${field.label} is required.`;
        } else {
          nextValues[field.id] = raw;
        }
      }
    }
    if (Object.keys(nextErrors).length) {
      setErrors(nextErrors);
      return;
    }

    setSubmitting(true);
    setErrors({});
    setFormError("");
    try {
      await onSubmit({
        requestId: request.requestId,
        runId: request.runId,
        actionId,
        values: nextValues,
      });
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "Your response could not be submitted.");
    } finally {
      setSubmitting(false);
    }
  }

  function renderField(field: HumanInputField) {
    const id = `human-${request.requestId}-${field.id}`;
    const kind = field.kind.trim().toLowerCase().replaceAll("-", "_");
    const constraints = field.constraints ?? {};
    const options = field.options ?? [];
    const common = {
      id,
      disabled: disabled || submitting,
      required: field.required,
      placeholder: field.placeholder,
      "aria-describedby": errors[field.id] ? `${id}-error` : undefined,
      "aria-invalid": errors[field.id] ? true : undefined,
    };
    const textValue = typeof values[field.id] === "string" ? values[field.id] as string : "";

    if (kind === "boolean") {
      return (
        <label className="human-boolean" htmlFor={id}>
          <input
            {...common}
            id={id}
            type="checkbox"
            checked={typeof values[field.id] === "boolean" ? values[field.id] as boolean : false}
            onChange={(event) => updateValue(field.id, event.target.checked)}
          />
          <span>{field.placeholder || "Yes"}</span>
        </label>
      );
    }
    if (kind === "single_choice" && options.length > 0) {
      return (
        <div id={id} className="human-choice-list" role="radiogroup" aria-labelledby={`${id}-label`}>
          {options.map((option) => (
            <label
              className={`human-choice ${values[field.id] === option.value ? "selected" : ""}`}
              key={option.value}
            >
              <input
                type="radio"
                name={id}
                value={option.value}
                checked={values[field.id] === option.value}
                disabled={disabled || submitting}
                onChange={() => updateValue(field.id, option.value)}
              />
              <span>{option.label}</span>
            </label>
          ))}
        </div>
      );
    }
    if ((kind === "multi_select" || kind === "multiselect") && options.length > 0) {
      const selected = Array.isArray(values[field.id]) ? values[field.id] as string[] : [];
      return (
        <div id={id} className="human-choice-list" role="group" aria-labelledby={`${id}-label`}>
          {options.map((option) => (
            <label
              className={`human-choice ${selected.includes(option.value) ? "selected" : ""}`}
              key={option.value}
            >
              <input
                type="checkbox"
                value={option.value}
                checked={selected.includes(option.value)}
                disabled={disabled || submitting}
                onChange={(event) => updateValue(
                  field.id,
                  event.target.checked
                    ? [...selected, option.value]
                    : selected.filter((value) => value !== option.value),
                )}
              />
              <span>{option.label}</span>
            </label>
          ))}
        </div>
      );
    }
    if (kind === "select" && options.length > 0) {
      return (
        <select
          {...common}
          value={textValue}
          onChange={(event) => updateValue(field.id, event.target.value)}
        >
          <option value="">Choose an option</option>
          {options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
      );
    }
    if (kind === "textarea") {
      return (
        <textarea
          {...common}
          value={textValue}
          onChange={(event) => updateValue(field.id, event.target.value)}
        />
      );
    }
    if (kind === "number") {
      const minimum = constraints.minimum ?? constraints.min;
      const maximum = constraints.maximum ?? constraints.max;
      return (
        <input
          {...common}
          type="number"
          min={typeof minimum === "number" ? minimum : undefined}
          max={typeof maximum === "number" ? maximum : undefined}
          value={textValue}
          onChange={(event) => updateValue(field.id, event.target.value)}
        />
      );
    }
    if (["date", "time", "datetime", "datetime_local"].includes(kind)) {
      const inputType = kind === "datetime" || kind === "datetime_local" ? "datetime-local" : kind;
      return (
        <input
          {...common}
          type={inputType}
          value={textValue}
          onChange={(event) => updateValue(field.id, event.target.value)}
        />
      );
    }
    if ((kind === "single_choice" || kind === "select") && options.length === 0) {
      return (
        <input
          {...common}
          type="text"
          value={textValue}
          onChange={(event) => updateValue(field.id, event.target.value)}
        />
      );
    }
    return (
      <textarea
        {...common}
        className="human-json-input"
        value={textValue}
        placeholder={field.placeholder || "Enter a JSON value"}
        onChange={(event) => updateValue(field.id, event.target.value)}
      />
    );
  }

  return (
    <section
      className="human-input-card"
      aria-labelledby={`human-title-${request.requestId}`}
      aria-live="polite"
      role="region"
    >
      <div className="human-input-header">
        <div className="human-input-icon"><CircleAlert size={15} /></div>
        <div>
          <div className="human-input-kicker">YOUR INPUT IS NEEDED</div>
          <h3 id={`human-title-${request.requestId}`}>{request.title}</h3>
        </div>
      </div>
      {request.description && <p className="human-input-description">{request.description}</p>}
      <form
        className="human-input-form"
        onSubmit={(event) => {
          event.preventDefault();
          const firstAction = request.actions[0];
          if (firstAction) void submit(firstAction.id);
        }}
      >
        {request.fields.map((field) => {
          const id = `human-${request.requestId}-${field.id}`;
          return (
            <div className="human-field" key={field.id}>
              <label className="human-field-label" id={`${id}-label`} htmlFor={id}>
                {field.label}{field.required && <span className="required-mark"> *</span>}
              </label>
              {field.description && <p className="human-field-description">{field.description}</p>}
              {renderField(field)}
              {errors[field.id] && <span className="human-field-error" id={`${id}-error`} role="alert">{errors[field.id]}</span>}
            </div>
          );
        })}
        {formError && <p className="human-field-error" role="alert">{formError}</p>}
        <div className="human-input-actions">
          {request.actions.map((action, index) => (
            <button
              className={`human-submit ${index === 0 ? "primary" : ""}`}
              key={action.id}
              type="button"
              disabled={disabled || submitting}
              onClick={() => void submit(action.id)}
            >
              {submitting && index === 0 ? "Submitting…" : action.label}
            </button>
          ))}
        </div>
      </form>
    </section>
  );
}

function GoogleMark() {
  return <svg aria-hidden="true" viewBox="0 0 48 48" className="google-mark"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" /><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.52-4.17 7.09-10.31 7.09-17.65z" /><path fill="#FBBC05" d="M10.53 28.59A14.4 14.4 0 0 1 9.75 24c0-1.59.27-3.13.76-4.59l-7.98-6.19A23.9 23.9 0 0 0 0 24c0 3.87.93 7.52 2.56 10.78l7.97-6.19z" /><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.8l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.46-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" /></svg>;
}

export default App;
