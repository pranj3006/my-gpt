import asyncio
from io import BytesIO
import json
import logging
import math
import os
import re
import secrets
import uuid
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime, time
from pathlib import Path, PureWindowsPath
from urllib.parse import urlencode

import httpx
from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.starlette_client import OAuth
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse, StreamingResponse

import db
from model_catalog import MODEL_IDS, MODEL_OPTIONS
from schemas import (
    AttachmentRef,
    ChatMessageResponse,
    ChatRunCreated,
    ChatRunRequest,
    ConversationCreateRequest,
    ConversationResponse,
    ConversationSummary,
    HumanInputAction,
    HumanInputField,
    HumanInputOption,
    HumanInputRequest,
    HumanInputSubmitRequest,
    HumanInputSubmitResult,
    ModelOption,
    RunSummary,
    UserProfile,
)


load_dotenv(Path(__file__).with_name(".env"))

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("my_gpt.auth")

CALLBACK_URL = os.getenv(
    "GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/callback"
)
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")
FRONTEND_ORIGINS = {
    origin.strip().rstrip("/")
    for origin in os.getenv(
        "FRONTEND_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
}
secure_cookie_setting = os.getenv("SESSION_COOKIE_SECURE")
if secure_cookie_setting is None:
    COOKIE_SECURE = CALLBACK_URL.startswith("https://")
elif secure_cookie_setting.lower() in {"true", "1", "yes"}:
    COOKIE_SECURE = True
elif secure_cookie_setting.lower() in {"false", "0", "no"}:
    COOKIE_SECURE = False
else:
    raise RuntimeError("SESSION_COOKIE_SECURE must be true or false.")
SESSION_SECRET = os.getenv("SESSION_SECRET")
if not SESSION_SECRET:
    SESSION_SECRET = secrets.token_urlsafe(48)
    logger.warning(
        "SESSION_SECRET is not configured; using an ephemeral session key. "
        "Set SESSION_SECRET for stable sessions and multi-worker deployments."
    )
elif len(SESSION_SECRET) < 32:
    raise RuntimeError("SESSION_SECRET must contain at least 32 characters.")

@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    db.init_db()
    from agents import agent_runtime

    async with agent_runtime():
        yield


app = FastAPI(title="My GPT API", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie="my_gpt_session",
    max_age=60 * 60 * 8,
    same_site="lax",
    https_only=COOKIE_SECURE,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(FRONTEND_ORIGINS),
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)

oauth = OAuth()
UPLOAD_DIR = Path(__file__).resolve().parent / "data" / "uploads"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".py", ".csv"}
active_run_tasks: dict[str, asyncio.Task[None]] = {}
conversation_locks: dict[str, asyncio.Lock] = {}


def get_conversation_lock(thread_id: str) -> asyncio.Lock:
    lock = conversation_locks.get(thread_id)
    if lock is None:
        lock = asyncio.Lock()
        conversation_locks[thread_id] = lock
    return lock


def google_client():
    client_id = os.getenv("GOOGLE_SSO_CLIENT_ID") or os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_SSO_CLIENT_SECRET") or os.getenv(
        "GOOGLE_CLIENT_SECRET"
    )
    if not client_id or not client_secret:
        logger.error("Google OAuth is unavailable because credentials are missing.")
        raise HTTPException(
            status_code=503,
            detail="Google sign-in is not configured on the server.",
        )
    return oauth.register(
        name="google",
        client_id=client_id,
        client_secret=client_secret,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )


def login_redirect(error: str | None = None) -> RedirectResponse:
    target = FRONTEND_URL
    if error:
        target = f"{target}/?{urlencode({'auth_error': error})}"
    return RedirectResponse(target, status_code=303)


@app.get("/auth/login")
async def login(request: Request):
    client = google_client()
    logger.info("Starting Google sign-in.")
    return await client.authorize_redirect(request, CALLBACK_URL)


@app.get("/auth/callback")
async def auth_callback(request: Request):
    if request.query_params.get("error"):
        logger.warning("Google sign-in was rejected by the identity provider.")
        return login_redirect("sign_in_cancelled")

    try:
        token = await google_client().authorize_access_token(request)
    except (OAuthError, httpx.HTTPError, ValueError) as error:
        logger.warning(
            "Google OAuth callback validation failed (%s).",
            type(error).__name__,
        )
        request.session.clear()
        return login_redirect("sign_in_failed")

    user_info = token.get("userinfo")
    if not isinstance(user_info, dict):
        logger.error("Google OAuth response did not include verified user information.")
        request.session.clear()
        return login_redirect("sign_in_failed")

    subject = user_info.get("sub")
    email = user_info.get("email")
    if (
        not isinstance(subject, str)
        or not subject
        or not isinstance(email, str)
        or not email
        or user_info.get("email_verified") is not True
    ):
        logger.warning("Google sign-in returned incomplete or unverified identity claims.")
        request.session.clear()
        return login_redirect("sign_in_failed")

    try:
        db.upsert_user(
            user_id=subject,
            email=email,
            name=user_info.get("name") or email,
            picture=user_info.get("picture"),
        )
    except ValueError:
        logger.exception("Verified Google identity could not be persisted.")
        request.session.clear()
        return login_redirect("sign_in_failed")

    request.session.clear()
    request.session["user"] = {
        "id": subject,
        "email": email,
        "name": user_info.get("name") or email,
        "picture": user_info.get("picture"),
    }
    logger.info("Google sign-in succeeded.")
    return RedirectResponse(FRONTEND_URL, status_code=303)


