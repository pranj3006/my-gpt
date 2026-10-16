# My GPT

My GPT is a conversational AI web application. The frontend is a responsive React app in [`src/app/frontend`](./src/app/frontend), built with Vite and TypeScript. The Python API provides Google OpenID Connect sign-in, owner-scoped conversations, model discovery, attachment upload, and persisted/resumable chat runs.

This README distinguishes implemented behavior from planned capabilities. Only backend-persisted events and data count as production behavior.

## Product goal

Provide a polished ChatGPT-style interface where an authenticated user can start conversations, return to prior chats, select a model, send text and attachments, follow model/tool progress, and answer questions when an agent needs human input.

The UI should remain independent of any specific model provider or orchestration framework. The backend owns authentication, authorization, conversation persistence, model/tool execution, streaming, and resuming paused runs. The frontend presents the backend's conversation events and sends user actions back.

## Requirements and scope

### In scope

- **Sign-in and account:** Sign in with Google using the backend's OAuth flow, display the authenticated user's profile, and sign out. Chat and run APIs must be authorized on the server for that user.
- **Conversation history:** Start a new chat, list the signed-in user's previous chats, select a chat to continue it, and keep conversation context across turns. History must be isolated by account; the product experience should use backend persistence rather than treating browser storage as the source of truth.
- **Chat composer:** Send a message with Enter, insert a newline with Shift+Enter, attach files, and select an available model from a dropdown. The UI must provide busy, disabled, error, and retry states where appropriate.
- **Execution feedback:** Stream assistant output into the active conversation and show meaningful status/activity events, such as thinking, a tool call, a tool result, or a completed response. Do not show fabricated tool activity as if it came from the backend.
- **Generic HITL (planned):** Render a backend/model-provided input request, collect the user's answer or selected action, and submit a structured response correlated with the paused request/run. The UI must not depend on which model or agent requested the input. This flow is not implemented yet.
- **Profile:** Show account details and available preferences from the authenticated user/session.
- **Responsive and accessible interaction:** Support desktop and mobile navigation, collapsible chat history, keyboard use, visible focus, understandable validation/errors, and reduced-motion preferences.

### Explicitly out of scope

- Voice input, speech playback, or voice conversations.
- Project creation or project-management features.
- Choosing when a model needs human input; this is determined by the backend/model.
- Frontend-owned AI orchestration, provider credentials, or simulated production tool execution.

## User-facing functionality

1. **Sign-in:** An unauthenticated user sees the Google sign-in entry point. On success, load the current user and their chat history. On cancellation, expired session, or failure, show a useful state and let the user retry.
2. **Start or resume a chat:** The sidebar has a New chat action and selectable chat history. Resuming a chat loads its messages and allows the user to continue that session. Sidebar collapse/expand and mobile navigation must not discard the current draft or conversation.
3. **Send a message:** The composer supports multiline text, attachments, and model selection. The interface shows the submitted user message, prevents accidental duplicate submissions while a request is active, and indicates errors without silently losing the draft.
4. **Follow execution:** Display status events and streamed text in order. Tool calls and results should be distinguishable from assistant prose and should not expose secrets or internal data that is not intended for the user. Allow stopping an active generation when the backend supports cancellation.
5. **Respond to a human-input request (planned):** The generic interaction contract below describes the intended flow. The current API/UI does not accept HITL responses or resume interrupted runs; an agent interrupt is surfaced as a failed run, not a completed answer.
6. **Account controls:** Show profile information and sign out through the backend; signing out must clear the server session and frontend account state.

## Generic HITL interaction contract

When HITL is implemented, the backend should send a serializable input request as part of the conversation/run event stream. The UI should render the request data, rather than hard-coding agent-specific forms.

Suggested request shape:

```json
{
  "requestId": "stable-request-id",
  "runId": "paused-run-id",
  "title": "Choose how to continue",
  "description": "Optional context for the user.",
  "fields": [
    {
      "id": "detail",
      "label": "How much detail?",
      "kind": "single_choice",
      "required": true,
      "options": [
        { "label": "Brief", "value": "brief" },
        { "label": "Detailed", "value": "detailed" }
      ]
    }
  ],
  "actions": [
    { "id": "continue", "label": "Continue" },
    { "id": "cancel", "label": "Cancel" }
  ]
}
```

Fields may include `id`, `label`, `kind`, `required`, `description`, `placeholder`, constraints, and `options`. The UI should support common input kinds such as text, textarea, number, date/time, boolean, single choice, multi-select, select, and JSON. Unknown kinds should have a safe generic fallback (for example, JSON input) or a clear unsupported-field message; they must not silently submit invalid data.

The response sent to the backend should preserve the request/run correlation and typed values:

```json
{
  "requestId": "stable-request-id",
  "runId": "paused-run-id",
  "actionId": "continue",
  "values": {
    "detail": "brief"
  }
}
```

The future backend must validate the request is still pending, validate the submitted action and values, authorize the user against the conversation/run, and resume or cancel the run. The UI should display validation errors from both client-side checks and the backend. Model-specific schemas should be normalized by the backend or an adapter, not embedded in the chat components.

