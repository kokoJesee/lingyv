from datetime import datetime, timezone
from enum import Enum
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class DraftContent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: Text
    items: list[Text] = Field(min_length=1)


class DraftStatus(str, Enum):
    PROPOSED = "PROPOSED"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


class Draft(BaseModel):
    draft_id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    revision: int = 1
    status: DraftStatus = DraftStatus.PROPOSED
    content: DraftContent
    confirmation_token: UUID | None = Field(default_factory=uuid4)
    confirmed_revision: int | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DraftOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: UUID = Field(default_factory=uuid4)


class CreateDraftRequest(DraftOperation):
    content: DraftContent


class EditDraftRequest(CreateDraftRequest):
    expected_revision: int = Field(ge=1)


class RejectDraftRequest(DraftOperation):
    expected_revision: int = Field(ge=1)


class ConfirmDraftRequest(RejectDraftRequest):
    confirmation_token: UUID
