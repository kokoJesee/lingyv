"""Long-term memory contracts: L2 notes only; L1 records arrive with the canvas work."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

MemoryKind = Literal["fact", "preference", "context", "strategy"]
MemoryStatus = Literal["proposed", "confirmed", "expired", "superseded", "deleted"]
MemorySourceType = Literal["user_stated", "manual", "canvas_confirmed", "archive_confirmed"]

MAX_SUMMARY_CHARS = 50
MAX_TEXT_CHARS = 400
MAX_IMPORTANCE = 5


class MemorySource(BaseModel):
    type: MemorySourceType = "user_stated"
    session_id: str | None = None
    message_index: int | None = None


class Memory(BaseModel):
    memory_id: str
    owner_id: str = "local"
    kind: MemoryKind
    summary: str
    text: str
    importance: int = Field(default=3, ge=1, le=MAX_IMPORTANCE)
    source: MemorySource = Field(default_factory=MemorySource)
    status: MemoryStatus = "proposed"
    revision: int = 1
    valid_from: datetime
    valid_to: datetime | None = None
    review_at: datetime | None = None
    evidence_count: int = 0
    last_used_at: datetime | None = None
    use_count: int = 0
    derived_from_record_id: str | None = None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None

    def is_active(self, now: datetime) -> bool:
        return (
            self.status == "confirmed"
            and self.deleted_at is None
            and (self.valid_to is None or self.valid_to > now)
        )


class MemoryReference(BaseModel):
    memory_id: str
    revision: int


class MemoryCreateRequest(BaseModel):
    kind: MemoryKind
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    summary: str | None = Field(default=None, max_length=MAX_SUMMARY_CHARS)
    importance: int = Field(default=3, ge=1, le=MAX_IMPORTANCE)
    session_id: str | None = None
    message_index: int | None = None
    derived_from_record_id: str | None = None

    @field_validator("text")
    @classmethod
    def _no_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value.strip()


class MemoryUpdateRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    text: str | None = Field(default=None, min_length=1, max_length=MAX_TEXT_CHARS)
    summary: str | None = Field(default=None, max_length=MAX_SUMMARY_CHARS)
    kind: MemoryKind | None = None
    importance: int | None = Field(default=None, ge=1, le=MAX_IMPORTANCE)


class MemoryConfirmRequest(BaseModel):
    expected_revision: int = Field(ge=1)


class MemorySelection(BaseModel):
    memories: list[Memory] = Field(default_factory=list)
    excluded_memory_ids: list[str] = Field(default_factory=list)

    def references(self) -> list[MemoryReference]:
        return [MemoryReference(memory_id=m.memory_id, revision=m.revision) for m in self.memories]


def summarise(text: str, limit: int = MAX_SUMMARY_CHARS) -> str:
    """One-line summary used in the model context; never invents content."""
    first = text.strip().splitlines()[0] if text.strip() else ""
    for stop in ("。", "！", "？", "；", ".", "!", "?", ";"):
        if stop in first:
            first = first.split(stop)[0] + stop
            break
    return first[:limit]
