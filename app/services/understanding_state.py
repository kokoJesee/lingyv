"""Memory-only state; no operation cache contains interpretation/report text."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from app.schemas.understanding import Understanding


@dataclass
class UserReport:
    report_id: str
    content: str
    revision: int = 1


@dataclass
class UnderstandingEntry:
    value: Understanding
    token: UUID | None
    wearable_revision: int | None = None


@dataclass
class UnderstandingState:
    entries: dict[str, UnderstandingEntry] = field(default_factory=dict)
    reports: dict[str, UserReport] = field(default_factory=dict)
    operations: dict = field(default_factory=dict)

    def invalidate(self, *, source_kind=None, source_id=None, wearable=False):
        """Invalidate direct references and all descendants, retaining only current revision."""
        changed = set()
        while True:
            newly = []
            for identity, entry in self.entries.items():
                value = entry.value
                if value.status not in ('proposed', 'confirmed'):
                    continue
                direct = wearable and entry.wearable_revision is not None
                for ref in value.evidence_refs:
                    direct |= (ref.kind == source_kind and ref.source_id == source_id)
                    direct |= (ref.kind == 'understanding' and ref.source_id in changed)
                if direct:
                    now = datetime.now(timezone.utc)
                    entry.value = Understanding.model_validate({**value.model_dump(),
                        'revision': value.revision+1, 'status': 'invalidated', 'updated_at': now,
                        'confirmed_revision': None, 'confirmed_at': None, 'invalidation_reason': 'source_changed'})
                    entry.token = None
                    newly.append(identity)
            if not newly: break
            changed.update(newly)
        return bool(changed)

    def clear(self):
        self.entries.clear()
        self.reports.clear()
        for operation in self.operations.values():
            operation.result = None
            operation.error = ('request_cleared', '理解或自述已清空。', 409)
