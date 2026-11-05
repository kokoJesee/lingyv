"""Atomic memory-only ingestion and purpose-specific data access.

Callers must authenticate session ownership before calling this local service.
Snapshots are data, not capabilities: validate their revision before downstream commit.
"""
import hashlib
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

from app.agent.errors import RuntimeFailure
from app.config.settings import get_settings
from app.schemas.session import SessionStatus
from app.schemas.workflow import StopReason, WorkflowPhase
from app.schemas.wearable_service import ImportWearables, SetWearableConsent, DeleteWearableRecord
from app.services.session_service import SessionService, OperationRecord
from app.services.wearable_state import Consent

MAX_BYTES = 4 * 1024 * 1024
MAX_RECORDS = 2000


@dataclass(frozen=True)
class WearableSnapshot:
    session_id: UUID
    revision: int
    scope: str
    records: tuple


class WearableService:
    def __init__(self, sessions: SessionService, settings=None):
        self.sessions = sessions
        self.settings = settings or get_settings()

    async def import_records(self, session_id, request: ImportWearables):
        return await self._apply(session_id, 'import', request)

    async def set_consent(self, session_id, request: SetWearableConsent):
        """Replace this provider's selection; empty scopes revoke all uses."""
        return await self._apply(session_id, 'consent', request)

    async def delete(self, session_id, request: DeleteWearableRecord):
        return await self._apply(session_id, 'delete', request)

    def revision(self, session_id):
        return self.sessions.require(session_id).wearables.revision

    def consent(self, session_id, provider):
        return deepcopy(self.sessions.require(session_id).wearables.consents.get(provider))

    @staticmethod
    def _check_revision(state, expected):
        if expected != state.revision:
            raise RuntimeFailure('stale_revision', '设备数据或授权已改变，请读取当前版本后重试。')

    @staticmethod
    def _replay(previous, fingerprint):
        if previous.fingerprint != fingerprint:
            raise RuntimeFailure('operation_id_conflict', '同一操作编号不能用于不同内容。')
        if previous.error:
            raise RuntimeFailure(*previous.error)
        return deepcopy(previous.result)

    async def _apply(self, session_id, action, request):
        # Revalidate even if a caller constructed or modified a Pydantic instance.
        request = type(request).model_validate_json(request.model_dump_json())
        payload = request.model_dump_json(exclude={'operation_id'})
        if len(payload.encode('utf-8')) > MAX_BYTES:
            raise RuntimeFailure('wearable_too_large', '导入内容超过 4 MiB。', 413)
        fingerprint = hashlib.sha256((action + payload).encode('utf-8')).hexdigest()
        record = self.sessions.require(session_id)
        state = record.wearables
        previous = state.operations.get(request.operation_id)
        if previous:
            return self._replay(previous, fingerprint)
        self._check_revision(state, request.expected_revision)
        old = state.consents.get(request.provider) if action == 'consent' else None
        privacy_change = action == 'delete' or (old is not None and (
            not old.scopes.issubset(request.scopes) or not old.allowed_metrics.issubset(request.allowed_metrics)))
        if record.lifecycle_pending:
            raise RuntimeFailure('session_busy', '会话生命周期操作进行中。')
        if not privacy_change and (record.lock.locked() or record.session.workflow.phase in (
            WorkflowPhase.RUNNING, WorkflowPhase.WAITING_CONFIRMATION
        )):
            raise RuntimeFailure('session_busy', '运行中不能导入或扩大设备授权。')
        if privacy_change:
            record.lifecycle_pending += 1
            if record.session.workflow.phase == WorkflowPhase.RUNNING and record.active_task:
                record.cancel_reason = record.cancel_reason or StopReason.USER_CANCELLED
                record.active_task.cancel()
        try:
            async with record.lock:
                previous = state.operations.get(request.operation_id)
                if previous:
                    return self._replay(previous, fingerprint)
                expands = action == 'consent' and (old is None or not frozenset(request.scopes).issubset(old.scopes) or not frozenset(request.allowed_metrics).issubset(old.allowed_metrics))
                if record.session.status != SessionStatus.ACTIVE and (not privacy_change or expands):
                    raise RuntimeFailure('session_ended', '已结束会话不能新增设备数据或授权。')
                at_limit = len(record.operations) + len(record.draft_operations) + len(state.operations) >= self.settings.runtime_max_requests_per_session
                if at_limit:
                    # Privacy operations must still be possible after ordinary budget exhaustion.
                    if not privacy_change:
                        raise RuntimeFailure('request_limit_exceeded', '会话操作记录已达上限。')
                operation = OperationRecord(fingerprint=fingerprint)
                if not at_limit:
                    state.operations[request.operation_id] = operation
                try:
                    self._check_revision(state, request.expected_revision)
                    if action == 'import':
                        result, records = self._prepare_import(state, request.batch)
                        changed = bool(result['accepted'])
                        if changed:
                            state.records = records
                    elif action == 'consent':
                        metrics, scopes = frozenset(request.allowed_metrics), frozenset(request.scopes)
                        current = state.consents.get(request.provider)
                        changed = current is None or current.allowed_metrics != metrics or current.scopes != scopes
                        if changed:
                            state.consents[request.provider] = Consent(
                                current.consent_id if current else uuid4(), (current.revision + 1) if current else 1,
                                metrics, scopes, datetime.now(timezone.utc))
                        result = {'changed': changed}
                    else:
                        changed = request.record_id in state.records
                        state.records.pop(request.record_id, None)
                        if changed:
                            state.retired_ids.add(request.record_id)
                        result = {'deleted': int(changed)}
                    if changed:
                        state.revision += 1
                        record.session.revision += 1
                    result['revision'] = state.revision
                    operation.result = result
                    return deepcopy(result)
                except RuntimeFailure as error:
                    operation.error = (error.code, error.message, error.status_code)
                    raise
        finally:
            if privacy_change:
                record.lifecycle_pending -= 1

    @staticmethod
    def _prepare_import(state, batch):
        candidates = deepcopy(state.records)
        keys = {(r.provider, r.provider_record_id): r.record_id for r in candidates.values()}
        accepted = unchanged = invalid = 0
        for incoming in batch.daily + batch.intervals + batch.sleeps + batch.workouts:
            if incoming.record_id in state.retired_ids:
                raise RuntimeFailure("record_deleted", "已删除记录编号不能复用，请使用新编号。")
            key = (incoming.provider, incoming.provider_record_id)
            if key in keys and keys[key] != incoming.record_id:
                raise RuntimeFailure('record_identity_conflict', '来源记录编号已绑定其他记录。')
            current = candidates.get(incoming.record_id)
            if current:
                if (current.provider, current.provider_record_id, current.record_type) != (incoming.provider, incoming.provider_record_id, incoming.record_type):
                    raise RuntimeFailure('record_identity_conflict', '记录身份不能改变。')
                if current == incoming:
                    unchanged += 1
                    continue
                if incoming.record_revision != current.record_revision + 1:
                    raise RuntimeFailure('stale_record_revision', '修改必须使用下一记录版本。')
            elif incoming.record_revision != 1:
                raise RuntimeFailure('stale_record_revision', '新记录版本必须为 1。')
            candidates[incoming.record_id] = incoming.model_copy(deep=True)
            keys[key] = incoming.record_id
            accepted += 1
            invalid += sum(m.quality != 'valid' for m in incoming.metrics)
        if len(candidates) > MAX_RECORDS or sum(len(r.model_dump_json().encode('utf-8')) for r in candidates.values()) > MAX_BYTES:
            raise RuntimeFailure('wearable_storage_limit', '会话设备数据超过内存预算。', 413)
        return {'accepted': accepted, 'unchanged': unchanged, 'invalid_metrics': invalid}, candidates

    def snapshot(self, session_id, scope):
        if scope not in ('local_analysis', 'model_context'):
            raise RuntimeFailure('invalid_scope', '未知设备数据用途。', 400)
        record = self.sessions.require(session_id)
        if record.lifecycle_pending:
            raise RuntimeFailure('session_busy', '会话生命周期操作进行中。')
        state = record.wearables

        def permitted(source, metric, visiting, budget):
            budget[0] -= 1
            if budget[0] < 0 or len(visiting) >= 64:
                return False
            consent = state.consents.get(source.provider)
            if not consent or scope not in consent.scopes or metric.metric_id not in consent.allowed_metrics:
                return False
            node = (source.record_id, metric.metric_id)
            if node in visiting:
                return False
            for ref in metric.dependency_refs:
                dependency = state.records.get(ref.source_id)
                # Until metric-level references exist, require access to ALL metrics of a dependency record.
                if ref.kind != 'wearable_record' or dependency is None or dependency.record_revision != ref.revision:
                    return False
                if not all(permitted(dependency, item, visiting | {node}, budget) for item in dependency.metrics):
                    return False
            return True

        selected = []
        for source in state.records.values():
            metrics = [m.model_copy(deep=True) for m in source.metrics if permitted(source, m, set(), [1000])]
            if metrics:
                copy = source.model_copy(deep=True)
                copy.metrics = metrics
                selected.append(copy)
        return WearableSnapshot(session_id, state.revision, scope, tuple(selected))

    def validate_snapshot(self, session_id, snapshot):
        record = self.sessions.require(session_id)
        if (snapshot.session_id != session_id or snapshot.revision != record.wearables.revision
                or record.lifecycle_pending or record.session.status != SessionStatus.ACTIVE):
            raise RuntimeFailure('stale_wearable_snapshot', '设备数据或授权已失效，结果不可提交。')
