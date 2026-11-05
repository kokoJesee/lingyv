"""Versioned, session-scoped interpretation management, not automatic memory."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from uuid import uuid4

from app.agent.errors import RuntimeFailure
from app.config.settings import get_settings
from app.schemas.session import SessionStatus
from app.schemas.understanding import Understanding
from app.schemas.understanding_service import (
    AddUserReport, ProposeUnderstanding, EditUnderstanding, DecideUnderstanding,
    ConfirmUnderstanding, UnderstandingView,
)
from app.schemas.workflow import StopReason, WorkflowPhase
from app.services.session_service import OperationRecord
from app.services.understanding_state import UnderstandingEntry, UserReport
from app.services.wearable_service import WearableService


class UnderstandingService:
    def __init__(self, sessions, settings=None):
        self.sessions = sessions
        self.settings = settings or get_settings()
        self.wearables = WearableService(sessions, self.settings)

    async def add_report(self, session_id, request: AddUserReport):
        return await self._apply(session_id, 'report', None, request, AddUserReport)

    async def delete_report(self, session_id, report_id, request: DecideUnderstanding):
        return await self._apply(session_id, 'delete_report', report_id, request, DecideUnderstanding)

    async def propose(self, session_id, request: ProposeUnderstanding):
        return await self._apply(session_id, 'propose', None, request, ProposeUnderstanding)

    async def edit(self, session_id, understanding_id, request: EditUnderstanding):
        return await self._apply(session_id, 'edit', understanding_id, request, EditUnderstanding)

    async def confirm(self, session_id, understanding_id, request: ConfirmUnderstanding):
        return await self._apply(session_id, 'confirm', understanding_id, request, ConfirmUnderstanding)

    async def reject(self, session_id, understanding_id, request: DecideUnderstanding):
        return await self._apply(session_id, 'reject', understanding_id, request, DecideUnderstanding)

    def _entry(self, record, identity):
        entry = record.understandings.entries.get(identity)
        if entry is None:
            raise RuntimeFailure('understanding_not_found', '当前会话没有该理解。', 404)
        return entry

    @staticmethod
    def _view(entry):
        return UnderstandingView(understanding=entry.value.model_copy(deep=True), confirmation_token=entry.token)

    def get(self, session_id, identity):
        record = self.sessions.require(session_id)
        if record.lifecycle_pending:
            raise RuntimeFailure('session_busy', '会话生命周期操作进行中。')
        return self._view(self._entry(record, identity))

    def _check_refs(self, record, refs, scope='local_analysis', target=None, trail=None, budget=None, *, own_barrier=False):
        trail = set() if trail is None else trail
        budget = [1000] if budget is None else budget
        if len(trail) >= 64:
            raise RuntimeFailure('source_conflict', '理解依赖过深。')
        keys = [(r.kind, r.source_id) for r in refs]
        if len(keys) != len(set(keys)):
            raise RuntimeFailure('source_conflict', '来源引用重复。')
        wearable = False
        snapshot = None
        for ref in refs:
            budget[0] -= 1
            if budget[0] < 0:
                raise RuntimeFailure('source_conflict', '理解依赖超出检查预算。')
            if ref.kind == 'user_report':
                source = record.understandings.reports.get(ref.source_id)
                valid = source is not None and source.revision == ref.revision
            elif ref.kind == 'understanding':
                if ref.source_id == target or ref.source_id in trail:
                    raise RuntimeFailure('source_conflict', '理解不能形成依赖循环。')
                parent = record.understandings.entries.get(ref.source_id)
                valid = parent is not None and parent.value.status == 'confirmed' and parent.value.revision == ref.revision
                if valid:
                    wearable |= self._check_refs(record, parent.value.evidence_refs, scope, target, trail | {ref.source_id}, budget, own_barrier=own_barrier)
            elif ref.kind == 'consent':
                consent = next((c for c in record.wearables.consents.values() if str(c.consent_id) == ref.source_id), None)
                valid = consent is not None and consent.revision == ref.revision and scope in consent.scopes
                wearable = True
            else:
                if snapshot is None:
                    # A high-priority edit owns one lifecycle barrier while holding the lock.
                    # This read is synchronous: expose only that barrier for the duration
                    # of snapshot validation, then restore it before any other task runs.
                    if own_barrier:
                        record.lifecycle_pending -= 1
                    try:
                        snapshot = {r.record_id: r for r in self.wearables.snapshot(record.session.session_id, scope).records}
                    finally:
                        if own_barrier:
                            record.lifecycle_pending += 1
                source = record.wearables.records.get(ref.source_id)
                permitted = snapshot.get(ref.source_id)
                # Record-level references cannot silently include unauthorized fields.
                valid = (source is not None and permitted is not None and source.record_revision == ref.revision
                         and len(permitted.metrics) == len(source.metrics))
                wearable = True
            if not valid:
                raise RuntimeFailure('source_invalid', '来源不存在、版本失效或当前用途未授权。')
        return wearable

    def current(self, session_id, *, scope='local_analysis'):
        """Only current confirmed interpretations; never include control tokens."""
        record = self.sessions.require(session_id)
        if scope not in ('local_analysis', 'model_context'):
            raise RuntimeFailure('invalid_scope', '未知用途。', 400)
        if record.lifecycle_pending or record.session.status != SessionStatus.ACTIVE:
            raise RuntimeFailure('session_not_editable', '会话不可用于新交互。')
        result = []
        for entry in record.understandings.entries.values():
            if entry.value.status != 'confirmed': continue
            if entry.wearable_revision is not None and entry.wearable_revision != record.wearables.revision: continue
            try:
                self._check_refs(record, entry.value.evidence_refs, scope)
            except RuntimeFailure:
                continue
            result.append(entry.value.model_copy(deep=True))
        return result

    def validate_reference(self, session_id, reference, *, scope='local_analysis'):
        if reference.kind != 'understanding':
            raise RuntimeFailure('source_invalid', '需要理解来源引用。')
        if not any(v.understanding_id == reference.source_id and v.revision == reference.revision
                   for v in self.current(session_id, scope=scope)):
            raise RuntimeFailure('stale_understanding', '理解已变更或不再有效。')

    def _replay(self, record, operation, fingerprint):
        if operation.fingerprint != fingerprint:
            raise RuntimeFailure('operation_id_conflict', '操作编号对应不同内容。')
        if operation.error:
            raise RuntimeFailure(*operation.error)
        receipt = operation.result
        if receipt['kind'] == 'report':
            report = record.understandings.reports.get(receipt['id'])
            if report is None:
                raise RuntimeFailure('source_invalid', '自述已删除。')
            return deepcopy(report)
        if receipt['kind'] == 'deleted': return deepcopy(receipt)
        entry = self._entry(record, receipt['id'])
        if entry.value.revision != receipt['revision']:
            raise RuntimeFailure('operation_superseded', '该操作的结果已被修订或作废。')
        self._check_refs(record, entry.value.evidence_refs)
        return self._view(entry)

    def _preflight_interrupt(self, record, action, identity, request):
        """Only a valid change to a live target may interrupt an active chat."""
        if record.session.status != SessionStatus.ACTIVE:
            raise RuntimeFailure('session_ended', '会话已结束。')
        state = record.understandings
        if action == 'delete_report':
            target = state.reports.get(identity)
            if target is None:
                raise RuntimeFailure('source_invalid', '自述不存在。', 404)
            if target.revision != request.expected_revision:
                raise RuntimeFailure('stale_revision', '自述版本已变化。')
            return
        entry = self._entry(record, identity)
        value = entry.value
        if value.revision != request.expected_revision:
            raise RuntimeFailure('stale_revision', '理解版本已改变。')
        if value.status in ('rejected', 'invalidated'):
            raise RuntimeFailure('understanding_inactive', '理解已拒绝或作废，请提出新理解。')
        if action == 'edit':
            if (request.interpretation == value.interpretation and request.origin == value.origin
                    and request.evidence_refs == value.evidence_refs):
                raise RuntimeFailure('session_busy', '未改变理解内容，不能中断当前运行。')
            count = len(record.operations)+len(record.draft_operations)+len(record.wearables.operations)+len(state.operations)
            if count >= self.settings.runtime_max_requests_per_session:
                raise RuntimeFailure('request_limit_exceeded', '会话操作记录已达上限。')
            self._check_refs(record, request.evidence_refs, target=identity)
            candidate = Understanding.model_validate({**value.model_dump(),
                'revision': value.revision+1, 'updated_at': datetime.now(timezone.utc),
                'interpretation': request.interpretation, 'origin': request.origin,
                'evidence_refs': request.evidence_refs, 'status': 'proposed',
                'confirmed_at': None, 'confirmed_revision': None})
            if len(candidate.model_dump_json()) > self.settings.runtime_max_context_chars:
                raise RuntimeFailure('understanding_too_large', '理解超过上下文预算。', 413)

    async def _apply(self, session_id, action, identity, request, schema):
        request = schema.model_validate_json(request.model_dump_json())
        fingerprint = hashlib.sha256(json.dumps(dict(action=action, identity=identity,
            request=request.model_dump(mode='json', exclude={'operation_id'})), sort_keys=True).encode()).hexdigest()
        record = self.sessions.require(session_id)
        state = record.understandings
        # Replays cannot cancel a later run. No await occurs before this check or
        # preflight, so the current target/version is stable until we set the barrier.
        if record.lifecycle_pending:
            raise RuntimeFailure('session_busy', '会话生命周期操作进行中，请稍后重试。')
        previous = state.operations.get(request.operation_id)
        if previous and action in ('edit', 'reject', 'delete_report'):
            return self._replay(record, previous, fingerprint)
        active_chat = (record.session.workflow.kind == 'chat'
                       and record.session.workflow.phase == WorkflowPhase.RUNNING
                       and record.active_task is not None)
        interrupting = active_chat and action in ('edit', 'reject', 'delete_report')
        if interrupting:
            self._preflight_interrupt(record, action, identity, request)
        elif record.lock.locked() or record.session.workflow.phase in (WorkflowPhase.RUNNING, WorkflowPhase.WAITING_CONFIRMATION):
            raise RuntimeFailure('session_busy', '运行或生命周期操作进行中，请稍后重试。')
        if interrupting:
            record.lifecycle_pending += 1
            record.cancel_reason = record.cancel_reason or StopReason.USER_CANCELLED
            if not record.active_task.done():
                record.active_task.cancel()
        try:
            async with record.lock:
                if record.lifecycle_pending > int(interrupting):
                    raise RuntimeFailure('session_busy', '另一个生命周期操作进行中，请稍后重试。')
                previous = state.operations.get(request.operation_id)
                if previous:
                    return self._replay(record, previous, fingerprint)
                privacy = action in ('reject', 'delete_report')
                if record.session.status != SessionStatus.ACTIVE and not privacy:
                    raise RuntimeFailure('session_ended', '会话已结束。')
                count = len(record.operations)+len(record.draft_operations)+len(record.wearables.operations)+len(state.operations)
                at_limit = count >= self.settings.runtime_max_requests_per_session
                if at_limit and not privacy:
                    raise RuntimeFailure('request_limit_exceeded', '会话操作记录已达上限。')
                operation = OperationRecord(fingerprint=fingerprint)
                if not at_limit: state.operations[request.operation_id] = operation
                try:
                    if action == 'report':
                        if len(request.content) > self.settings.runtime_max_context_chars:
                            raise RuntimeFailure('report_too_large', '自述超过内容预算。', 413)
                        report = UserReport(str(uuid4()), request.content)
                        state.reports[report.report_id] = report
                        operation.result = dict(kind='report', id=report.report_id, revision=1)
                        record.session.revision += 1
                        return deepcopy(report)
                    if action == 'delete_report':
                        report = state.reports.get(identity)
                        if report is None:
                            raise RuntimeFailure('source_invalid', '自述不存在。', 404)
                        if report.revision != request.expected_revision:
                            raise RuntimeFailure('stale_revision', '自述版本已变化。')
                        del state.reports[identity]
                        state.invalidate(source_kind='user_report', source_id=identity)
                        operation.result = dict(kind='deleted', id=identity)
                        record.session.revision += 1
                        return deepcopy(operation.result)
                    now = datetime.now(timezone.utc)
                    if action == 'propose':
                        identity = str(uuid4())
                        wearable = self._check_refs(record, request.evidence_refs, target=identity, own_barrier=interrupting)
                        value = Understanding(understanding_id=identity, revision=1, status='proposed',
                            interpretation=request.interpretation, origin=request.origin, evidence_refs=request.evidence_refs,
                            created_at=now, updated_at=now)
                        entry = UnderstandingEntry(value, uuid4(), record.wearables.revision if wearable else None)
                    else:
                        current = self._entry(record, identity)
                        if current.value.revision != request.expected_revision:
                            raise RuntimeFailure('stale_revision', '理解版本已改变。')
                        if current.value.status in ('rejected', 'invalidated'):
                            raise RuntimeFailure('understanding_inactive', '理解已拒绝或作废，请提出新理解。')
                        fields = {**current.value.model_dump(), 'revision': current.value.revision+1, 'updated_at': now,
                                  'confirmed_at': None, 'confirmed_revision': None}
                        entry = UnderstandingEntry(current.value, None, current.wearable_revision)
                        if action == 'edit':
                            wearable = self._check_refs(record, request.evidence_refs, target=identity, own_barrier=interrupting)
                            fields.update(interpretation=request.interpretation, origin=request.origin,
                                          evidence_refs=request.evidence_refs, status='proposed')
                            entry.token = uuid4()
                            entry.wearable_revision = record.wearables.revision if wearable else None
                        elif action == 'confirm':
                            if current.value.status != 'proposed' or current.token != request.confirmation_token:
                                raise RuntimeFailure('stale_confirmation', '确认令牌已失效。')
                            self._check_refs(record, current.value.evidence_refs)
                            fields.update(status='confirmed', confirmed_at=now, confirmed_revision=fields['revision'])
                        else:
                            fields['status'] = 'rejected'
                        entry.value = Understanding.model_validate(fields)
                    if len(entry.value.model_dump_json()) > self.settings.runtime_max_context_chars:
                        raise RuntimeFailure('understanding_too_large', '理解超过上下文预算。', 413)
                    # Commit only after validation; changing this version invalidates descendants.
                    state.invalidate(source_kind='understanding', source_id=identity)
                    state.entries[identity] = entry
                    record.session.revision += 1
                    operation.result = dict(kind='understanding', id=identity, revision=entry.value.revision)
                    return self._view(entry)
                except RuntimeFailure as error:
                    operation.error = (error.code, error.message, error.status_code)
                    raise
        finally:
            if interrupting:
                record.lifecycle_pending -= 1
