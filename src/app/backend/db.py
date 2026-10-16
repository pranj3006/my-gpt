from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    inspect,
    func,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Path("data").mkdir(exist_ok=True)

DATABASE_URL = "sqlite:///data/chatbot_memory.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class ActiveRunExists(ValueError):
    pass


class ActiveRunLimitExceeded(ValueError):
    pass


class InvalidAttachment(ValueError):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True)  # Stable identity-provider subject.
    email = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    picture = Column(String)
    default_model_id = Column(String, nullable=False, default="gpt-4o-mini")
    preferences = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    conversations = relationship("Conversation", back_populates="user")


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, index=True)
    thread_id = Column(String, unique=True, index=True, nullable=False)
    user_id = Column(String, ForeignKey("users.id"), index=True)
    title = Column(String, nullable=False, default="New Chat")
    model_id = Column(String, nullable=False, default="gpt-4o-mini")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    user = relationship("User", back_populates="conversations")
    messages = relationship("ChatMessage", back_populates="conversation")
    runs = relationship("ChatRun", back_populates="conversation")


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant', 'tool', 'system')",
            name="ck_chat_message_role",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    thread_id = Column(
        String,
        ForeignKey("conversations.thread_id"),
        index=True,
        nullable=False,
    )
    run_id = Column(String, ForeignKey("chat_runs.id"), index=True)
    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    model_id = Column(String)
    details = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    conversation = relationship("Conversation", back_populates="messages")
    attachments = relationship("Attachment", back_populates="message")


class ChatRun(Base):
    __tablename__ = "chat_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'waiting_for_input', "
            "'completed', 'cancelled', 'failed')",
            name="ck_chat_run_status",
        ),
    )

    id = Column(String, primary_key=True)  # Stable run ID used by the stream and HITL.
    thread_id = Column(
        String,
        ForeignKey("conversations.thread_id"),
        index=True,
        nullable=False,
    )
    model_id = Column(String, nullable=False)
    status = Column(String, nullable=False, default="queued", index=True)
    error = Column(Text)
    cancellation_requested = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)

    conversation = relationship("Conversation", back_populates="runs")
    events = relationship("ConversationEvent", back_populates="run")
    human_input_requests = relationship("HumanInputRequest", back_populates="run")


class ConversationEvent(Base):
    __tablename__ = "conversation_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_run_event_sequence"),
        CheckConstraint("sequence >= 0", name="ck_run_event_sequence"),
    )

    id = Column(Integer, primary_key=True)
    thread_id = Column(
        String,
        ForeignKey("conversations.thread_id"),
        index=True,
        nullable=False,
    )
    run_id = Column(String, ForeignKey("chat_runs.id"), index=True, nullable=False)
    sequence = Column(Integer, nullable=False)
    correlation_id = Column(String, index=True)
    message_id = Column(Integer, ForeignKey("chat_messages.id"))
    event_type = Column(String, nullable=False)
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    run = relationship("ChatRun", back_populates="events")


class Attachment(Base):
    __tablename__ = "attachments"
    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="ck_attachment_size"),
    )

    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id"), index=True, nullable=False)
    thread_id = Column(String, ForeignKey("conversations.thread_id"), index=True)
    message_id = Column(Integer, ForeignKey("chat_messages.id"), index=True)
    filename = Column(String, nullable=False)
    content_type = Column(String, nullable=False)
    size_bytes = Column(Integer, nullable=False)
    storage_key = Column(String, unique=True, nullable=False)
    indexing_status = Column(String, nullable=False, default="indexed")
    indexing_error = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    message = relationship("ChatMessage", back_populates="attachments")


class HumanInputRequest(Base):
    __tablename__ = "human_input_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'responded', 'cancelled')",
            name="ck_human_input_status",
        ),
    )

    request_id = Column(String, primary_key=True)
    run_id = Column(String, ForeignKey("chat_runs.id"), index=True, nullable=False)
    thread_id = Column(
        String,
        ForeignKey("conversations.thread_id"),
        index=True,
        nullable=False,
    )
    title = Column(String, nullable=False)
    description = Column(Text)
    fields = Column(JSON, nullable=False, default=list)
    actions = Column(JSON, nullable=False, default=list)
    status = Column(String, nullable=False, default="pending", index=True)
    action_id = Column(String)
    response_values = Column(JSON)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    resolved_at = Column(DateTime)

    run = relationship("ChatRun", back_populates="human_input_requests")


