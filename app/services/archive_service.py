import asyncio
from uuid import UUID, uuid4

from pydantic import ValidationError

from app.agent.errors import RuntimeFailure
from app.agent.events import emit
from app.schemas.archive import ArchiveInfo, SaveArchiveRequest, SavedSession, Snapshot
from app.schemas.draft import Draft, DraftStatus
from app.schemas.session import Session
from app.schemas.workflow import StopReason, WorkflowPhase, WorkflowSnapshot
from app.schemas.context import TurnDependencies
from app.services.understanding_state import UnderstandingEntry, UserReport
from app.services.archive_store import SQLiteArchiveStore
from app.services.session_service import OperationRecord, SessionRecord, SessionService


async def _disk_call(function, *args):
    # Complete an authorized transaction before releasing a held session lock.
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()  # Consume a failure; the caller still receives cancellation.
        raise


class ArchiveService:
    def __init__(self, sessions: SessionService, store: SQLiteArchiveStore):
        self.sessions = sessions
        self.store = store

    async def list(self) -> list[ArchiveInfo]:
        return await _disk_call(self.store.list)

    @staticmethod
    def _saveable(record):
        if (record.lifecycle_pending or record.active_task is not None or
                record.session.workflow.phase == WorkflowPhase.RUNNING):
            raise RuntimeFailure("archive_session_busy", "当前会话正在运行或清空，请完成后再保存。")

    async def save(self, session_id: UUID, request: SaveArchiveRequest) -> ArchiveInfo:
        record = self.sessions.require(session_id)
        self._saveable(record)
        async with record.lock:
            self._saveable(record)
            if record.session.revision != request.expected_revision:
                raise RuntimeFailure("stale_revision", "会话版本已变化，请查看当前状态后再保存。")
            session = record.session
            has_business = bool(record.support_mode or record.wearables.records or record.wearables.consents
                or record.wearables.operations or record.understandings.entries
                or record.understandings.reports or record.understandings.operations)
            if has_business and not request.save_business_data:
                raise RuntimeFailure('archive_business_consent_required', '保存包含设备、自述或理解的会话需要单独确认本地保存。')
            try:
                business = None
                if has_business:
                    business = {
                        'support_mode': record.support_mode, 'support_route': record.support_route,
                        'wearable_revision': record.wearables.revision,
                        'records': list(record.wearables.records.values()),
                        'retired_ids': sorted(record.wearables.retired_ids),
                        'consent_versions': {c.consent_id: c.revision for c in record.wearables.consents.values()},
                        'reports': [vars(r) for r in record.understandings.reports.values()],
                        'understandings': [{'value': e.value, 'wearable_revision': e.wearable_revision}
                                           for e in record.understandings.entries.values()],
                        'wearable_operations': {k: v.fingerprint for k, v in record.wearables.operations.items()},
                        'understanding_operations': {k: v.fingerprint for k, v in record.understandings.operations.items()},
                        'turn_sources': {i: record.business_turn_sources.get(i, TurnDependencies(
                            draft_refs=record.turn_sources.get(i, []))).model_dump()
                            for i in range(1, len(session.messages), 2)},
                    }
                saved_session = SavedSession.model_validate({
                    **session.model_dump(exclude={"workflow"}),
                    "workflow_phase": session.workflow.phase,
                    "workflow_kind": session.workflow.kind,
                })
                snapshot = Snapshot.model_validate({
                    'version': 2 if business is not None else 1, 'business': business,
                    "session": saved_session,
                    "drafts": [draft.model_dump(exclude={"confirmation_token"})
                               for draft in record.drafts.values()],
                    "operations": {key: item.fingerprint for key, item in record.operations.items()},
                    "draft_operations": {key: item.fingerprint for key, item in record.draft_operations.items()},
                    "turn_sources": {key: [item.model_dump() for item in items]
                                     for key, items in record.turn_sources.items()},
                })
            except ValidationError:
                raise RuntimeFailure("archive_invalid", "当前会话不满足快照一致性要求，未保存。") from None
            info = await _disk_call(self.store.save, snapshot, request.expected_archive_version)
            emit(record, "archive_saved")
            return info

    async def restore(self, archive_id: UUID) -> Session:
        if self.sessions.get(archive_id) is not None:
            raise RuntimeFailure("session_already_loaded", "该会话已在当前进程中，未覆盖内存内容。")
        _, snapshot = await _disk_call(self.store.load, archive_id)
        saved = snapshot.session
        interrupted = saved.workflow_phase in (WorkflowPhase.RUNNING, WorkflowPhase.WAITING_CONFIRMATION)
        workflow = WorkflowSnapshot(
            phase=WorkflowPhase.CANCELLED if interrupted else WorkflowPhase.IDLE,
            stop_reason=StopReason.RESTORE_INTERRUPTED if interrupted else None,
        )
        session = Session.model_validate({
            **saved.model_dump(exclude={"workflow_phase", "workflow_kind"}),
            "workflow": workflow, "revision": saved.revision + 1,
        })
        record = SessionRecord(session=session)
        for saved_draft in snapshot.drafts:
            draft = Draft.model_validate(saved_draft.model_dump())
            draft.confirmation_token = uuid4() if draft.status == DraftStatus.PROPOSED else None
            record.drafts[draft.draft_id] = draft
        # Old IDs prevent a second execution, but cached responses/confirmations never return.
        for source, target in ((snapshot.operations, record.operations),
                               (snapshot.draft_operations, record.draft_operations)):
            for operation_id, fingerprint in source.items():
                target[operation_id] = OperationRecord(
                    fingerprint=fingerprint,
                    error=("request_restored", "该编号来自已恢复存档，不会重新执行；新操作请使用新编号。", 409),
                )
        record.turn_sources = {
            key: [item.model_copy(deep=True) for item in items]
            for key, items in snapshot.turn_sources.items()
        }
        if snapshot.business is not None:
            saved_business = snapshot.business
            record.support_mode = saved_business.support_mode or bool(session.messages)
            record.support_route = saved_business.support_route
            record.wearables.revision = saved_business.wearable_revision + 1
            record.wearables.records = {r.record_id: r.model_copy(deep=True) for r in saved_business.records}
            record.wearables.retired_ids = set(saved_business.retired_ids)
            # Restoring saved data grants no local-analysis or model-context permission.
            record.understandings.reports = {r.report_id: UserReport(**r.model_dump()) for r in saved_business.reports}
            for entry in saved_business.understandings:
                value = entry.value.model_copy(deep=True)
                record.understandings.entries[value.understanding_id] = UnderstandingEntry(
                    value, uuid4() if value.status == 'proposed' else None, entry.wearable_revision)
            record.understandings.invalidate(wearable=True)
            for source, target in ((saved_business.wearable_operations, record.wearables.operations),
                                   (saved_business.understanding_operations, record.understandings.operations)):
                for identity, fingerprint in source.items():
                    target[identity] = OperationRecord(fingerprint=fingerprint,
                        error=('request_restored', '该编号来自已恢复存档；新操作请使用新编号。', 409))
            record.business_turn_sources = {i: TurnDependencies.model_validate(d.model_dump())
                                           for i, d in saved_business.turn_sources.items()}
        # Source manifests describe a past call; a new manifest appears after the next successful turn.
        self.sessions.install(record)
        emit(record, "archive_restored")
        return session.model_copy(deep=True)

    async def delete(self, archive_id: UUID, expected_version: int) -> None:
        # Removing disk data intentionally leaves an already-loaded memory session alone.
        await _disk_call(self.store.delete, archive_id, expected_version)
