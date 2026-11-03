from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class WorkflowPhase(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class StopReason(str, Enum):
    COMPLETED = "completed"
    AWAITING_USER = "awaiting_user"
    USER_CANCELLED = "user_cancelled"
    REQUEST_CANCELLED = "request_cancelled"
    SESSION_ENDED = "session_ended"
    HISTORY_CLEARED = "history_cleared"
    MODEL_ERROR = "model_error"
    OUTPUT_INVALID = "output_invalid"
    CONTEXT_LIMIT = "context_limit"
    STEP_LIMIT = "step_limit"
    INTERNAL_ERROR = "internal_error"
    RESTORE_INTERRUPTED = "restore_interrupted"


class WorkflowSnapshot(BaseModel):
    run_id: UUID | None = None
    kind: Literal["chat", "demo"] | None = None
    phase: WorkflowPhase = WorkflowPhase.IDLE
    step: str | None = None
    step_count: int = 0
    stop_reason: StopReason | None = None
    confirmation_token: UUID | None = None


class ConfirmWorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: UUID
    confirmation_token: UUID


class CancelWorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: UUID


class DemoDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1)
    items: list[str] = Field(min_length=1)