class LongTermMemory(Base):
    __tablename__ = "long_term_memory"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, ForeignKey("users.id"), index=True)
    thread_id = Column(String, ForeignKey("conversations.thread_id"), index=True)
    memory = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


def _migrate_legacy_columns() -> None:
    """Add new nullable/defaulted columns without discarding existing chat data."""
    additions = {
        "conversations": {
            "user_id": "VARCHAR REFERENCES users(id)",
            "model_id": "VARCHAR NOT NULL DEFAULT 'gpt-4o-mini'",
        },
        "chat_messages": {
            "run_id": "VARCHAR REFERENCES chat_runs(id)",
            "model_id": "VARCHAR",
            "details": "JSON NOT NULL DEFAULT '{}'",
        },
        "long_term_memory": {
            "user_id": "VARCHAR REFERENCES users(id)",
        },
        "attachments": {
            "indexing_status": "VARCHAR NOT NULL DEFAULT 'indexed'",
            "indexing_error": "TEXT",
        },
    }

    with engine.begin() as connection:
        inspector = inspect(connection)
        for table_name, columns in additions.items():
            if not inspector.has_table(table_name):
                continue
            existing = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, definition in columns.items():
                if column_name not in existing:
                    connection.exec_driver_sql(
                        f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"
                    )


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _migrate_legacy_columns()


def upsert_user(
    *,
    user_id: str,
    email: str,
    name: str,
    picture: str | None,
) -> User:
    """Persist only an identity already verified by the Google OIDC callback."""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        email_owner = db.query(User).filter(User.email == email, User.id != user_id).first()
        if email_owner is not None:
            raise ValueError("The verified email is already linked to another account.")
        if user is None:
            user = User(id=user_id, email=email, name=name, picture=picture)
            db.add(user)
        else:
            user.email = email
            user.name = name
            user.picture = picture
            user.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()


def create_conversation(
    *,
    thread_id: str,
    user_id: str,
    title: str = "New Chat",
    model_id: str = "gpt-4o-mini",
) -> Conversation:
    if not thread_id or not user_id:
        raise ValueError("A conversation ID and signed-in user are required.")
    db = SessionLocal()
    try:
        if db.query(User.id).filter(User.id == user_id).first() is None:
            raise ValueError("The signed-in user has not been persisted.")
        conversation = Conversation(
            thread_id=thread_id,
            user_id=user_id,
            title=title.strip()[:120] or "New Chat",
            model_id=model_id,
        )
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        return conversation
    finally:
        db.close()


def get_owned_conversation(thread_id: str, user_id: str) -> Conversation:
    db = SessionLocal()
    try:
        conversation = _owned_conversation(db, thread_id, user_id)
        db.expunge(conversation)
        return conversation
    finally:
        db.close()


def delete_conversation(thread_id: str, user_id: str) -> list[str]:
    """Delete an owned conversation and return its attachment storage keys."""
    db = SessionLocal()
    try:
        conversation = _owned_conversation(db, thread_id, user_id)
        if (
            db.query(ChatRun.id)
            .filter(
                ChatRun.thread_id == thread_id,
                ChatRun.status.in_(("queued", "running", "waiting_for_input")),
            )
            .first()
            is not None
        ):
            raise ActiveRunExists("Stop the active response before deleting this conversation.")
        attachment_keys = [
            row.storage_key
            for row in db.query(Attachment.storage_key)
            .filter(Attachment.thread_id == thread_id)
            .all()
        ]
        run_ids = [
            row.id for row in db.query(ChatRun.id).filter(ChatRun.thread_id == thread_id)
        ]
        if run_ids:
            db.query(HumanInputRequest).filter(
                HumanInputRequest.thread_id == thread_id
            ).delete(synchronize_session=False)
            db.query(ConversationEvent).filter(
                ConversationEvent.thread_id == thread_id
            ).delete(synchronize_session=False)
        db.query(Attachment).filter(Attachment.thread_id == thread_id).delete(
            synchronize_session=False
        )
        db.query(ChatMessage).filter(ChatMessage.thread_id == thread_id).delete(
            synchronize_session=False
        )
        db.query(LongTermMemory).filter(
            LongTermMemory.thread_id == thread_id
        ).delete(synchronize_session=False)
        if run_ids:
            db.query(ChatRun).filter(ChatRun.thread_id == thread_id).delete(
                synchronize_session=False
            )
        db.delete(conversation)
        db.commit()
        return attachment_keys
    finally:
        db.close()