@app.get("/auth/me")
async def current_user(request: Request):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    try:
        stored_user = db.upsert_user(
            user_id=user["id"],
            email=user["email"],
            name=user["name"],
            picture=user.get("picture"),
        )
    except (KeyError, ValueError) as error:
        logger.warning("Authenticated session identity is invalid (%s).", type(error).__name__)
        request.session.clear()
        raise HTTPException(status_code=401, detail="Not authenticated.") from error
    return UserProfile(
        id=stored_user.id,
        email=stored_user.email,
        name=stored_user.name,
        picture=stored_user.picture,
        defaultModelId=stored_user.default_model_id,
        preferences=stored_user.preferences,
    )


def authenticated_user(request: Request) -> dict[str, str]:
    user = request.session.get("user")
    if not isinstance(user, dict) or not isinstance(user.get("id"), str) or not user["id"]:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    return user


def require_trusted_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") not in FRONTEND_ORIGINS:
        raise HTTPException(status_code=403, detail="Untrusted request origin.")


def require_model(model_id: str) -> None:
    if model_id not in MODEL_IDS:
        raise HTTPException(status_code=422, detail="The selected model is not available.")


def conversation_summary(conversation: db.Conversation) -> ConversationSummary:
    return ConversationSummary(
        id=conversation.thread_id,
        title=conversation.title,
        modelId=conversation.model_id,
        createdAt=conversation.created_at,
        updatedAt=conversation.updated_at,
    )


def conversation_payload(thread_id: str, user_id: str) -> ConversationResponse:
    try:
        conversation = db.get_owned_conversation(thread_id, user_id)
        messages = db.get_chat_history(thread_id, user_id)
        runs = db.get_conversation_runs(thread_id, user_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    payload = conversation_summary(conversation).model_dump()
    return ConversationResponse(
        **payload,
        messages=[
            ChatMessageResponse(
                id=str(message.id),
                role=message.role,
                content=message.content,
                attachments=[
                    item.filename
                    for item in db.get_owned_attachments(
                        message.details.get("attachmentIds", []), user_id
                    )
                ],
                incomplete=bool(message.details.get("incomplete", False)),
                createdAt=message.created_at,
            )
            for message in messages
            if message.role in {"user", "assistant"}
        ],
        runs=[
            RunSummary(id=run.id, status=run.status, modelId=run.model_id)
            for run in runs
        ],
    )


@app.get("/api/models", response_model=list[ModelOption])
async def list_models(_user: dict[str, str] = Depends(authenticated_user)):
    return MODEL_OPTIONS


@app.post(
    "/api/conversations",
    response_model=ConversationSummary,
    status_code=201,
    dependencies=[Depends(require_trusted_origin)],
)
async def create_conversation(
    payload: ConversationCreateRequest,
    user: dict[str, str] = Depends(authenticated_user),
):
    require_model(payload.model_id)
    try:
        conversation = db.create_conversation(
            thread_id=str(uuid.uuid4()),
            user_id=user["id"],
            title=payload.title,
            model_id=payload.model_id,
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return conversation_summary(conversation)


@app.get("/api/conversations", response_model=list[ConversationSummary])
async def list_user_conversations(user: dict[str, str] = Depends(authenticated_user)):
    return [conversation_summary(item) for item in db.list_conversations(user["id"])]


@app.get("/api/conversations/{thread_id}", response_model=ConversationResponse)
async def read_conversation(
    thread_id: str,
    user: dict[str, str] = Depends(authenticated_user),
):
    return conversation_payload(thread_id, user["id"])


async def _remove_conversation(
    thread_id: str,
    stop_active_run: bool,
    user: dict[str, str],
):
    try:
        active_runs = db.get_active_conversation_runs(thread_id, user["id"])
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if active_runs and not stop_active_run:
        raise HTTPException(
            status_code=409,
            detail="This conversation has an active response. Confirm stopping it before deleting.",
        )
    if active_runs:
        tasks_to_stop: list[asyncio.Task[None]] = []
        for active_run in active_runs:
            try:
                run = db.request_run_cancellation(active_run.id, user["id"])
            except ValueError as error:
                raise HTTPException(status_code=404, detail=str(error)) from error
            if run.status not in {"queued", "running", "waiting_for_input"}:
                continue
            task = active_run_tasks.get(run.id)
            if task is not None and not task.done():
                task.cancel()
                tasks_to_stop.append(task)
            else:
                db.append_run_event(
                    run_id=run.id,
                    event_type="status",
                    payload={"status": "cancelled"},
                )
                db.append_run_event(run_id=run.id, event_type="cancelled", payload={})
        if tasks_to_stop:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks_to_stop, return_exceptions=True),
                    timeout=5,
                )
            except asyncio.TimeoutError as error:
                raise HTTPException(
                    status_code=409,
                    detail="The active response could not be stopped yet. Please retry deletion.",
                ) from error
        try:
            remaining_runs = db.get_active_conversation_runs(thread_id, user["id"])
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        if remaining_runs:
            raise HTTPException(
                status_code=409,
                detail="The active response could not be stopped yet. Please retry deletion.",
            )

    try:
        storage_keys = db.get_conversation_attachment_keys(thread_id, user["id"])
        if storage_keys:
            from rag import delete_conversation_documents

            await asyncio.to_thread(delete_conversation_documents, thread_id)
        for key in storage_keys:
            stored_path = UPLOAD_DIR / key
            try:
                stored_path.unlink(missing_ok=True)
            except OSError as error:
                logger.exception("A conversation upload could not be removed.")
                raise HTTPException(
                    status_code=503,
                    detail="Conversation files could not be cleaned up. Please retry deletion.",
                ) from error
        db.delete_conversation(thread_id, user["id"])
    except HTTPException:
        raise
    except db.ActiveRunExists as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception as error:
        logger.exception("Conversation retrieval data could not be cleaned up.")
        raise HTTPException(
            status_code=503,
            detail="Conversation documents could not be cleaned up. Please retry deletion.",
        ) from error
    return Response(status_code=204)


