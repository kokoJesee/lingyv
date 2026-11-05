from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field
from app.schemas.wearable import EvidenceReference


class DraftReference(BaseModel):
    draft_id: UUID
    revision: int


class TurnDependencies(BaseModel):
    wearable_revision: int | None = None
    understanding_refs: list[EvidenceReference] = Field(default_factory=list)
    draft_refs: list[DraftReference] = Field(default_factory=list)


class ContextSource(BaseModel):
    kind: Literal["system_prompt", "history_message", "confirmed_draft", "current_input", "psychology_record"]
    model_message_index: int
    source_id: str | None = None
    evidence_type: str | None = None
    history_index: int | None = None
    role: str | None = None
    draft_id: UUID | None = None
    draft_revision: int | None = None


class ContextManifest(BaseModel):
    builder_version: str = "context_v0.1"
    prompt_version: str
    session_id: UUID
    session_revision: int
    request_id: UUID
    model_message_count: int
    character_count: int
    sources: list[ContextSource] = Field(default_factory=list)
    excluded_history_indices: list[int] = Field(default_factory=list)
    business_dependencies: TurnDependencies | None = None

    def draft_references(self) -> list[DraftReference]:
        return [
            DraftReference(draft_id=source.draft_id, revision=source.draft_revision)
            for source in self.sources if source.kind == "confirmed_draft"
        ]