def get_conversation_attachment_keys(thread_id: str, user_id: str) -> list[str]:
    db = SessionLocal()
    try:
        _owned_conversation(db, thread_id, user_id)
        return [
            row.storage_key
            for row in db.query(Attachment.storage_key)
            .filter(Attachment.thread_id == thread_id)
            .all()
        ]
    finally:
        db.close()


def get_active_conversation_runs(thread_id: str, user_id: str) -> list[ChatRun]:
    db = SessionLocal()
    try:
        _owned_conversation(db, thread_id, user_id)
        runs = (
            db.query(ChatRun)
            .filter(
                ChatRun.thread_id == thread_id,
                ChatRun.status.in_(("queued", "running", "waiting_for_input")),
            )
            .all()
        )
        for run in runs:
            db.expunge(run)
        return runs
    finally:
        db.close()


def _owned_conversation(db: Any, thread_id: str, user_id: str) -> Conversation:
    conversation = (
        db.query(Conversation)
        .filter(
            Conversation.thread_id == thread_id,
            Conversation.user_id == user_id,
        )
        .first()
    )
    if conversation is None:
        raise ValueError("Conversation does not exist or is not owned by this user.")
    return conversation


def create_or_update_conversation(
    thread_id: str,
    user_id: str,
    first_message: str | None = None,
    model_id: str = "gpt-4o-mini",
) -> None:
    if not user_id:
        raise ValueError("A signed-in user ID is required.")

    db = SessionLocal()
    try:
        conversation = (
            db.query(Conversation)
            .filter(Conversation.thread_id == thread_id)
            .first()
        )
        if conversation is None:
            title = "New Chat"
            if first_message:
                normalized_message = first_message.strip()
                title = normalized_message[:40]
                if len(normalized_message) > 40:
                    title += "..."
            conversation = Conversation(
                thread_id=thread_id,
                user_id=user_id,
                title=title,
                model_id=model_id,
            )
            db.add(conversation)
        elif conversation.user_id != user_id:
            raise ValueError("Conversation does not exist or is not owned by this user.")
        else:
            conversation.updated_at = datetime.utcnow()
            conversation.model_id = model_id
        db.commit()
    finally:
        db.close()


def list_conversations(user_id: str) -> list[Conversation]:
    db = SessionLocal()
    try:
        return (
            db.query(Conversation)
            .filter(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc())
            .all()
        )
    finally:
        db.close()