@app.delete(
    "/api/conversations/{thread_id}",
    status_code=204,
    dependencies=[Depends(require_trusted_origin)],
)
async def remove_conversation(
    thread_id: str,
    stop_active_run: bool = Query(default=False, alias="stopActiveRun"),
    user: dict[str, str] = Depends(authenticated_user),
):
    try:
        db.get_owned_conversation(thread_id, user["id"])
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    async with get_conversation_lock(thread_id):
        return await _remove_conversation(thread_id, stop_active_run, user)


def validate_upload_content(filename: str, content: bytes) -> None:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        if not content.lstrip().startswith(b"%PDF-"):
            raise HTTPException(status_code=415, detail="The file is not a valid PDF document.")
        return
    if suffix == ".docx":
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                if "[Content_Types].xml" not in archive.namelist() or "word/document.xml" not in archive.namelist():
                    raise HTTPException(status_code=415, detail="The file is not a valid DOCX document.")
        except zipfile.BadZipFile as error:
            raise HTTPException(status_code=415, detail="The file is not a valid DOCX document.") from error
        return
    try:
        content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise HTTPException(status_code=415, detail="Text attachments must be UTF-8 encoded.") from error


async def _upload_attachment(
    thread_id: str,
    file: UploadFile,
    user: dict[str, str],
):
    try:
        db.get_owned_conversation(thread_id, user["id"])
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    raw_filename = file.filename or ""
    filename = PureWindowsPath(raw_filename).name
    filename = Path(filename).name.strip()
    suffix = Path(filename).suffix.lower()
    if not filename or suffix not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail="Unsupported attachment. Use PDF, DOCX, TXT, MD, PY, or CSV files.",
        )
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Attachments must be 10 MB or smaller.")
    validate_upload_content(filename, content)

    attachment_id = str(uuid.uuid4())
    storage_key = f"{attachment_id}{suffix}"
    UPLOAD_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = UPLOAD_DIR / storage_key
    file_descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(file_descriptor, "wb") as stored_file:
            stored_file.write(content)
        attachment = db.create_attachment(
            attachment_id=attachment_id,
            user_id=user["id"],
            thread_id=thread_id,
            filename=filename[:255],
            content_type=file.content_type or "application/octet-stream",
            size_bytes=len(content),
            storage_key=storage_key,
        )
    except Exception:
        path.unlink(missing_ok=True)
        logger.exception("Attachment metadata could not be stored.")
        raise

    from rag import add_document_to_rag, delete_document_from_rag

    async def discard_failed_upload() -> bool:
        vector_cleanup_succeeded = True
        try:
            await asyncio.to_thread(delete_document_from_rag, path, thread_id)
        except Exception:
            logger.exception("Partial RAG data for attachment %s could not be removed.", attachment_id)
            vector_cleanup_succeeded = False
        if not vector_cleanup_succeeded:
            try:
                db.set_attachment_indexing_status(
                    attachment_id,
                    user["id"],
                    thread_id,
                    status="failed",
                    error="Indexing failed and retrieval cleanup did not complete.",
                )
            except Exception:
                logger.exception("Failed attachment %s could not be marked unavailable.", attachment_id)
            return False
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.exception("Failed attachment file %s could not be removed.", attachment_id)
            try:
                db.set_attachment_indexing_status(
                    attachment_id,
                    user["id"],
                    thread_id,
                    status="failed",
                    error="Indexing failed and private file cleanup did not complete.",
                )
            except Exception:
                logger.exception("Failed attachment %s could not be marked unavailable.", attachment_id)
            return False
        try:
            db.delete_attachment(attachment_id, user["id"], thread_id)
        except Exception:
            logger.exception("Failed attachment metadata %s could not be removed.", attachment_id)
            return False
        return True

    indexing_task = asyncio.create_task(asyncio.to_thread(add_document_to_rag, path, thread_id))
    try:
        await asyncio.shield(indexing_task)
        db.set_attachment_indexing_status(
            attachment.id,
            user["id"],
            thread_id,
            status="indexed",
        )
    except asyncio.CancelledError:
        try:
            await asyncio.shield(indexing_task)
        except Exception:
            logger.exception("Cancelled attachment indexing %s did not finish successfully.", attachment_id)
        await discard_failed_upload()
        raise
    except Exception as error:
        logger.exception("Attachment %s could not be indexed.", attachment_id)
        cleanup_succeeded = await discard_failed_upload()
        if isinstance(error, ValueError):
            raise HTTPException(status_code=422, detail=str(error)) from error
        if not cleanup_succeeded:
            raise HTTPException(
                status_code=503,
                detail=(
                    "The document could not be indexed and cleanup did not finish. "
                    "It is unavailable to chat; retry conversation deletion to finish cleanup."
                ),
            ) from error
        raise HTTPException(
            status_code=503,
            detail="The document could not be indexed. Check the file and server embedding configuration, then retry.",
        ) from error

    return AttachmentRef(
        id=attachment.id,
        filename=attachment.filename,
        contentType=attachment.content_type,
        sizeBytes=attachment.size_bytes,
        indexingStatus="indexed",
    )


