from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal
from app.schemas.psychology import PsychologyResponse

from app.schemas.session import SessionState
from app.schemas.context import ContextManifest
from app.schemas.workflow import WorkflowSnapshot


class CorrectionTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    understanding_id: str = Field(min_length=1)
    expected_revision: int = Field(strict=True, ge=1)


class SendMessageRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    content: str = Field(min_length=1)
    request_id: UUID = Field(default_factory=uuid4)
    correction_of: CorrectionTarget | None = None

    @model_validator(mode="after")
    def correction_size(self):
        if self.correction_of is not None and len(self.content) > 2000:
            raise ValueError("correction must fit the understanding text limit")
        return self


class SendMessageResponse(BaseModel):
    session_id: UUID
    state: SessionState
    reply: str
    request_id: UUID
    run_id: UUID
    revision: int
    workflow: WorkflowSnapshot
    context_manifest: ContextManifest | None = None
    psychology: PsychologyResponse | None = None
    review_status: Literal["unreviewed"] | None = None