def create_run_with_user_message(
    *,
    run_id: str,
    thread_id: str,
    user_id: str,
    model_id: str,
    content: str,
    attachment_ids: list[str],
) -> tuple[ChatRun, ChatMessage]:
    db = SessionLocal()
    try:
        conversation = _owned_conversation(db, thread_id, user_id)
        if (
            db.query(ChatRun.id)
            .filter(
                ChatRun.thread_id == thread_id,
                ChatRun.status.in_(("queued", "running", "waiting_for_input")),
            )
            .first()
            is not None
        ):
            raise ActiveRunExists("A response is already running in this conversation.")
        active_run_count = (
            db.query(ChatRun.id)
            .join(Conversation, Conversation.thread_id == ChatRun.thread_id)
            .filter(
                Conversation.user_id == user_id,
                ChatRun.status.in_(("queued", "running", "waiting_for_input")),
            )
            .count()
        )
        if active_run_count >= 3:
            raise ActiveRunLimitExceeded("You already have three active responses. Stop one before starting another.")
        attachments = []
        if attachment_ids:
            attachments = (
                db.query(Attachment)
                .filter(
                    Attachment.id.in_(attachment_ids),
                    Attachment.user_id == user_id,
                    Attachment.thread_id == thread_id,
                    Attachment.indexing_status == "indexed",
                )
                .all()
            )
            if len(attachments) != len(set(attachment_ids)):
                raise InvalidAttachment(
                    "One or more documents are not indexed for this conversation."
                )
        run = ChatRun(id=run_id, thread_id=thread_id, model_id=model_id, status="queued")
        db.add(run)
        db.flush()
        message = ChatMessage(
            thread_id=thread_id,
            run_id=run_id,
            role="user",
            content=content,
            model_id=model_id,
            details={"attachmentIds": attachment_ids},
        )
        db.add(message)
        db.flush()
        for attachment in attachments:
            attachment.thread_id = thread_id
            attachment.message_id = message.id
        if conversation.title == "New Chat":
            normalized = content.strip()
            if normalized:
                conversation.title = normalized[:40] + ("..." if len(normalized) > 40 else "")
        conversation.model_id = model_id
        conversation.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(run)
        db.refresh(message)
        return run, message
    finally:
        db.close()


def get_owned_run(run_id: str, user_id: str) -> ChatRun:
    db = SessionLocal()
    try:
        run = (
            db.query(ChatRun)
            .join(Conversation, Conversation.thread_id == ChatRun.thread_id)
            .filter(ChatRun.id == run_id, Conversation.user_id == user_id)
            .first()
        )
        if run is None:
            raise ValueError("Run does not exist or is not owned by this user.")
        db.expunge(run)
        return run
    finally:
        db.close()


def set_run_status(
    run_id: str,
    *,
    status: str,
    error: str | None = None,
) -> None:
    db = SessionLocal()
    try:
        run = db.query(ChatRun).filter(ChatRun.id == run_id).first()
        if run is None:
            raise ValueError("Run does not exist.")
        run.status = status
        run.error = error
        if status == "running" and run.started_at is None:
            run.started_at = datetime.utcnow()
        if status in {"completed", "cancelled", "failed"}:
            run.completed_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()


def request_run_cancellation(run_id: str, user_id: str) -> ChatRun:
    db = SessionLocal()
    try:
        run = (
            db.query(ChatRun)
            .join(Conversation, Conversation.thread_id == ChatRun.thread_id)
            .filter(ChatRun.id == run_id, Conversation.user_id == user_id)
            .first()
        )
        if run is None:
            raise ValueError("Run does not exist or is not owned by this user.")
        if run.status in {"queued", "running", "waiting_for_input"}:
            run.cancellation_requested = True
            db.commit()
            db.refresh(run)
        db.expunge(run)
        return run
    finally:
        db.close()


def run_cancellation_requested(run_id: str) -> bool:
    db = SessionLocal()
    try:
        run = db.query(ChatRun).filter(ChatRun.id == run_id).first()
        return run is None or run.cancellation_requested
    finally:
        db.close()


def append_run_event(
    *,
    run_id: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
    correlation_id: str | None = None,
    message_id: int | None = None,
) -> dict[str, Any]:
    """Persist an event and allocate its sequence in the same transaction."""
    db = SessionLocal()
    try:
        run = db.query(ChatRun).filter(ChatRun.id == run_id).with_for_update().first()
        if run is None:
            raise ValueError("Run does not exist.")
        current = (
            db.query(func.max(ConversationEvent.sequence))
            .filter(ConversationEvent.run_id == run_id)
            .scalar()
        )
        sequence = 0 if current is None else current + 1
        event_payload = payload or {}
        event = ConversationEvent(
            thread_id=run.thread_id,
            run_id=run_id,
            sequence=sequence,
            correlation_id=correlation_id,
            message_id=message_id,
            event_type=event_type,
            payload=event_payload,
        )
        db.add(event)
        status = event_payload.get("status")
        if event_type == "status" and status in {
            "queued", "running", "waiting_for_input", "completed", "cancelled", "failed"
        }:
            run.status = status
            if status == "running" and run.started_at is None:
                run.started_at = datetime.utcnow()
            if status in {"completed", "cancelled", "failed"}:
                run.completed_at = datetime.utcnow()
        db.commit()
        return {
            "type": event_type,
            "conversationId": run.thread_id,
            "runId": run_id,
            "sequence": sequence,
            "correlationId": correlation_id,
            "messageId": str(message_id) if message_id is not None else None,
            "payload": event_payload,
        }
    finally:
        db.close()


