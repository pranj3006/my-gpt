---
name: My GPT Development Standards
description: "Use when implementing or reviewing My GPT code. Covers architecture, security, data compatibility, accessibility, and the core run/build/test commands."
applyTo: "src/app/**"
---

# My GPT development standards and commands

## Engineering approach

- Inspect relevant existing code and README acceptance criteria before editing. Make precise changes, preserve unrelated work, and update related docs/contracts when behavior changes.
- Keep responsibilities separated: FastAPI routes/auth and service logic in the backend; database access in persistence helpers; request/response validation in Pydantic schemas; API calls, error handling, and stream parsing in a frontend service/adapter; presentation in React components.
- Keep the UI provider- and framework-agnostic. Normalize backend-specific events and HITL payloads at an adapter boundary.
- Use existing patterns, shared types, and dependencies. Prefer clear typed code over casts, duplicated helpers, silent defaults, or broad exception handling. Surface actionable errors.
- Implement the requested behavior end-to-end when practical. A schema, database model, demo, or type alone does not establish that an API feature works.

## Security and data integrity

- Derive identity from the authenticated server session. Never trust a client-supplied user ID, hidden UI control, or browser storage as authorization.
- Authorize every conversation, run, attachment, and HITL operation against the authenticated owner. Use explicit errors for nonexistent or unauthorized resources.
- Never expose secrets, credentials, checkpoint internals, or unfiltered tool data to the frontend. Enforce validation, limits, rate controls, and authorization server-side.
- Preserve existing data. Use tested, repeatable additive migrations; do not drop/recreate tables or silently assign ownership to legacy unowned data.
- Use stable resource IDs, explicit transaction/session lifecycle, valid state transitions, and monotonically increasing per-run event sequence numbers. Define persistence and recovery semantics for run state and final messages.
- Validate model IDs, message roles, statuses, attachment metadata, HITL pending state, allowed actions, and typed field values at API boundaries.
- Keep Python Pydantic wire aliases and TypeScript shapes aligned (camelCase on the wire). Update both and test the contract when it changes.

## UI quality and accessibility

- Keep busy, disabled, error, and retry states understandable. Preserve the composer draft on failure and prevent accidental duplicate active submissions.
- Do not display fabricated tool activity as backend execution. Only expose events returned by the backend.
- Preserve keyboard support (Enter submits; Shift+Enter adds a newline), visible focus, mobile navigation, responsive layout, reduced-motion behavior, labels, and validation errors.
- Treat local storage only as an explicit cache, not durable history or authorization state.

## Core commands

Run frontend commands from `src/app/frontend`:

```bash
npm install
npm run dev
npm run typecheck
npm run build
```

Run backend setup/start commands from `src/app/backend` using the project's configured Python environment:

```bash
pip install -r requirements.txt
uvicorn app:app --reload --port 8000
```

Run the existing focused backend unit tests from `src/app/backend`:

```bash
python -m unittest discover -s tests -v
```

The backend tests include API-level coverage for ownership, event replay, uploads, and mocked agent execution, alongside RAG helper and agent configuration tests. They do not prove live provider-backed execution. Add/use focused tests with the configured environment for backend changes; do not treat Python syntax checks or schema-only tests as API coverage. Use the smallest relevant checks, then run the frontend typecheck/build or broader suite when affected.

## Before calling work complete

- Verify behavior against the actual requirement and README acceptance criteria, not only a proxy such as compilation.
- Test owner isolation, migration compatibility, validation, and failure behavior for data/API changes as relevant.
- Test frontend error/draft behavior, event order, keyboard/accessibility, and real backend integration as relevant.
- State clearly what remains demo-only or unimplemented. Avoid claiming end-to-end functionality based on types/models alone.