@app.post(
    "/api/conversations/{thread_id}/attachments",
    response_model=AttachmentRef,
    status_code=201,
    dependencies=[Depends(require_trusted_origin)],
)
async def upload_attachment(
    thread_id: str,
    file: UploadFile = File(...),
    user: dict[str, str] = Depends(authenticated_user),
):
    try:
        db.get_owned_conversation(thread_id, user["id"])
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    async with get_conversation_lock(thread_id):
        return await _upload_attachment(thread_id, file, user)


def normalize_human_input(
    interrupt_value: object,
    *,
    run_id: str,
) -> tuple[HumanInputRequest, str]:
    request_id = str(uuid.uuid4())
    if isinstance(interrupt_value, dict) and "fields" in interrupt_value:
        raw_fields = interrupt_value["fields"]
        raw_actions = interrupt_value.get("actions", [{"id": "continue", "label": "Continue"}])
        if not isinstance(raw_fields, list) or len(raw_fields) > 40:
            raise ValueError("The agent produced an invalid human-input field list.")
        if not isinstance(raw_actions, list) or not raw_actions or len(raw_actions) > 20:
            raise ValueError("The agent produced an invalid human-input action list.")
        fields = [HumanInputField.model_validate(field) for field in raw_fields]
        actions = [HumanInputAction.model_validate(action) for action in raw_actions]
        if len({field.id for field in fields}) != len(fields):
            raise ValueError("The agent produced duplicate human-input field IDs.")
        if len({action.id for action in actions}) != len(actions):
            raise ValueError("The agent produced duplicate human-input action IDs.")
        title = interrupt_value.get("title", "Additional information required")
        description = interrupt_value.get("description")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("The agent produced an invalid human-input title.")
        if description is not None and not isinstance(description, str):
            raise ValueError("The agent produced an invalid human-input description.")
        return (
            HumanInputRequest(
                requestId=request_id,
                runId=run_id,
                title=title.strip()[:200],
                description=description,
                fields=fields,
                actions=actions,
            ),
            "mapping",
        )

    if isinstance(interrupt_value, str):
        prompt = interrupt_value.strip()
    elif isinstance(interrupt_value, dict):
        prompt_value = next(
            (
                interrupt_value[key]
                for key in ("description", "question", "prompt", "message")
                if isinstance(interrupt_value.get(key), str)
            ),
            None,
        )
        prompt = prompt_value.strip() if prompt_value else ""
    else:
        prompt = ""
    if not prompt:
        raise ValueError("The agent produced an unsupported human-input request.")

    if re.search(r"\byes\s*(?:/|or)\s*no\b", prompt, flags=re.IGNORECASE):
        fields = [
            HumanInputField(
                id="decision",
                label="Your decision",
                kind="single_choice",
                required=True,
                options=[
                    HumanInputOption(label="Yes", value="yes"),
                    HumanInputOption(label="No", value="no"),
                ],
            )
        ]
    else:
        fields = [
            HumanInputField(
                id="answer",
                label="Your response",
                kind="textarea",
                required=True,
            )
        ]
    return (
        HumanInputRequest(
            requestId=request_id,
            runId=run_id,
            title="Agent input required",
            description=prompt[:4000],
            fields=fields,
            actions=[HumanInputAction(id="continue", label="Continue")],
        ),
        "string",
    )


