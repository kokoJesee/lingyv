"""Versioned data-only snapshot; execution handles and credentials are never stored."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator
from typing import Annotated

from app.schemas.context import DraftReference
from app.schemas.draft import DraftContent, DraftStatus
from app.schemas.session import Message, SessionState, SessionStatus
from app.schemas.workflow import WorkflowPhase
from app.schemas.business_archive import SavedBusinessState

SNAPSHOT_VERSION = 1
MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024
MAX_ARCHIVES = 1000


class DataModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class SaveArchiveRequest(DataModel):
    expected_revision: int = Field(ge=0, strict=True)
    expected_archive_version: int | None = Field(default=None, ge=1, strict=True)
    save_business_data: bool = Field(default=False, strict=True)


class ArchiveInfo(DataModel):
    archive_id: UUID
    archive_version: int = Field(ge=1)
    snapshot_version: Literal[1, 2] = 1
    session_revision: int = Field(ge=0)
    message_count: int = Field(ge=0)
    draft_count: int = Field(ge=0)
    status: SessionStatus
    saved_at: datetime


class SavedMessage(Message):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=MAX_SNAPSHOT_BYTES)


class SavedSession(DataModel):
    session_id: UUID
    state: SessionState
    status: SessionStatus
    revision: int = Field(ge=0)
    messages: list[SavedMessage] = Field(max_length=2000)
    created_at: datetime
    workflow_phase: WorkflowPhase
    workflow_kind: Literal["chat", "demo"] | None


class SavedDraft(DataModel):
    draft_id: UUID
    session_id: UUID
    revision: int = Field(ge=1)
    status: DraftStatus
    content: DraftContent
    confirmed_revision: int | None = Field(default=None, ge=1)
    created_at: datetime
    updated_at: datetime


class SavedReference(DraftReference):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)


Fingerprint = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class Snapshot(DataModel):
    version: Literal[1, 2] = 1
    session: SavedSession
    drafts: list[SavedDraft] = Field(max_length=1000)
    operations: dict[UUID, Fingerprint]
    draft_operations: dict[UUID, Fingerprint]
    turn_sources: dict[int, list[SavedReference]]
    business: SavedBusinessState | None = None

    @field_validator("turn_sources", mode="before")
    @classmethod
    def canonical_indices(cls, value):
        if not isinstance(value, dict) or any(
            not (type(key) is int or (isinstance(key, str) and key.isascii() and key.isdecimal() and str(int(key)) == key))
            for key in value
        ):
            raise ValueError("noncanonical dependency indices")
        return value

    @model_validator(mode="after")
    def consistent(self):
        if (self.version == 2) != (self.business is not None):
            raise ValueError('business payload requires snapshot v2')
        session = self.session
        if session.created_at.tzinfo is None:
            raise ValueError("timestamp must include timezone")
        if len(session.messages) % 2 or any(
            message.role != ("user" if index % 2 == 0 else "assistant")
            for index, message in enumerate(session.messages)
        ):
            raise ValueError("only completed conversation turns can be saved")
        if len(self.operations) + len(self.draft_operations) > 10000:
            raise ValueError("too many operation records")
        drafts = {draft.draft_id: draft for draft in self.drafts}
        if len(drafts) != len(self.drafts):
            raise ValueError("duplicate draft id")
        for draft in self.drafts:
            if draft.session_id != session.session_id:
                raise ValueError("draft belongs to another session")
            if draft.created_at.tzinfo is None or draft.updated_at.tzinfo is None:
                raise ValueError("timestamp must include timezone")
            if draft.status == DraftStatus.CONFIRMED:
                if draft.confirmed_revision != draft.revision:
                    raise ValueError("confirmation revision mismatch")
            elif draft.confirmed_revision is not None:
                raise ValueError("unexpected confirmed revision")
        if set(self.turn_sources) != set(range(1, len(session.messages), 2)):
            raise ValueError("every assistant message must retain its dependency record")
        for references in self.turn_sources.values():
            if len(references) > 1000:
                raise ValueError("too many references")
            for ref in references:
                if ref.draft_id not in drafts or ref.revision > drafts[ref.draft_id].revision:
                    raise ValueError("invalid draft dependency")
        if self.business is not None:
            if len(self.operations) + len(self.draft_operations) + len(self.business.wearable_operations) + len(self.business.understanding_operations) > 10000:
                raise ValueError('too many business operation records')
            if set(self.business.turn_sources) != set(range(1, len(session.messages), 2)):
                raise ValueError('missing business turn dependency')
            for dependencies in self.business.turn_sources.values():
                for ref in dependencies.draft_refs:
                    if ref.revision < 1 or ref.draft_id not in drafts or ref.revision > drafts[ref.draft_id].revision:
                        raise ValueError('invalid business draft dependency')
        return self
