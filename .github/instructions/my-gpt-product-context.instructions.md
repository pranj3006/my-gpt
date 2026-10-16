---
name: My GPT Product Context
description: "Use when planning My GPT features, APIs, conversation flows, model selection, uploads, streamed execution, generic human input, or product acceptance."
---

# My GPT product context and development status

## Source of truth and goal

The project [`README.md`](../../README.md) is authoritative for detailed product requirements, user flows, setup, and the acceptance checklist. Read relevant sections before implementing a feature and keep this concise context aligned with it.

My GPT is a chat interface for authenticated users to start and resume private conversations, select an available model, send messages and attachments, follow real model/tool progress, and answer generic human-input requests. The frontend should be independent of model provider and orchestration framework. The backend owns authentication, authorization, persistence, execution, streaming, and upload.

## Product scope

### In scope

- Google sign-in, authenticated profile, and server-side sign-out.
- Durable conversation history scoped to the authenticated account.
- New, list, read/resume, continue, and delete conversation flows.
- Server-provided model catalog and stable model IDs.
- Multiline composer, attachments, visible busy/error/retry states, and preservation of drafts on failures.
- Ordered assistant text deltas and meaningful status, tool call/result, completion, cancellation, and error events.
- Generic HITL request rendering and typed response submission correlated by stable request/run IDs.
- Responsive desktop/mobile behavior and keyboard/accessibility support.

### Out of scope

Voice; project management; frontend-owned AI orchestration or provider credentials; simulated production tool execution; and frontend choice of when a model requires human input.

## Required interaction contracts

- Chat/run requests identify the conversation and model, include message content and uploaded attachment references, and are authorized using the server session.
- Stream events identify conversation and run, preserve per-run sequence ordering, and use message/correlation IDs where needed. Human-input request events are persisted before delivery. Tool events must be backend-originated and filtered to avoid leaking secrets/internal data.
- A generic HITL request includes `requestId`, `runId`, title/description, fields, and available actions. Fields may include kind, required, description, placeholder, constraints, and options. Support common kinds (text, textarea, number, date/time, boolean, single-choice, multi-select/select, JSON); unknown kinds must have a safe JSON fallback or explicit unsupported message, never silently invalid submission.
- HITL response includes `requestId`, `runId`, `actionId`, and typed `values`. The server must authorize ownership, ensure the request is still pending, validate allowed action/values, then resume or cancel the corresponding run and return visible errors.
- Legacy string interrupts are normalized to a generic response field; structured interrupt payloads use their supplied field/action contract. Pending requests and partial assistant text are persisted, and accepted responses resume the matching checkpoint. The frontend renders only backend-originated request events; it must not provide a local human-input demo as if it were agent execution.
- File names in the browser are not uploaded files. Each file is uploaded to an owned conversation through the backend, validated, stored privately, and indexed before the UI marks it ready. Runs send attachment IDs, not filenames, and the backend rejects IDs not indexed for that conversation.
- Conversation deletion requires confirmation in the UI, stops active runs when possible, and removes the owned conversation's messages, run/event records, uploaded files, memory, and RAG chunks. A failed stop or cleanup must return a visible retryable error.
- Browser storage may be a clearly identified cache only; server persistence is authoritative.

## Implemented behavior and limitations

- The backend persists the verified Google subject/profile and derives ownership from the server session. Browser-supplied user IDs are not proof of identity. Owner-scoped APIs cover model listing, conversations, uploads, run creation, SSE events, and cancellation.
- Existing unowned conversations are preserved but excluded from authenticated history; they are never automatically assigned to an account.
- The backend catalog currently offers only `gpt-4o-mini`. Frontend choices are fetched from the catalog and unsupported IDs are rejected.
- Runs persist the user message and each ordered event before SSE delivery. Clients can replay from a sequence number. Final assistant messages are persisted; partial output is marked incomplete after failure or cancellation. Agent execution itself is in process, so an API process restart does not resume an active task.
- Uploads accept PDF, DOCX, TXT, Markdown, Python, and CSV files up to 10 MB at `POST /api/conversations/{id}/attachments`. The API validates file content, binds metadata and private storage to the authenticated owner/conversation, indexes immediately, and returns an attachment ID plus `indexingStatus`; indexing requires `OPENAI_API_KEY`. Failed indexing returns an error and the UI exposes a retry state rather than marking the file ready. If cleanup also fails, the item remains unavailable and associated with the conversation for deletion cleanup.
- `DELETE /api/conversations/{id}?stopActiveRun=true` stops active in-process runs before deleting conversation records, private uploads, long-term memory, and conversation-scoped RAG chunks. A run that cannot be stopped or cleanup that fails returns a visible error instead of claiming success.
- Focused backend tests cover API ownership/event behavior, agent configuration, and RAG helpers. Mocked agent streams do not establish live provider-backed behavior.
- The generic HITL API/UI persists pending requests, validates owner/action/typed values, streams normalized requests, and resumes or cancels the matching run.

## Remaining development

1. Smoke-test sign-in, live provider-backed runs, cancellation, uploads, and reconnect behavior with valid deployment credentials.
2. Add an intentional migration/admin flow if legacy unowned conversations should become available to users. Never infer ownership.
3. Continue verifying responsive layout, keyboard/focus behavior, reduced motion, and failure/draft preservation against the README acceptance checklist.

Treat this order as a useful dependency sequence, not a mandate to bundle unrelated features. Reassess prerequisites and scope for each requested change.