Human-input run resumption is not connected yet. The current chat does not fabricate a HITL prompt or claim to resume a paused run.

## Backend/frontend integration requirements

The backend/API layer needs to provide:

- Authenticated current-user, login, callback, and logout flows.
- Authorized conversation list/create/read/delete operations, scoped to the signed-in user.
- A list of available models and stable model identifiers accepted by the chat API.
- A chat/run operation that accepts a conversation ID, model ID, message content, and attachment references, and returns streamed events or a stream that can be adapted into UI events.
- Events for assistant text deltas, execution status, tool activity/results, HITL requests, completion, cancellation, and errors. Events should include conversation/run/message IDs and sequence/correlation IDs where needed.
- (Planned) An endpoint/action to submit a HITL response and resume or cancel the corresponding paused run.
- Conversation-scoped attachment upload/storage, metadata, size/type limits, indexing status, and access control. The frontend uploads file contents through the approved API and sends returned attachment IDs with chat requests.
- Clear error responses, authorization checks, rate limits, and cancellation semantics.

Keep API calls and stream parsing in a frontend service/adapter layer, not inside presentation components. Normalize backend-specific event formats into stable frontend types. Persist conversation history and run state on the backend; local browser storage may be used as a clearly identified cache, not as authorization or durable storage.

### Implemented chat API

- `GET /api/models` returns the server-owned model catalog. The current catalog contains only `gpt-4o-mini`; unsupported IDs are rejected rather than silently replaced.
- `POST /api/conversations`, `GET /api/conversations`, and `GET /api/conversations/{id}` create, list, and read only the authenticated user's conversations. `DELETE /api/conversations/{id}` deletes only an owned conversation and its messages, run/event records, uploads, memory, and RAG chunks. For an active run, the UI asks before sending `?stopActiveRun=true`; deletion returns a visible `409` if the run cannot be stopped.
- `POST /api/conversations/{id}/attachments` accepts PDF, DOCX, TXT, Markdown, Python, and CSV files up to 10 MB. It validates content, stores the file outside public web paths, associates it with the signed-in owner and conversation, and indexes it immediately. The response contains an attachment ID and `indexingStatus`; failed indexing returns an error and attempts to remove partial upload/index data. If cleanup itself fails, the unavailable upload remains associated with the conversation so conversation deletion can retry cleanup. Indexing requires the configured OpenAI embedding key.
- `POST /api/conversations/{id}/runs` accepts `modelId`, `content`, and uploaded attachment IDs; it persists the user message and returns a run ID with `202 Accepted`.
- `GET /api/runs/{runId}/events?afterSequence=N` streams persisted SSE events. Each event has a monotonically increasing per-run `sequence`; clients can reconnect after the last sequence. `POST /api/runs/{runId}/cancel` requests cancellation. Only the owning account can read or cancel a run.
- Assistant deltas and sanitized tool activity are persisted before delivery. Final assistant messages are stored on completion; partial messages are marked incomplete on cancellation or failure. A user may have up to three active runs at once.

The SQLite compatibility migration preserves existing unowned conversations. They remain excluded from authenticated lists and cannot be read or assigned to a user through the new API; no ownership is inferred from browser storage. Event replay survives client disconnects; an API process restart does not resume an in-memory agent task.

## Current implementation status

- The frontend uses the server as the source of truth for conversation history, model choices, file uploads, run state, and streamed events. Local demo replies and browser-local history are no longer used.
- Google sign-in persists the verified Google subject/profile to SQLite and APIs derive ownership only from the signed server session.
- Conversation and run APIs are owner-scoped. Legacy unowned rows remain preserved and unavailable to signed-in users unless migrated deliberately by a separate, verified process.
- Run execution uses the LangGraph agent server-side. Assistant deltas and allowlisted tool names are persisted in sequence before SSE delivery; event replay, cancellation, and partial-message state are implemented. A user is limited to three active runs.
- Uploaded PDF, DOCX, TXT, Markdown, Python, and CSV attachments are saved with owner-scoped metadata and bounded by a 10 MB upload limit. For RAG execution, OpenAI embeddings require a configured `OPENAI_API_KEY`.
- `src/app/backend/rag.py` stores embeddings in persistent Chroma at `src/app/backend/data/rag/chroma_db`; ingestion and retrieval are scoped by conversation thread ID. The focused RAG tests mock the vector store.
- Generic HITL submission/resumption is not yet connected to the chat UI/API. If the existing agent pauses for input, the run must not be treated as a completed answer; see current-state notes for the remaining limitation.

## Local setup

### Requirements

- Node.js `24.21.x` (the frontend [`.nvmrc`](./src/app/frontend/.nvmrc) pins `24.21.0`).
- npm.
- Python 3.12 recommended, plus dependencies listed in [`src/app/backend/requirements.txt`](./src/app/backend/requirements.txt) to run the API and RAG tests.
- Google OAuth client credentials in the backend environment to test sign-in.