def get_run_events(run_id: str, after_sequence: int = -1) -> list[dict[str, Any]]:
    db = SessionLocal()
    try:
        events = (
            db.query(ConversationEvent)
            .filter(
                ConversationEvent.run_id == run_id,
                ConversationEvent.sequence > after_sequence,
            )
            .order_by(ConversationEvent.sequence.asc())
            .all()
        )
        return [
            {
                "type": event.event_type,
                "conversationId": event.thread_id,
                "runId": event.run_id,
                "sequence": event.sequence,
                "correlationId": event.correlation_id,
                "messageId": str(event.message_id) if event.message_id is not None else None,
                "payload": event.payload,
            }
            for event in events
        ]
    finally:
        db.close()


def create_attachment(
    *,
    attachment_id: str,
    user_id: str,
    thread_id: str,
    filename: str,
    content_type: str,
    size_bytes: int,
    storage_key: str,
    indexing_status: str = "pending",
) -> Attachment:
    db = SessionLocal()
    try:
        _owned_conversation(db, thread_id, user_id)
        attachment = Attachment(
            id=attachment_id,
            user_id=user_id,
            thread_id=thread_id,
            filename=filename,
            content_type=content_type,
            size_bytes=size_bytes,
            storage_key=storage_key,
            indexing_status=indexing_status,
        )
        db.add(attachment)
        db.commit()
        db.refresh(attachment)
        return attachment
    finally:
        db.close()


def set_attachment_indexing_status(
    attachment_id: str,
    user_id: str,
    thread_id: str,
    *,
    status: str,
    error: str | None = None,
) -> None:
    if status not in {"pending", "indexed", "failed"}:
        raise ValueError("Unsupported attachment indexing status.")
    db = SessionLocal()
    try:
        attachment = (
            db.query(Attachment)
            .filter(
                Attachment.id == attachment_id,
                Attachment.user_id == user_id,
                Attachment.thread_id == thread_id,
            )
            .first()
        )
        if attachment is None:
            raise ValueError("Attachment does not exist for this conversation.")
        attachment.indexing_status = status
        attachment.indexing_error = error
        db.commit()
    finally:
        db.close()


def delete_attachment(
    attachment_id: str,
    user_id: str,
    thread_id: str,
) -> str | None:
    db = SessionLocal()
    try:
        attachment = (
            db.query(Attachment)
            .filter(
                Attachment.id == attachment_id,
                Attachment.user_id == user_id,
                Attachment.thread_id == thread_id,
            )
            .first()
        )
        if attachment is None:
            return None
        storage_key = attachment.storage_key
        db.delete(attachment)
        db.commit()
        return storage_key
    finally:
        db.close()


def get_owned_attachments(attachment_ids: list[str], user_id: str) -> list[Attachment]:
    if not attachment_ids:
        return []
    db = SessionLocal()
    try:
        attachments = (
            db.query(Attachment)
            .filter(Attachment.id.in_(attachment_ids), Attachment.user_id == user_id)
            .all()
        )
        if len(attachments) != len(set(attachment_ids)):
            raise ValueError("One or more attachments do not belong to this user.")
        return [
            Attachment(
                id=item.id,
                user_id=item.user_id,
                thread_id=item.thread_id,
                message_id=item.message_id,
                filename=item.filename,
                content_type=item.content_type,
                size_bytes=item.size_bytes,
                storage_key=item.storage_key,
                indexing_status=item.indexing_status,
                indexing_error=item.indexing_error,
                created_at=item.created_at,
            )
            for item in attachments
        ]
    finally:
        db.close()


