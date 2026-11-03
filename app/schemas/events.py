from datetime import datetime, timezone
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.schemas.workflow import StopReason, WorkflowPhase


class RuntimeEvent(BaseModel):
    event_id: UUID = Field(default_factory=uuid4)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event: str
    request_id: UUID | None = None
    run_id: UUID | None = None
    phase: WorkflowPhase
    step: str | None = None
    stop_reason: StopReason | None = None
    elapsed_ms: float | None = None
    message_count: int