### Configure the backend

1. From the project root, create and activate a virtual environment (or activate your existing project environment):

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

2. Install backend dependencies:

   ```bash
   cd src/app/backend
   pip install -r requirements.txt
   ```

3. Copy [`example.env`](./src/app/backend/example.env) to `.env` in `src/app/backend` and configure the values you need:

   ```bash
   cp example.env .env
   ```

   Set `OPENAI_API_KEY` to enable document embeddings/RAG use. Google sign-in additionally requires `GOOGLE_SSO_CLIENT_ID`, `GOOGLE_SSO_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI=http://localhost:8000/auth/callback`, and a persistent `SESSION_SECRET` of at least 32 characters. Never put server secrets in frontend environment variables or commit them. Tavily, Weatherstack, and Alpha Vantage keys are only needed for their respective agent tools.

4. Add `http://localhost:8000/auth/callback` as an authorized redirect URI for the Google OAuth client if you want to test sign-in.

### Run the application

Start the backend from `src/app/backend`:

```bash
uvicorn app:app --reload --port 8000
```

In a second terminal, start the frontend:

```bash
cd src/app/frontend
npm install
npm run dev
```

Open `http://localhost:5173`. The backend defaults to this frontend URL and permits `http://localhost:5173` and `http://127.0.0.1:5173`; configure `FRONTEND_URL` and `FRONTEND_ORIGINS` when using a different origin. Set `VITE_API_BASE_URL` in `src/app/frontend/.env` if the API is not at `http://localhost:8000`.

The auth endpoints are `/auth/login`, `/auth/callback`, `/auth/me`, and `/auth/logout`. The API uses Google's OIDC authorization-code flow and a short-lived HttpOnly, SameSite=Lax session cookie. For HTTPS deployments, use an HTTPS callback URL, a persistent session secret, and explicitly configure the frontend URL/origins. Chat and run endpoints authorize the session owner server-side.

### Test and verify

Run the backend unit tests from `src/app/backend` with the same Python environment used to install requirements:

```bash
python -m unittest discover -s tests -v
```

The backend suite includes [`tests/test_api.py`](./src/app/backend/tests/test_api.py) for authenticated ownership, legacy unowned history, model validation, attachment upload metadata, event replay, and mocked streamed run persistence; [`tests/test_rag.py`](./src/app/backend/tests/test_rag.py) covers extraction and conversation-scoped indexing with a fake vector store; and [`tests/test_agents.py`](./src/app/backend/tests/test_agents.py) covers agent configuration. These tests do not prove live provider execution with valid Google/OpenAI credentials.

Run frontend checks from `src/app/frontend`:

```bash
npm run typecheck
npm run build
```

For a local smoke test, keep both dev servers running and open `http://localhost:5173`. With Google OAuth configured, verify sign-in, server-backed history, message submission, streaming, and cancellation. Without credentials, the authenticated APIs should reject unauthenticated access; for example, `GET http://localhost:8000/auth/me` should return HTTP 401. The OpenAPI page is available at `http://localhost:8000/docs`.

The RAG tests use a fake vector store and do not build a persistent index. During real document ingestion, Chroma files are created under `src/app/backend/data/rag/chroma_db` and remain local/ignored by Git. Uploads are indexed for their conversation before the UI marks them ready; run requests may reference only indexed attachment IDs associated with that conversation. Conversation deletion removes its Chroma chunks and private upload files.

### Frontend commands

Run from `src/app/frontend`:

| Command | Description |
| --- | --- |
| `npm run dev` | Start the Vite development server |
| `npm run typecheck` | Run TypeScript project checks |
| `npm run build` | Type-check and create the production bundle in `dist/` |
| `npm run preview` | Serve the production bundle locally |

## Acceptance checklist

- [x] Google sign-in loads the authenticated user's profile; sign-out invalidates the session.
- [x] A user can create, list, resume, and continue only their own conversations; legacy unowned rows remain unassigned.
- [x] The selected model ID is sent with each new run, and available model choices come from backend configuration.
- [x] Text and supported attachments reach the backend; backend/API errors are visible and failed submissions retain the draft.
- [x] Uploads are authenticated, conversation-scoped, validated, indexed before use, and sent to chat by attachment ID; indexing failures remain visible and retryable.
- [x] Conversation deletion is owner-scoped, confirmed in the UI, stops active runs when possible, and removes conversation messages, runs/events, private files, and RAG chunks.
- [x] Assistant output streams incrementally with persisted event ordering, status, tool activity, completion, failure, cancellation, and replay; backend behavior is covered by focused mocked/API tests.
- [ ] Live provider-backed runs and cancellation should be smoke-tested with valid Google OAuth/OpenAI credentials.
- [ ] Generic HITL request rendering, validation, submission, and run resumption are not implemented.
- [ ] The UI remains usable on narrow screens, with keyboard navigation, visible focus, and reduced-motion support.
- [ ] No API keys, OAuth client secrets, or authorization decisions are exposed to or trusted from the frontend.
