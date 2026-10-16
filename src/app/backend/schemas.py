from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class UserProfile(ApiModel):
    id: str
    email: str
    name: str
    picture: str | None = None
    default_model_id: str = Field(default="gpt-4o-mini", alias="defaultModelId")
    preferences: dict[str, Any] = Field(default_factory=dict)


class ModelOption(ApiModel):
    id: str
    name: str
    description: str = ""


class AttachmentRef(ApiModel):
    id: str
    filename: str
    content_type: str = Field(alias="contentType")
    size_bytes: int = Field(alias="sizeBytes", ge=0)
    indexing_status: Literal["pending", "indexed", "failed"] = Field(
        default="indexed",
        alias="indexingStatus",
    )


class ConversationSummary(ApiModel):
    id: str
    title: str
    model_id: str = Field(alias="modelId")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class ConversationCreateRequest(ApiModel):
    title: str = Field(default="New Chat", max_length=120)
    model_id: str = Field(default="gpt-4o-mini", alias="modelId")


class ChatMessageResponse(ApiModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    attachments: list[str] = Field(default_factory=list)
    incomplete: bool = False
    created_at: datetime = Field(alias="createdAt")


class RunSummary(ApiModel):
    id: str
    status: Literal["queued", "running", "waiting_for_input", "completed", "cancelled", "failed"]
    model_id: str = Field(alias="modelId")


class ConversationResponse(ConversationSummary):
    messages: list[ChatMessageResponse] = Field(default_factory=list)
    runs: list[RunSummary] = Field(default_factory=list)


class ChatRunRequest(ApiModel):
    model_id: str = Field(alias="modelId")
    content: str = Field(default="", max_length=32_000)
    attachments: list[str] = Field(default_factory=list, max_length=20)


class ChatRunCreated(ApiModel):
    run_id: str = Field(alias="runId")
    conversation_id: str = Field(alias="conversationId")
    user_message_id: str = Field(alias="userMessageId")



class HumanInputOption(ApiModel):
    label: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1, max_length=200)


class HumanInputField(ApiModel):
    id: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=64)
    required: bool = False
    description: str | None = Field(default=None, max_length=1000)
    placeholder: str | None = Field(default=None, max_length=500)
    constraints: dict[str, Any] = Field(default_factory=dict)
    options: list[HumanInputOption] = Field(default_factory=list, max_length=100)


class HumanInputAction(ApiModel):
    id: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)


class HumanInputRequest(ApiModel):
    request_id: str = Field(alias="requestId", min_length=1, max_length=100)
    run_id: str = Field(alias="runId", min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    fields: list[HumanInputField] = Field(max_length=40)
    actions: list[HumanInputAction] = Field(min_length=1, max_length=20)


class HumanInputResponse(ApiModel):
    request_id: str = Field(alias="requestId")
    run_id: str = Field(alias="runId")
    action_id: str = Field(alias="actionId")
    values: dict[str, Any]


class RunEvent(ApiModel):
    type: Literal[
        "status",
        "assistant_delta",
        "tool_call",
        "tool_result",
        "human_input_request",
        "completed",
        "cancelled",
        "error",
    ]
    conversation_id: str = Field(alias="conversationId")
    run_id: str = Field(alias="runId")
    sequence: int = Field(ge=0)
    correlation_id: str | None = Field(default=None, alias="correlationId")
    message_id: str | None = Field(default=None, alias="messageId")
    payload: dict[str, Any] = Field(default_factory=dict)


RunStreamEvent = RunEvent


class HumanInputSubmitRequest(HumanInputResponse):
    pass


class HumanInputSubmitResult(ApiModel):
    request_id: str = Field(alias="requestId")
    run_id: str = Field(alias="runId")
    status: Literal["resuming", "cancelled"]
