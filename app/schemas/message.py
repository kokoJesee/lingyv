from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.session import SessionState
from app.schemas.context import ContextManifest
from app.schemas.workflow import WorkflowSnapshot


class SendMessageRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    content: str = Field(min_length=1)
    request_id: UUID = Field(default_factory=uuid4)


class SendMessageResponse(BaseModel):
    session_id: UUID
    state: SessionState
    reply: str
    request_id: UUID
    run_id: UUID
    revision: int
    workflow: WorkflowSnapshot
    context_manifest: ContextManifest | None = None