def is_cancel_action(action: HumanInputAction) -> bool:
    return action.id.strip().lower() in {"cancel", "stop", "abort"} or action.label.strip().lower().startswith(
        ("cancel", "stop")
    )


def validate_human_input_values(
    request: db.HumanInputRequest,
    *,
    action_id: str,
    values: dict[str, object],
) -> tuple[bool, object]:
    actions = [HumanInputAction.model_validate(action) for action in request.actions]
    action = next((item for item in actions if item.id == action_id), None)
    if action is None:
        raise ValueError("Choose one of the available actions.")
    cancelled = is_cancel_action(action)
    if cancelled:
        return True, {}
    try:
        serialized_values = json.dumps(values, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("The human-input response must contain JSON values.") from error
    if len(serialized_values) > 64_000:
        raise ValueError("The human-input response is too large.")
    fields = [HumanInputField.model_validate(field) for field in request.fields]
    field_ids = {field.id for field in fields}
    unknown_ids = set(values) - field_ids
    if unknown_ids:
        raise ValueError("The response contains an unknown field.")

    validated_values: dict[str, object] = {}
    for field in fields:
        present = field.id in values
        value = values.get(field.id)
        if not present or value is None or value == "":
            if field.required:
                raise ValueError(f"{field.label} is required.")
            continue

        kind = field.kind.strip().lower().replace("-", "_")
        constraints = field.constraints
        options = {option.value for option in field.options}
        if kind in {"text", "textarea"}:
            if not isinstance(value, str):
                raise ValueError(f"{field.label} must be text.")
            if field.required and not value.strip():
                raise ValueError(f"{field.label} is required.")
            min_length = constraints.get("minLength", constraints.get("min_length"))
            max_length = constraints.get("maxLength", constraints.get("max_length"))
            if isinstance(min_length, int) and len(value) < min_length:
                raise ValueError(f"{field.label} is too short.")
            if isinstance(max_length, int) and len(value) > max_length:
                raise ValueError(f"{field.label} is too long.")
            pattern = constraints.get("pattern")
            if pattern is not None:
                if not isinstance(pattern, str):
                    raise ValueError(f"{field.label} has invalid validation rules.")
                try:
                    matches = re.search(pattern, value)
                except re.error as error:
                    raise ValueError(f"{field.label} has invalid validation rules.") from error
                if matches is None:
                    raise ValueError(f"{field.label} has an invalid format.")
        elif kind == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{field.label} must be a finite number.")
            minimum = constraints.get("minimum", constraints.get("min"))
            maximum = constraints.get("maximum", constraints.get("max"))
            if isinstance(minimum, (int, float)) and value < minimum:
                raise ValueError(f"{field.label} is below the minimum.")
            if isinstance(maximum, (int, float)) and value > maximum:
                raise ValueError(f"{field.label} is above the maximum.")
        elif kind == "boolean":
            if not isinstance(value, bool):
                raise ValueError(f"{field.label} must be true or false.")
        elif kind in {"single_choice", "select"}:
            if not isinstance(value, str):
                raise ValueError(f"Choose a valid option for {field.label}.")
            if field.options and value not in options:
                raise ValueError(f"Choose a valid option for {field.label}.")
        elif kind in {"multi_select", "multiselect"}:
            if (
                not isinstance(value, list)
                or any(not isinstance(option, str) for option in value)
                or len(set(value)) != len(value)
                or (field.options and any(option not in options for option in value))
            ):
                raise ValueError(f"Choose valid options for {field.label}.")
        elif kind == "date":
            if not isinstance(value, str):
                raise ValueError(f"{field.label} must be a date.")
            try:
                date.fromisoformat(value)
            except ValueError as error:
                raise ValueError(f"{field.label} must be a valid date.") from error
        elif kind == "time":
            if not isinstance(value, str):
                raise ValueError(f"{field.label} must be a time.")
            try:
                time.fromisoformat(value)
            except ValueError as error:
                raise ValueError(f"{field.label} must be a valid time.") from error
        elif kind in {"datetime", "datetime_local"}:
            if not isinstance(value, str):
                raise ValueError(f"{field.label} must be a date and time.")
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as error:
                raise ValueError(f"{field.label} must be a valid date and time.") from error
        else:
            try:
                json.dumps(value, allow_nan=False)
            except (TypeError, ValueError) as error:
                raise ValueError(f"{field.label} must contain valid JSON data.") from error
        validated_values[field.id] = value

    if request.resume_mode == "string":
        if len(validated_values) != 1:
            raise ValueError("The agent's response must contain exactly one answer.")
        resume_value = next(iter(validated_values.values()))
        if not isinstance(resume_value, str):
            raise ValueError("The agent's response must be text.")
    else:
        resume_value = {"actionId": action_id, "values": validated_values}
    return cancelled, resume_value


async def execute_run(
    run_id: str,
    thread_id: str,
    user_id: str,
    model_id: str,
    content: str,
    attachment_ids: list[str],
    resume_value: object | None = None,
) -> None:
    accumulated_text = ""
    try:
        if resume_value is not None:
            accumulated_text = db.get_run_assistant_content(run_id)
        db.append_run_event(
            run_id=run_id,
            event_type="status",
            payload={"status": "running"},
        )
        if db.run_cancellation_requested(run_id):
            raise asyncio.CancelledError
        attachments = db.get_owned_attachments(attachment_ids, user_id)
        if any(
            attachment.thread_id != thread_id or attachment.indexing_status != "indexed"
            for attachment in attachments
        ):
            raise ValueError("One or more documents are not indexed for this conversation.")
        prompt = content.strip()
        if attachments:
            filenames = ", ".join(item.filename for item in attachments)
            prompt = (
                f"{prompt}\n\nUploaded documents available for this conversation: "
                f"{filenames}. Search them when relevant."
            ).strip()

        from agents import get_agent
        from langchain_core.messages import HumanMessage
        from langgraph.types import Command

        agent = get_agent(model_id)
        config = {
            "configurable": {
                "thread_id": thread_id,
                "user_id": user_id,
            }
        }
        run_input = (
            Command(resume=resume_value)
            if resume_value is not None
            else {"messages": [HumanMessage(content=prompt)]}
        )
        async for event in agent.astream_events(
            run_input,
            config=config,
            version="v2",
        ):
            if db.run_cancellation_requested(run_id):
                raise asyncio.CancelledError
            event_name = event.get("event")
            event_data = event.get("data", {})
            if event_name == "on_chat_model_stream":
                chunk = event_data.get("chunk")
                value = getattr(chunk, "content", "")
                if isinstance(value, str):
                    delta = value
                elif isinstance(value, list):
                    delta = "".join(
                        part.get("text", "")
                        for part in value
                        if isinstance(part, dict) and isinstance(part.get("text"), str)
                    )
                else:
                    delta = ""
                if delta:
                    accumulated_text += delta
                    db.append_run_event(
                        run_id=run_id,
                        event_type="assistant_delta",
                        payload={"delta": delta},
                    )
            elif event_name == "on_tool_start":
                db.append_run_event(
                    run_id=run_id,
                    event_type="tool_call",
                    payload={"name": str(event.get("name") or "Tool")[:80]},
                )
            elif event_name == "on_tool_end":
                db.append_run_event(
                    run_id=run_id,
                    event_type="tool_result",
                    payload={"name": str(event.get("name") or "Tool")[:80]},
                )

        if db.run_cancellation_requested(run_id):
            raise asyncio.CancelledError
        snapshot = await agent.aget_state(config)
        interrupts = [
            interrupt
            for task in snapshot.tasks
            for interrupt in task.interrupts
        ]
        if interrupts:
            if len(interrupts) != 1:
                raise ValueError("The agent requested multiple inputs at the same time.")
            interrupt_value = getattr(interrupts[0], "value", interrupts[0])
            input_request, resume_mode = normalize_human_input(
                interrupt_value,
                run_id=run_id,
            )
            db.create_human_input_request(
                request_id=input_request.request_id,
                run_id=run_id,
                title=input_request.title,
                description=input_request.description,
                fields=[field.model_dump() for field in input_request.fields],
                actions=[action.model_dump() for action in input_request.actions],
                resume_mode=resume_mode,
            )
            db.save_run_partial_text(run_id, accumulated_text)
            db.append_run_event(
                run_id=run_id,
                event_type="status",
                payload={"status": "waiting_for_input"},
            )
            db.append_run_event(
                run_id=run_id,
                event_type="human_input_request",
                payload=input_request.model_dump(by_alias=True),
                correlation_id=input_request.request_id,
            )
            return

        assistant_id = None
        if accumulated_text:
            assistant_id = db.save_assistant_message(
                thread_id=thread_id,
                user_id=user_id,
                run_id=run_id,
                model_id=model_id,
                content=accumulated_text,
            )
        db.append_run_event(
            run_id=run_id,
            event_type="status",
            payload={"status": "completed"},
            message_id=assistant_id,
        )
        db.append_run_event(
            run_id=run_id,
            event_type="completed",
            payload={"messageId": str(assistant_id) if assistant_id is not None else None},
            message_id=assistant_id,
        )
    except asyncio.CancelledError:
        db.close_pending_human_input_requests(run_id)
        if accumulated_text:
            db.save_assistant_message(
                thread_id=thread_id,
                user_id=user_id,
                run_id=run_id,
                model_id=model_id,
                content=accumulated_text,
                incomplete=True,
            )
        if db.get_owned_run(run_id, user_id).status != "cancelled":
            db.append_run_event(run_id=run_id, event_type="status", payload={"status": "cancelled"})
            db.append_run_event(run_id=run_id, event_type="cancelled", payload={})
    except Exception:
        logger.exception("Chat run %s failed.", run_id)
        db.close_pending_human_input_requests(run_id)
        if accumulated_text:
            db.save_assistant_message(
                thread_id=thread_id,
                user_id=user_id,
                run_id=run_id,
                model_id=model_id,
                content=accumulated_text,
                incomplete=True,
            )
        db.append_run_event(run_id=run_id, event_type="status", payload={"status": "failed"})
        db.append_run_event(
            run_id=run_id,
            event_type="error",
            payload={"message": "The assistant could not complete this response. Please try again."},
        )
    finally:
        active_run_tasks.pop(run_id, None)


async def _create_chat_run(
    thread_id: str,
    payload: ChatRunRequest,
    user: dict[str, str],
):
    require_model(payload.model_id)
    if not payload.content.strip() and not payload.attachments:
        raise HTTPException(status_code=422, detail="Write a message or attach a document.")
    run_id = str(uuid.uuid4())
    try:
        run, message = db.create_run_with_user_message(
            run_id=run_id,
            thread_id=thread_id,
            user_id=user["id"],
            model_id=payload.model_id,
            content=payload.content.strip(),
            attachment_ids=payload.attachments,
        )
    except db.ActiveRunExists as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except db.ActiveRunLimitExceeded as error:
        raise HTTPException(status_code=429, detail=str(error)) from error
    except db.InvalidAttachment as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    db.append_run_event(run_id=run.id, event_type="status", payload={"status": "queued"})
    active_run_tasks[run_id] = asyncio.create_task(
        execute_run(
            run_id,
            thread_id,
            user["id"],
            payload.model_id,
            payload.content,
            payload.attachments,
        )
    )
    return ChatRunCreated(
        runId=run_id,
        conversationId=thread_id,
        userMessageId=str(message.id),
    )


@app.post(
    "/api/conversations/{thread_id}/runs",
    response_model=ChatRunCreated,
    status_code=202,
    dependencies=[Depends(require_trusted_origin)],
)
async def create_chat_run(
    thread_id: str,
    payload: ChatRunRequest,
    user: dict[str, str] = Depends(authenticated_user),
):
    try:
        db.get_owned_conversation(thread_id, user["id"])
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    async with get_conversation_lock(thread_id):
        return await _create_chat_run(thread_id, payload, user)


@app.get("/api/runs/{run_id}/events")
async def stream_run_events(
    run_id: str,
    request: Request,
    after_sequence: int = Query(default=-1, alias="afterSequence"),
    user: dict[str, str] = Depends(authenticated_user),
):
    if after_sequence < -1:
        raise HTTPException(status_code=422, detail="afterSequence must be at least -1.")
    try:
        db.get_owned_run(run_id, user["id"])
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    last_event_id = request.headers.get("last-event-id")
    if last_event_id is not None:
        try:
            after_sequence = max(after_sequence, int(last_event_id))
        except ValueError as error:
            raise HTTPException(status_code=400, detail="Last-Event-ID must be an integer.") from error

    async def event_stream():
        cursor = after_sequence
        while True:
            if await request.is_disconnected():
                return
            events = db.get_run_events(run_id, cursor)
            for event in events:
                cursor = event["sequence"]
                yield (
                    f"id: {cursor}\n"
                    f"event: {event['type']}\n"
                    f"data: {json.dumps(event, separators=(',', ':'))}\n\n"
                )
            run = db.get_owned_run(run_id, user["id"])
            if run.status in {"completed", "cancelled", "failed"} and not events:
                return
            if not events:
                await asyncio.sleep(0.35)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post(
    "/api/runs/{run_id}/human-input",
    response_model=HumanInputSubmitResult,
    status_code=202,
    dependencies=[Depends(require_trusted_origin)],
)
async def submit_human_input(
    run_id: str,
    payload: HumanInputSubmitRequest,
    user: dict[str, str] = Depends(authenticated_user),
):
    if payload.run_id != run_id:
        raise HTTPException(status_code=422, detail="The response runId does not match the request URL.")
    try:
        initial_request = db.get_owned_human_input_request(payload.request_id, user["id"])
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if initial_request.run_id != run_id:
        raise HTTPException(status_code=404, detail="Human-input request does not belong to this run.")

    async with get_conversation_lock(initial_request.thread_id):
        task = active_run_tasks.get(run_id)
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5)
            except asyncio.TimeoutError as error:
                raise HTTPException(
                    status_code=409,
                    detail="The run is still saving its paused state. Please retry your response.",
                ) from error
        try:
            pending_request = db.get_owned_human_input_request(payload.request_id, user["id"])
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        if pending_request.run_id != run_id:
            raise HTTPException(status_code=404, detail="Human-input request does not belong to this run.")
        if pending_request.status != "pending":
            raise HTTPException(status_code=409, detail="This human-input request is no longer pending.")
        try:
            cancelled, resume_value = validate_human_input_values(
                pending_request,
                action_id=payload.action_id,
                values=payload.values,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        try:
            db.resolve_human_input_request(
                request_id=payload.request_id,
                run_id=run_id,
                user_id=user["id"],
                action_id=payload.action_id,
                values={} if cancelled else payload.values,
                cancelled=cancelled,
            )
            run = db.get_owned_run(run_id, user["id"])
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

        if cancelled:
            task = active_run_tasks.get(run_id)
            if task is not None and not task.done():
                task.cancel()
            else:
                partial = db.get_run_assistant_content(run_id)
                if partial:
                    db.save_assistant_message(
                        thread_id=run.thread_id,
                        user_id=user["id"],
                        run_id=run_id,
                        model_id=run.model_id,
                        content=partial,
                        incomplete=True,
                    )
                db.append_run_event(run_id=run_id, event_type="status", payload={"status": "cancelled"})
                db.append_run_event(run_id=run_id, event_type="cancelled", payload={})
            return HumanInputSubmitResult(
                requestId=payload.request_id,
                runId=run_id,
                status="cancelled",
            )

        active_run_tasks[run_id] = asyncio.create_task(
            execute_run(
                run_id,
                run.thread_id,
                user["id"],
                run.model_id,
                "",
                [],
                resume_value,
            )
        )
        return HumanInputSubmitResult(
            requestId=payload.request_id,
            runId=run_id,
            status="resuming",
        )


@app.post(
    "/api/runs/{run_id}/cancel",
    status_code=202,
    dependencies=[Depends(require_trusted_origin)],
)
async def cancel_chat_run(
    run_id: str,
    user: dict[str, str] = Depends(authenticated_user),
):
    try:
        run = db.request_run_cancellation(run_id, user["id"])
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    task = active_run_tasks.get(run_id)
    if run.status in {"queued", "running", "waiting_for_input"} and run.cancellation_requested:
        if task is None or task.done():
            partial = db.get_run_assistant_content(run_id)
            if partial:
                db.save_assistant_message(
                    thread_id=run.thread_id,
                    user_id=user["id"],
                    run_id=run_id,
                    model_id=run.model_id,
                    content=partial,
                    incomplete=True,
                )
            db.append_run_event(
                run_id=run_id,
                event_type="status",
                payload={"status": "cancelled"},
            )
            db.append_run_event(run_id=run_id, event_type="cancelled", payload={})
        else:
            task.cancel()
    return {"runId": run_id, "status": "cancellation_requested" if run.cancellation_requested else run.status}


@app.post("/auth/logout", status_code=204)
async def logout(request: Request):
    origin = request.headers.get("origin", "").rstrip("/")
    if origin not in FRONTEND_ORIGINS:
        logger.warning("Rejected logout request from an untrusted origin.")
        raise HTTPException(status_code=403, detail="Untrusted request origin.")
    request.session.clear()
    response = Response(status_code=204)
    response.delete_cookie(
        "my_gpt_session",
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    logger.info("User signed out.")
    return response
