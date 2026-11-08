"""Executable form of the team's v0.1.0 contract; no default safety route."""
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from app.schemas.wearable import ContractModel

Text = Annotated[str, StringConstraints(min_length=1, pattern=r'\S')]
SourceId = Annotated[str, StringConstraints(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')]
Route = Literal['routine_support', 'clarify_safety', 'safety_support', 'urgent_real_world_help']


class SourceText(ContractModel):
    source_id: SourceId
    content: Text


class HistorySource(SourceText):
    role: Literal['user', 'assistant']


class RecordSource(SourceText):
    evidence_type: Literal['user_report', 'device_observation', 'confirmed_memory', 'hypothesis']


class PsychologyRequest(ContractModel):
    schema_version: Literal['0.1.0']
    session_id: UUID
    request_id: UUID
    session_revision: int = Field(strict=True, ge=0)
    current_message: SourceText
    history: list[HistorySource]
    records: list[RecordSource]

    @model_validator(mode='after')
    def unique_sources(self):
        ids = [self.current_message.source_id] + [s.source_id for s in self.history+self.records]
        if len(set(ids)) != len(ids):
            raise ValueError('source IDs must be unique within a request')
        return self


class PsychologyResult(ContractModel):
    route: Route
    evidence_refs: list[SourceId] = Field(min_length=1)
    uncertainties: list[Text]
    reply: Text
    suggested_next_step: Text | None
    memory_update: None
    follow_up_offer: Text | None

    @model_validator(mode='after')
    def distinct_references(self):
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError('duplicate evidence references')
        return self


class PsychologyResponse(ContractModel):
    schema_version: Literal['0.1.0']
    session_id: UUID
    request_id: UUID
    session_revision: int = Field(strict=True, ge=0)
    result: PsychologyResult
