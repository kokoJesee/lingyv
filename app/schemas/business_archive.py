"""Data-only business snapshot. Use permissions and control tokens are not restored."""
from typing import Annotated, Literal
from uuid import UUID
from pydantic import Field, StringConstraints, field_validator, model_validator
from app.schemas.wearable import (
    ContractModel, WearableDailyRecord, WearableIntervalRecord, SleepEpisode, WorkoutEvent,
    EvidenceReference,
)
from app.schemas.understanding import Understanding
from app.schemas.psychology import Route
from app.schemas.context import TurnDependencies

Fingerprint = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{64}$')]
PositiveRevision = Annotated[int, Field(strict=True, ge=1)]
Record = WearableDailyRecord | WearableIntervalRecord | SleepEpisode | WorkoutEvent


class SavedReport(ContractModel):
    report_id: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=2000)
    revision: PositiveRevision


class SavedUnderstanding(ContractModel):
    value: Understanding
    wearable_revision: int | None = Field(default=None, strict=True, ge=0)


class SavedTurnDependencies(TurnDependencies):
    model_config = {'extra': 'forbid'}
    wearable_revision: int | None = Field(default=None, strict=True, ge=0)


class SavedBusinessState(ContractModel):
    support_mode: bool
    support_route: Route | None
    wearable_revision: int = Field(strict=True, ge=0)
    records: list[Record] = Field(max_length=2000)
    retired_ids: list[str] = Field(max_length=10000)
    # Historical version metadata, not permissions to read devices after restore.
    consent_versions: dict[UUID, PositiveRevision]
    reports: list[SavedReport] = Field(max_length=10000)
    understandings: list[SavedUnderstanding] = Field(max_length=10000)
    wearable_operations: dict[UUID, Fingerprint]
    understanding_operations: dict[UUID, Fingerprint]
    turn_sources: dict[int, SavedTurnDependencies]

    @field_validator('turn_sources', mode='before')
    @classmethod
    def canonical_indices(cls, value):
        if not isinstance(value, dict) or any(not (type(k) is int or (
            isinstance(k, str) and k.isascii() and k.isdecimal() and str(int(k)) == k)) for k in value):
            raise ValueError('noncanonical business dependency index')
        return value

    @model_validator(mode='after')
    def consistent(self):
        if self.support_route is not None and not self.support_mode:
            raise ValueError('route without support mode')
        if not self.support_mode and any(d.wearable_revision is not None or d.understanding_refs
                                         for d in self.turn_sources.values()):
            raise ValueError('business history cannot downgrade to ordinary chat')
        records = {r.record_id: r for r in self.records}
        reports = {r.report_id: r for r in self.reports}
        entries = {e.value.understanding_id: e for e in self.understandings}
        if len(records) != len(self.records) or len(reports) != len(self.reports) or len(entries) != len(self.understandings):
            raise ValueError('duplicate business identity')
        if len({(r.provider, r.provider_record_id) for r in self.records}) != len(records):
            raise ValueError('duplicate provider identity')
        if len(set(self.retired_ids)) != len(self.retired_ids) or set(self.retired_ids).intersection(records):
            raise ValueError('invalid retired records')
        if len(self.consent_versions) > 2000:
            raise ValueError('too many consent versions')

        def check_entry(identity, trail=(), budget=None):
            budget = [1000] if budget is None else budget
            budget[0] -= 1
            if identity in trail or len(trail) >= 64 or budget[0] < 0:
                raise ValueError('understanding dependency cycle or budget')
            entry = entries[identity]
            device = False
            for ref in entry.value.evidence_refs:
                if ref.kind == 'user_report':
                    source = reports.get(ref.source_id)
                    valid = source is not None and source.revision == ref.revision
                elif ref.kind == 'understanding':
                    parent = entries.get(ref.source_id)
                    valid = parent is not None and parent.value.status == 'confirmed' and parent.value.revision == ref.revision
                    if valid: device |= check_entry(ref.source_id, (*trail, identity), budget)
                elif ref.kind == 'wearable_record':
                    source = records.get(ref.source_id)
                    valid = source is not None and source.record_revision == ref.revision
                    device = True
                else:
                    valid = self.consent_versions.get(UUID(ref.source_id)) == ref.revision
                    device = True
                if not valid:
                    raise ValueError('invalid active understanding source')
            if device != (entry.wearable_revision is not None):
                raise ValueError('device dependency marker mismatch')
            if device and entry.wearable_revision != self.wearable_revision:
                raise ValueError('active understanding has stale device version')
            return device

        for identity, entry in entries.items():
            if entry.wearable_revision is not None and entry.wearable_revision > self.wearable_revision:
                raise ValueError('future understanding device version')
            if entry.value.status in ('proposed', 'confirmed'):
                check_entry(identity)
        for deps in self.turn_sources.values():
            if deps.wearable_revision is not None and deps.wearable_revision > self.wearable_revision:
                raise ValueError('future turn device version')
            for ref in deps.understanding_refs:
                if ref.kind != 'understanding' or ref.source_id not in entries or ref.revision > entries[ref.source_id].value.revision:
                    raise ValueError('invalid historical understanding dependency')
        return self
