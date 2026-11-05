import asyncio
from collections import deque
from dataclasses import dataclass, field
from uuid import UUID

from app.services.wearable_state import WearableState
from app.agent.errors import RuntimeFailure
from app.schemas.events import RuntimeEvent
from app.schemas.draft import Draft
from app.schemas.context import ContextManifest, DraftReference
from app.schemas.session import Session, SessionSummary
from app.schemas.workflow import StopReason, WorkflowSnapshot


@dataclass
class OperationRecord:
    fingerprint: str
    result: dict | None = None
    error: tuple[str, str, int] | None = None
    workflow: WorkflowSnapshot | None = None


@dataclass
class SessionRecord:
    session: Session
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    operations: dict[UUID, OperationRecord] = field(default_factory=dict)
    draft_operations: dict[UUID, OperationRecord] = field(default_factory=dict)
    drafts: dict[UUID, Draft] = field(default_factory=dict)
    events: deque[RuntimeEvent] = field(default_factory=lambda: deque(maxlen=200))
    active_task: asyncio.Task | None = None
    cancel_reason: StopReason | None = None
    lifecycle_pending: int = 0
    turn_sources: dict[int, list[DraftReference]] = field(default_factory=dict)
    last_context: ContextManifest | None = None
    wearables: WearableState = field(default_factory=WearableState)


class SessionService:
    def __init__(self) -> None:
        self._records: dict[UUID, SessionRecord] = {}

    def create(self) -> Session:
        session = Session()
        self._records[session.session_id] = SessionRecord(session=session)
        return session

    def install(self, record: SessionRecord) -> None:
        session_id = record.session.session_id
        if session_id in self._records:
            raise RuntimeFailure("session_already_loaded", "该会话已在当前进程中，未覆盖内存内容。")
        self._records[session_id] = record

    def get(self, session_id: UUID) -> Session | None:
        record = self._records.get(session_id)
        return record.session if record else None

    def require(self, session_id: UUID) -> SessionRecord:
        record = self._records.get(session_id)
        if record is None:
            raise RuntimeFailure("session_not_found", "Session not found", 404)
        return record

    def get_lock(self, session_id: UUID) -> asyncio.Lock:
        return self.require(session_id).lock

    def list(self) -> list[SessionSummary]:
        return [
            SessionSummary(
                session_id=session.session_id, status=session.status,
                state=session.state, phase=session.workflow.phase.value,
                message_count=len(session.messages), revision=session.revision,
                created_at=session.created_at,
            )
            for record in self._records.values()
            for session in [record.session]
        ]


session_service = SessionService()
