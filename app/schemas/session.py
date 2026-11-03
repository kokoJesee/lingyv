from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.schemas.workflow import WorkflowSnapshot


class SessionState(str, Enum):
    INITIAL = "INITIAL"
    UNDERSTANDING = "UNDERSTANDING"
    CLARIFICATION = "CLARIFICATION"


class SessionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    ENDED = "ENDED"


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class Session(BaseModel):
    session_id: UUID = Field(default_factory=uuid4)
    state: SessionState = SessionState.INITIAL
    status: SessionStatus = SessionStatus.ACTIVE
    revision: int = 0
    messages: list[Message] = Field(default_factory=list)
    workflow: WorkflowSnapshot = Field(default_factory=WorkflowSnapshot)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SessionSummary(BaseModel):
    session_id: UUID
    status: SessionStatus
    state: SessionState
    phase: str
    message_count: int
    revision: int
    created_at: datetime
