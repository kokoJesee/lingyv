"""Canvas contracts: a versioned, user-confirmed understanding of one experience.

Five lanes (facts / interpretation / emotion / needs / unknown) plus provenance.
Only ``status == "confirmed"`` revisions may be injected into a model context or
referenced by records, cards and memories.
"""
from __future__ import annotations

import re
from datetime import datetime, time
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

CanvasLane = Literal["facts", "interpretation", "emotion", "needs", "unknown"]
CANVAS_LANES: tuple[CanvasLane, ...] = ("facts", "interpretation", "emotion", "needs", "unknown")
LANE_LABELS: dict[str, str] = {
    "facts": "事实",
    "interpretation": "我如何理解",
    "emotion": "感受",
    "needs": "我在意什么",
    "unknown": "还有哪些不知道",
}
EntrySource = Literal["user", "inferred"]
CanvasStatus = Literal["draft", "confirmed", "deleted"]
RecordStatus = Literal["saved", "superseded", "deleted"]
SensitivityLevel = Literal["none", "notice"]
DAY_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MONTH_PATTERN = re.compile(r"^\d{4}-\d{2}$")
MAX_ENTRY_CHARS = 200
MAX_NOTE_CHARS = 200


def new_entry_id() -> str:
    return "e_" + uuid4().hex[:8]


def new_canvas_id() -> str:
    return "cv_" + uuid4().hex[:12]


def new_record_id() -> str:
    return "rec_" + uuid4().hex[:12]


def empty_lanes() -> dict[str, list["CanvasEntry"]]:
    return {lane: [] for lane in CANVAS_LANES}


class Sensitivity(BaseModel):
    level: SensitivityLevel = "none"
    categories: list[str] = Field(default_factory=list)

    def hit(self) -> bool:
        return self.level == "notice"


class CanvasEntry(BaseModel):
    entry_id: str = Field(default_factory=new_entry_id)
    text: str = Field(min_length=1, max_length=MAX_ENTRY_CHARS)
    source: EntrySource = "user"
    confirmed: bool = True
    origin: dict | None = None
    risk_tags: list[str] = Field(default_factory=list)
    lang: str = "zh"
    embedding_ref: str | None = None

    @field_validator("text")
    @classmethod
    def _strip(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("entry text must not be blank")
        return value.strip()


class Canvas(BaseModel):
    canvas_id: str = Field(default_factory=new_canvas_id)
    owner_id: str = "local"
    schema_version: str = "canvas.v1"
    origin_session_id: str
    day: str
    status: CanvasStatus = "draft"
    revision: int = 1
    lanes: dict[str, list[CanvasEntry]] = Field(default_factory=empty_lanes)
    note: str | None = Field(default=None, max_length=MAX_NOTE_CHARS)
    source: Literal["dialog", "manual", "import"] = "manual"
    persona_id: str | None = None
    draft_profile: str | None = None
    prompt_version: str | None = None
    model: str | None = None
    sensitivity: Sensitivity = Field(default_factory=Sensitivity)
    enc_key_id: str = "k1"
    merged_from: list[str] = Field(default_factory=list)
    export_version: int = 1
    confirmed_revision: int | None = None
    confirmed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None

    @field_validator("lanes")
    @classmethod
    def _fill_lanes(cls, value: dict) -> dict:
        return {lane: list(value.get(lane, [])) for lane in CANVAS_LANES}

    def is_confirmed(self) -> bool:
        return self.status == "confirmed" and self.confirmed_revision == self.revision

    def is_empty(self) -> bool:
        return not any(entries for entries in self.lanes.values())

    def total_chars(self) -> int:
        return sum(len(entry.text) for entries in self.lanes.values() for entry in entries)


class CanvasEntryInput(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_ENTRY_CHARS)
    source: EntrySource = "user"
    origin: dict | None = None


class CreateCanvasRequest(BaseModel):
    day: str | None = None
    lanes: dict[str, list[CanvasEntryInput]] = Field(default_factory=dict)
    note: str | None = Field(default=None, max_length=MAX_NOTE_CHARS)
    source: Literal["dialog", "manual", "import"] = "manual"
    persona_id: str | None = None
    draft_profile: str | None = None
    prompt_version: str | None = None
    model: str | None = None

    @field_validator("day")
    @classmethod
    def _check_day(cls, value: str | None) -> str | None:
        if value is not None and not DAY_PATTERN.match(value):
            raise ValueError("day must be YYYY-MM-DD")
        return value

    @field_validator("lanes")
    @classmethod
    def _check_lanes(cls, value: dict) -> dict:
        unknown = set(value) - set(CANVAS_LANES)
        if unknown:
            raise ValueError(f"unknown lanes: {sorted(unknown)}")
        return value


class CanvasOp(BaseModel):
    op: Literal["add", "update", "delete"]
    lane: CanvasLane
    entry_id: str | None = None
    text: str | None = Field(default=None, max_length=MAX_ENTRY_CHARS)
    source: EntrySource = "user"


class PatchCanvasRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    request_id: str | None = None
    ops: list[CanvasOp] = Field(min_length=1)


class ConfirmCanvasRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    request_id: str | None = None
    observed_at: str | None = None

    @field_validator("observed_at")
    @classmethod
    def _check_observed(cls, value: str | None) -> str | None:
        if value is not None and not DAY_PATTERN.match(value):
            raise ValueError("observed_at must be YYYY-MM-DD")
        return value


class CanvasSnapshot(BaseModel):
    canvas_id: str
    revision: int
    day: str
    lanes: dict[str, list[CanvasEntry]]
    note: str | None = None


class CanvasReference(BaseModel):
    canvas_id: str
    revision: int


class Record(BaseModel):
    record_id: str = Field(default_factory=new_record_id)
    owner_id: str = "local"
    canvas_id: str
    canvas_revision: int
    version: int = 1
    day: str
    observed_at: str
    recorded_at: datetime
    status: RecordStatus = "saved"
    snapshot: CanvasSnapshot
    cards: list[dict] = Field(default_factory=list)
    note: str | None = None
    summary: str | None = None
    tags: list[str] = Field(default_factory=list)
    sensitivity: Sensitivity = Field(default_factory=Sensitivity)
    superseded_by: str | None = None
    export_version: int = 1
    enc_key_id: str = "k1"
    created_at: datetime
    deleted_at: datetime | None = None


class RecordPreview(BaseModel):
    record_id: str
    day: str
    title: str
    summary: str
    card_count: int
    sensitivity: Sensitivity = Field(default_factory=Sensitivity)


class DayView(BaseModel):
    day: str
    records: list[RecordPreview] = Field(default_factory=list)


class ConfirmResult(BaseModel):
    canvas: Canvas
    record: Record


def canvas_title(canvas: Canvas) -> str:
    """Preview title: user note first, otherwise the first facts entry."""
    if canvas.note and canvas.note.strip():
        return canvas.note.strip()[:24]
    for lane in CANVAS_LANES:
        for entry in canvas.lanes.get(lane, []):
            text = entry.text.strip()
            if text:
                return text[:24]
    return "（空白的一天）"


def canvas_summary(canvas: Canvas) -> str:
    """Preview summary: one line, prefers interpretation then facts."""
    parts: list[str] = []
    for lane in ("interpretation", "facts", "emotion", "needs"):
        entries = canvas.lanes.get(lane, [])
        if entries:
            parts.append(entries[0].text.strip())
    text = "；".join(parts)
    return text[:60] if text else "（还没有写内容）"


def day_start(day: str) -> time:
    """Reserved for future timezone-aware bucketing; M1 stores plain dates."""
    return time.min