def save_chat_message(
    thread_id: str,
    user_id: str,
    role: str,
    content: str,
    *,
    run_id: str | None = None,
    model_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    if role not in {"user", "assistant", "tool", "system"}:
        raise ValueError("Unsupported chat message role.")

    db = SessionLocal()
    try:
        conversation = _owned_conversation(db, thread_id, user_id)
        if run_id is not None:
            run = (
                db.query(ChatRun)
                .filter(ChatRun.id == run_id, ChatRun.thread_id == thread_id)
                .first()
            )
            if run is None:
                raise ValueError("Run does not exist for this conversation.")
        db.add(
            ChatMessage(
                thread_id=thread_id,
                run_id=run_id,
                role=role,
                content=content,
                model_id=model_id,
                details=details or {},
            )
        )
        conversation.updated_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()


def get_chat_history(thread_id: str, user_id: str) -> list[ChatMessage]:
    db = SessionLocal()
    try:
        _owned_conversation(db, thread_id, user_id)
        return (
            db.query(ChatMessage)
            .filter(ChatMessage.thread_id == thread_id)
            .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
            .all()
        )
    finally:
        db.close()


def get_conversation_runs(thread_id: str, user_id: str) -> list[ChatRun]:
    db = SessionLocal()
    try:
        _owned_conversation(db, thread_id, user_id)
        runs = (
            db.query(ChatRun)
            .filter(ChatRun.thread_id == thread_id)
            .order_by(ChatRun.created_at.asc())
            .all()
        )
        for run in runs:
            db.expunge(run)
        return runs
    finally:
        db.close()


def save_assistant_message(
    *,
    thread_id: str,
    user_id: str,
    run_id: str,
    model_id: str,
    content: str,
    incomplete: bool = False,
) -> int:
    db = SessionLocal()
    try:
        conversation = _owned_conversation(db, thread_id, user_id)
        run = (
            db.query(ChatRun)
            .filter(ChatRun.id == run_id, ChatRun.thread_id == thread_id)
            .first()
        )
        if run is None:
            raise ValueError("Run does not exist for this conversation.")
        message = ChatMessage(
            thread_id=thread_id,
            run_id=run_id,
            role="assistant",
            content=content,
            model_id=model_id,
            details={"incomplete": incomplete},
        )
        db.add(message)
        conversation.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(message)
        return message.id
    finally:
        db.close()


def save_memory(
    user_id: str,
    memory: str,
    thread_id: str | None = None,
) -> str:
    if not user_id:
        raise ValueError("A signed-in user ID is required.")
    db = SessionLocal()
    try:
        if thread_id is not None:
            _owned_conversation(db, thread_id, user_id)
        db.add(
            LongTermMemory(
                user_id=user_id,
                thread_id=thread_id,
                memory=memory,
            )
        )
        db.commit()
        return "Memory saved successfully."
    finally:
        db.close()


def search_memory(
    user_id: str,
    query: str = "",
    thread_id: str | None = None,
) -> str:
    if not user_id:
        raise ValueError("A signed-in user ID is required.")
    db = SessionLocal()
    try:
        memories_query = db.query(LongTermMemory).filter(
            LongTermMemory.user_id == user_id
        )
        if thread_id is not None:
            _owned_conversation(db, thread_id, user_id)
            memories_query = memories_query.filter(
                LongTermMemory.thread_id == thread_id
            )
        if query.strip():
            memories_query = memories_query.filter(
                LongTermMemory.memory.ilike(f"%{query.strip()}%")
            )
        memories = (
            memories_query.order_by(LongTermMemory.created_at.desc())
            .limit(20)
            .all()
        )
        if not memories:
            return "No saved memory found."
        return "\n".join(f"- {memory.memory}" for memory in memories)
    finally:
        db.close()
