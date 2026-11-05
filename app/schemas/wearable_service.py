"""Service requests carry no caller-supplied session ownership."""
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, field_validator

from app.schemas.wearable import ContractModel, Text, WearableImportBatch
from app.wearables.metric_registry import get_metric

Scope = Literal['local_analysis', 'model_context']


class WearableOperation(ContractModel):
    operation_id: UUID = Field(default_factory=uuid4)
    expected_revision: int = Field(strict=True, ge=0)


class ImportWearables(WearableOperation):
    batch: WearableImportBatch


class SetWearableConsent(WearableOperation):
    provider: Text
    allowed_metrics: list[Text] = Field(max_length=30)
    scopes: list[Scope] = Field(max_length=2)

    @field_validator('allowed_metrics')
    @classmethod
    def registered_metrics(cls, values):
        for value in values:
            get_metric(value)
        if len(values) != len(set(values)):
            raise ValueError('duplicate metric')
        return values

    @field_validator('scopes')
    @classmethod
    def distinct_scopes(cls, values):
        if len(values) != len(set(values)):
            raise ValueError('duplicate scope')
        return values


class DeleteWearableRecord(WearableOperation):
    record_id: Text
