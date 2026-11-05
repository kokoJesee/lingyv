"""Memory-only state owned by SessionRecord, never a process-global data store."""
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from app.schemas.wearable import RecordBase


@dataclass
class Consent:
    consent_id: UUID
    revision: int
    allowed_metrics: frozenset[str]
    scopes: frozenset[str]
    updated_at: datetime


@dataclass
class WearableState:
    revision: int = 0
    records: dict[str, RecordBase] = field(default_factory=dict)
    retired_ids: set[str] = field(default_factory=set)
    consents: dict[str, Consent] = field(default_factory=dict)
    # Fingerprint + small receipt/error only: never cache personal record bodies.
    operations: dict = field(default_factory=dict)

    def clear(self):
        self.records.clear()
        self.consents.clear()
        self.retired_ids.clear()
        self.revision += 1
        for operation in self.operations.values():
            operation.result = None
            operation.error = ('request_cleared', '设备操作对应的内容已清空。', 409)
