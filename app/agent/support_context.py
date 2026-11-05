"""Source-aware psychology turns; caller holds the session lock."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from uuid import uuid4

from app.agent.errors import RuntimeFailure
from app.psychology.inputs import PsychologyInputBuilder
from app.psychology.prompts import PROMPT_VERSION, system_prompt
from app.schemas.context import ContextManifest, ContextSource, DraftReference, TurnDependencies
from app.schemas.draft import DraftStatus
from app.schemas.psychology import RecordSource
from app.schemas.understanding import Understanding
from app.schemas.wearable import EvidenceReference
from app.services.state_analysis import AnalysisConfig
from app.services.understanding_service import UnderstandingService
from app.services.understanding_state import UserReport, UnderstandingEntry


def dependencies_valid(record, deps, sessions, suppressed=()):
    if deps is None: return True
    if deps.wearable_revision is not None and deps.wearable_revision != record.wearables.revision:
        return False
    for ref in deps.draft_refs:
        draft = record.drafts.get(ref.draft_id)
        if draft is None or draft.status != DraftStatus.CONFIRMED or draft.confirmed_revision != ref.revision or draft.revision != ref.revision:
            return False
    if deps.understanding_refs:
        valid = {(u.understanding_id, u.revision) for u in UnderstandingService(sessions).current(record.session.session_id, scope='model_context')}
        if any(ref.source_id in suppressed or (ref.source_id, ref.revision) not in valid for ref in deps.understanding_refs):
            return False
    return True


def prune_cached_results(record, sessions):
    """Content is discarded lazily before any reuse; operation IDs remain tombstones."""
    for operation in record.operations.values():
        if operation.result is not None and not dependencies_valid(record, operation.dependencies, sessions):
            operation.result = None
            operation.error = ('source_withdrawn', '该回复依赖的来源已变更或撤回，不会重放或重新执行。', 409)
            operation.support_notice = None


def prepare_correction(record, request):
    if request.correction_of is None:
        return (), None
    target = request.correction_of
    entry = record.understandings.entries.get(target.understanding_id)
    if entry is None or entry.value.revision != target.expected_revision:
        raise RuntimeFailure('stale_revision', '要纠正的理解已不存在或版本已变化。')
    if entry.value.status not in ('proposed', 'confirmed'):
        raise RuntimeFailure('understanding_inactive', '该理解已拒绝或作废。')
    suppressed = {target.understanding_id}
    while True:
        children = {key for key, e in record.understandings.entries.items()
                    if any(r.kind == 'understanding' and r.source_id in suppressed for r in e.value.evidence_refs)}
        if children.issubset(suppressed): break
        suppressed.update(children)
    now = datetime.now(timezone.utc)
    report = UserReport(str(uuid4()), request.content)
    value = Understanding(understanding_id=entry.value.understanding_id, revision=entry.value.revision+1,
        status='proposed', interpretation=request.content, origin='user_report',
        evidence_refs=[EvidenceReference(kind='user_report', source_id=report.report_id, revision=1)],
        created_at=entry.value.created_at, updated_at=now)
    candidate = UnderstandingEntry(value, uuid4())
    return tuple(suppressed), (report, candidate)


def commit_correction(record, candidate):
    if candidate is None: return
    report, entry = candidate
    record.understandings.invalidate(source_kind='understanding', source_id=entry.value.understanding_id)
    record.understandings.reports[report.report_id] = report
    record.understandings.entries[entry.value.understanding_id] = entry


def prepare_support(record, sessions, request, settings, *, as_of=None):
    suppressed, correction = prepare_correction(record, request)
    history, indices, excluded = [], [], []
    inherited = TurnDependencies()
    drafts = {(d.draft_id, d.revision) for d in record.drafts.values() if d.status == DraftStatus.CONFIRMED and d.confirmed_revision == d.revision}
    for index, message in enumerate(record.session.messages):
        draft_refs = record.turn_sources.get(index, [])
        deps = record.business_turn_sources.get(index)
        if message.role == 'assistant' and (any((d.draft_id,d.revision) not in drafts for d in draft_refs)
                or not dependencies_valid(record, deps, sessions, suppressed)):
            excluded.append(index)
            continue
        history.append(message.model_copy(deep=True)); indices.append(index)
        if message.role == 'assistant':
            inherited.draft_refs.extend(draft_refs)
            if deps:
                inherited.draft_refs.extend(deps.draft_refs)
                inherited.understanding_refs.extend(deps.understanding_refs)
                if deps.wearable_revision is not None: inherited.wearable_revision = deps.wearable_revision
    builder = PsychologyInputBuilder(sessions)
    prepared = builder.prepare_locked(record.session.session_id, request.request_id, current_message=request.content,
        history=history, as_of=as_of or datetime.now(timezone.utc), config=AnalysisConfig(timezone=settings.analysis_timezone),
        excluded_understanding_ids=suppressed)
    for source in prepared.source_manifest.values():
        if source['kind'] == 'analysis': inherited.wearable_revision = prepared.wearable_revision
        elif source['kind'] == 'understanding':
            inherited.understanding_refs.append(EvidenceReference(kind='understanding', source_id=source['source_id'], revision=source['revision']))
            if record.understandings.entries[source['source_id']].wearable_revision is not None:
                inherited.wearable_revision = prepared.wearable_revision
    # Keep legacy confirmed draft data as data records, never as user factual history.
    for draft in record.drafts.values():
        if (draft.draft_id,draft.revision) not in drafts: continue
        identity = f'record_{len(prepared.request.records)+1:04}'
        prepared.request.records.append(RecordSource(source_id=identity, evidence_type='confirmed_memory',
            content='用户确认的草稿数据，非独立临床事实：'+draft.content.model_dump_json()))
        prepared.source_manifest[identity] = dict(kind='draft', source_id=str(draft.draft_id), revision=draft.revision)
        inherited.draft_refs.append(DraftReference(draft_id=draft.draft_id, revision=draft.revision))
    inherited.understanding_refs = list({(r.source_id,r.revision):r for r in inherited.understanding_refs}.values())
    inherited.draft_refs = list({(r.draft_id,r.revision):r for r in inherited.draft_refs}.values())
    prepared = replace(prepared, fingerprint=hashlib.sha256(prepared.request.model_dump_json().encode()).hexdigest())
    sources = [ContextSource(kind='system_prompt', model_message_index=0, role='system'),
               ContextSource(kind='current_input', model_message_index=1, role='user', source_id='current_1')]
    sources.extend(ContextSource(kind='history_message', model_message_index=1, role=m.role, history_index=i,
                   source_id=f'history_{j+1:04}') for j,(i,m) in enumerate(zip(indices,history)))
    sources.extend(ContextSource(kind='psychology_record', model_message_index=1, role='user', source_id=r.source_id,
                                  evidence_type=r.evidence_type) for r in prepared.request.records)
    manifest = ContextManifest(builder_version='psychology_context.v1', prompt_version=PROMPT_VERSION,
        session_id=record.session.session_id, session_revision=record.session.revision, request_id=request.request_id,
        model_message_count=2, character_count=len(system_prompt())+len(prepared.request.model_dump_json()),
        sources=sources, excluded_history_indices=excluded, business_dependencies=inherited)
    return builder, prepared, manifest, inherited, correction
