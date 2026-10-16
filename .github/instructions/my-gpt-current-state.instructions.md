---
name: My GPT Current State
description: "Use when assessing My GPT's current backend, frontend, RAG/document retrieval, database, authentication, model, streaming, attachment, or HITL implementation."
---

# My GPT current implementation state

This is a snapshot, not a substitute for inspecting the current source. Verify files before relying on details, and update this instruction when implementation status materially changes.

## Backend

- `src/app/backend/app.py` implements Google OIDC login, callback, current-user lookup, logout, authenticated owner-scoped conversation/model/upload/run APIs, SSE replay, run cancellation, and confirmed active-run-aware conversation deletion. The verified Google subject/profile is persisted from the server session; request bodies do not establish identity. The FastAPI lifespan initializes the persistent LangGraph checkpoint runtime.
- `src/app/backend/db.py` defines SQLAlchemy models for users/preferences, conversations, chat messages, runs, ordered run events, attachment metadata and indexing state, generic human-input requests, and long-term memory. Its additive compatibility migration preserves legacy rows; unowned conversations are not automatically assigned and are excluded from signed-in APIs.
- `src/app/backend/schemas.py` defines the Pydantic wire contracts. The current model catalog is server-owned in `model_catalog.py` and offers only `gpt-4o-mini`; unsupported model IDs are rejected.
- `src/app/backend/agents.py` builds the LangGraph agent with SQLite checkpointing and streams the selected model server-side. `tools.py` scopes memory tools with authenticated graph config rather than model-supplied user IDs. Tool events sent to clients expose names only, not raw arguments/results.
- Chat runs persist their user message, ordered run events, final assistant message, and completion/failure/cancellation state. `GET /api/runs/{runId}/events?afterSequence=N` replays persisted SSE events after client reconnects. Active runs are capped at three per user.
- `src/app/backend/rag.py` supports PDF, DOCX, TXT, Markdown, Python, and CSV extraction and conversation-scoped Chroma indexing/retrieval/deletion. `POST /api/conversations/{id}/attachments` validates files up to 10 MB, stores them privately, indexes them before returning the attachment ID/status, and attempts to remove failed partial uploads. If cleanup also fails, it leaves the attachment unavailable and associated with the conversation so deletion can retry cleanup. Indexing requires `OPENAI_API_KEY`.
- `DELETE /api/conversations/{id}?stopActiveRun=true` stops active runs before deleting messages, run/event records, private uploads, long-term memory, and conversation-scoped Chroma chunks. Failed cancellation or cleanup is reported to the client; the UI confirms deletion and displays success/errors.
- `src/app/backend/tests/test_api.py` covers owner isolation, legacy unowned history, model validation, conversation-bound attachment indexing and isolation, indexing failure cleanup, active-run deletion, sequence replay, and mocked agent event/message persistence. `test_rag.py` covers extraction, conversation-scoped indexing, and chunk cleanup; `test_agents.py` covers agent configuration. Run tests from `src/app/backend` using `python -m unittest discover -s tests -v`. These tests do not verify live provider calls with real credentials.
- Generic HITL request persistence and run resumption remain unconnected to the API/UI. Agent interrupts are reported as failures; do not imply paused runs can be answered or resumed in the app.

## Frontend

- `src/app/frontend/src/App.tsx` uses server-backed conversation history, server model options, uploaded/indexed attachment IDs, persisted streamed events, run cancellation, confirmation before deletion, retryable upload/error states, and account controls. Failed chat requests preserve the draft; local demo responses and localStorage-backed history have been removed.
- `src/app/frontend/src/api.ts` is the credentialed HTTP/SSE adapter. It reconnects using the last received sequence, sends multipart uploads to the selected conversation, and keeps stream parsing and server validation-error formatting out of presentation code.
- `src/app/frontend/src/models.ts` contains frontend API/domain types aligned with the Pydantic wire aliases and shapes.
- The frontend displays backend-originated status and tool activity only. File uploads and server-side chat execution require a running backend; document indexing additionally requires an OpenAI embeddings key. Upload failures are not presented as ready attachments.

## Remaining limitations

- Live provider-backed run/cancellation behavior needs a smoke test with valid server credentials; the focused execution test uses a fake LangGraph event source.
- Agent execution runs in memory; an API process restart does not resume an active agent task, even though already-persisted events remain available.
- Generic HITL submission/resumption is not implemented. An agent interrupt must not be presented as a normal completed response.
- Indexed attachments that are uploaded but never referenced by a run remain scoped to their conversation until it is deleted; there is no per-attachment management UI yet.
