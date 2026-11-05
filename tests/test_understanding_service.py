import asyncio
from copy import deepcopy
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.agent.errors import RuntimeFailure
from app.agent.orchestrator import AgentOrchestrator
from app.schemas.wearable import EvidenceReference
from app.schemas.understanding_service import (
    AddUserReport, ProposeUnderstanding, EditUnderstanding, ConfirmUnderstanding, DecideUnderstanding,
)
from app.services.understanding_service import UnderstandingService
from app.services.session_service import SessionService


def setup():
    sessions = SessionService(); sid = sessions.create().session_id
    return sessions, sid, UnderstandingService(sessions)


def ref(report):
    return EvidenceReference(kind='user_report', source_id=report.report_id, revision=report.revision)


def uref(view):
    v = view.understanding
    return EvidenceReference(kind='understanding', source_id=v.understanding_id, revision=v.revision)


async def propose(service, sid, text='可能与备考有关'):
    report = await service.add_report(sid, AddUserReport(content='最近作息变晚了'))
    req = ProposeUnderstanding(interpretation=text, origin='agent_hypothesis', evidence_refs=[ref(report)])
    return report, req, await service.propose(sid, req)


async def confirm(service, sid, view):
    return await service.confirm(sid, view.understanding.understanding_id, ConfirmUnderstanding(
        expected_revision=view.understanding.revision, confirmation_token=view.confirmation_token))


def test_correction_versions_tokens_and_no_old_replay():
    async def run():
        _, sid, service = setup()
        report, req, view = await propose(service, sid)
        assert not service.current(sid)
        assert (await service.propose(sid, req)) == view
        old_token = view.confirmation_token
        old = await confirm(service, sid, view)
        assert old.understanding.confirmed_revision == 2
        assert len(service.current(sid)) == 1
        travel = await service.add_report(sid, AddUserReport(content='其实是旅行，不是备考'))
        edit = EditUnderstanding(expected_revision=2, interpretation='作息变化与旅行有关', origin='user_report', evidence_refs=[ref(travel)])
        changed = await service.edit(sid, old.understanding.understanding_id, edit)
        assert changed.understanding.revision == 3
        assert changed.understanding.status == 'proposed'
        assert changed.confirmation_token != old_token
        assert not service.current(sid)
        with pytest.raises(RuntimeFailure) as error:
            await service.confirm(sid, old.understanding.understanding_id, ConfirmUnderstanding(expected_revision=3, confirmation_token=old_token))
        assert error.value.code == 'stale_confirmation'
        with pytest.raises(RuntimeFailure) as error:
            await service.propose(sid, req)
        assert error.value.code == 'operation_superseded'
        final = await confirm(service, sid, changed)
        assert service.current(sid)[0].interpretation == '作息变化与旅行有关'
        assert final.understanding.revision == 4
        with pytest.raises(RuntimeFailure): service.validate_reference(sid, uref(old))
        service.validate_reference(sid, uref(final))
        assert report.content == '最近作息变晚了'
    asyncio.run(run())


def test_sources_isolation_rejection_and_copy():
    async def run():
        sessions, sid, service = setup()
        _, request, view = await propose(service, sid)
        other = sessions.create().session_id
        with pytest.raises(RuntimeFailure): await service.propose(other, request)
        with pytest.raises(RuntimeFailure): service.get(other, view.understanding.understanding_id)
        copy = service.get(sid, view.understanding.understanding_id)
        copy.understanding.interpretation = 'changed outside service'
        assert service.get(sid, view.understanding.understanding_id).understanding.interpretation != copy.understanding.interpretation
        confirmed = await confirm(service, sid, view)
        rejected = await service.reject(sid, view.understanding.understanding_id, DecideUnderstanding(expected_revision=2))
        assert rejected.understanding.status == 'rejected'
        assert rejected.confirmation_token is None
        assert not service.current(sid)
        with pytest.raises(RuntimeFailure): await confirm(service, sid, view)
        with pytest.raises(RuntimeFailure): service.validate_reference(sid, uref(confirmed))
    asyncio.run(run())


def test_cascading_dependencies_and_cycle_rejected():
    async def run():
        _, sid, service = setup()
        report, _, first = await propose(service, sid)
        first = await confirm(service, sid, first)
        second = await service.propose(sid, ProposeUnderstanding(interpretation='依赖第一条理解', origin='agent_hypothesis', evidence_refs=[uref(first)]))
        second = await confirm(service, sid, second)
        with pytest.raises(RuntimeFailure) as error:
            await service.edit(sid, first.understanding.understanding_id, EditUnderstanding(expected_revision=2,
                interpretation='循环', origin='agent_hypothesis', evidence_refs=[uref(second)]))
        assert error.value.code == 'source_conflict'
        assert len(service.current(sid)) == 2
        await service.delete_report(sid, report.report_id, DecideUnderstanding(expected_revision=1))
        assert not service.current(sid)
        assert service.get(sid, first.understanding.understanding_id).understanding.status == 'invalidated'
        assert service.get(sid, second.understanding.understanding_id).understanding.status == 'invalidated'
    asyncio.run(run())


def test_parent_edit_invalidates_descendant():
    async def run():
        _, sid, service = setup()
        report, _, first = await propose(service, sid)
        first = await confirm(service, sid, first)
        second = await service.propose(sid, ProposeUnderstanding(interpretation='后续解释', origin='agent_hypothesis', evidence_refs=[uref(first)]))
        await service.edit(sid, first.understanding.understanding_id, EditUnderstanding(expected_revision=2,
            interpretation='新解释', origin='agent_hypothesis', evidence_refs=[ref(report)]))
        assert service.get(sid, second.understanding.understanding_id).understanding.status == 'invalidated'
        assert not service.current(sid)
    asyncio.run(run())


def test_clear_ends_content_and_replay():
    async def run():
        sessions, sid, service = setup()
        _, req, view = await propose(service, sid)
        await confirm(service, sid, view)
        await AgentOrchestrator(sessions).clear_history(sid)
        assert not service.current(sid)
        assert not sessions.require(sid).understandings.reports
        with pytest.raises(RuntimeFailure) as error: await service.propose(sid, req)
        assert error.value.code == 'request_cleared'
    asyncio.run(run())


def test_concurrent_edits_and_idempotency():
    async def run():
        _, sid, service = setup()
        report, req, view = await propose(service, sid)
        original = deepcopy(req)
        req.interpretation = 'conflicting body'
        with pytest.raises(RuntimeFailure) as error: await service.propose(sid, req)
        assert error.value.code == 'operation_id_conflict'
        edits = [EditUnderstanding(expected_revision=1, interpretation=t, origin='agent_hypothesis', evidence_refs=[ref(report)]) for t in ('A', 'B')]
        results = await asyncio.gather(*(service.edit(sid, view.understanding.understanding_id, e) for e in edits), return_exceptions=True)
        assert sum(isinstance(x, RuntimeFailure) for x in results) == 1
        with pytest.raises(RuntimeFailure): await service.propose(sid, original)
    asyncio.run(run())


def test_busy_and_ended_mutations_and_privacy_budget():
    async def run():
        from app.config.settings import Settings
        sessions, sid, _ = setup()
        service = UnderstandingService(sessions, Settings(runtime_max_requests_per_session=2))
        report, _, view = await propose(service, sid)
        with pytest.raises(RuntimeFailure) as error: await confirm(service, sid, view)
        assert error.value.code == 'request_limit_exceeded'
        async with sessions.require(sid).lock:
            with pytest.raises(RuntimeFailure) as error: await service.add_report(sid, AddUserReport(content='忙碌'))
            assert error.value.code == 'session_busy'
        await AgentOrchestrator(sessions).end_session(sid)
        await service.reject(sid, view.understanding.understanding_id, DecideUnderstanding(expected_revision=1))
        await service.delete_report(sid, report.report_id, DecideUnderstanding(expected_revision=1))
        assert len(sessions.require(sid).understandings.operations) == 2
        with pytest.raises(RuntimeFailure): service.current(sid)
    asyncio.run(run())


@pytest.mark.parametrize('change', [{'status':'confirmed'}, {'memory_update':'write'}, {'session_id':str(uuid4())}, {'interpretation':' '}])
def test_requests_cannot_grant_privileges(change):
    with pytest.raises(ValidationError):
        ProposeUnderstanding(**dict(interpretation='x', origin='agent_hypothesis', evidence_refs=[dict(kind='user_report', source_id='x', revision=1)]) | change)


def test_user_origin_requires_report_not_device():
    with pytest.raises(ValidationError):
        ProposeUnderstanding(interpretation='x', origin='user_report', evidence_refs=[dict(kind='wearable_record', source_id='x', revision=1)])


def test_wearable_grants_and_revocation_do_not_change_measurements():
    async def run():
        from app.schemas.wearable_service import SetWearableConsent
        from app.services.wearable_service import WearableService
        from app.wearables.mock import MockWearableAdapter
        from app.wearables.base import MockQuery
        from app.wearables.metric_registry import METRIC_REGISTRY
        sessions, sid, service = setup()
        data = WearableService(sessions)
        await MockWearableAdapter().import_into(data, sid, MockQuery(as_of='2026-11-04T00:00:00+08:00', days=1, include_intervals=False), expected_revision=0, operation_id=uuid4())
        record = next(iter(sessions.require(sid).wearables.records.values()))
        before = record.model_dump_json()
        req = ProposeUnderstanding(interpretation='作息变化可能与旅行有关', origin='agent_hypothesis', evidence_refs=[dict(kind='wearable_record', source_id=record.record_id, revision=1)])
        with pytest.raises(RuntimeFailure): await service.propose(sid, req)
        await data.set_consent(sid, SetWearableConsent(expected_revision=1, provider='mock', allowed_metrics=list(METRIC_REGISTRY), scopes=['local_analysis']))
        req.operation_id = uuid4()
        view = await service.propose(sid, req); confirmed = await confirm(service, sid, view)
        assert service.current(sid)
        assert not service.current(sid, scope='model_context')
        await data.set_consent(sid, SetWearableConsent(expected_revision=2, provider='mock', allowed_metrics=[], scopes=[]))
        assert not service.current(sid)
        assert service.get(sid, view.understanding.understanding_id).understanding.status == 'invalidated'
        assert record.model_dump_json() == before
        with pytest.raises(RuntimeFailure): service.validate_reference(sid, uref(confirmed))
        with pytest.raises(RuntimeFailure): await service.propose(sid, req)
    asyncio.run(run())


def test_demo_reports_correction_without_memory_write():
    from app.understanding_cli import demo
    result = asyncio.run(demo())
    assert result['old_reference_invalid']
    assert result['current_revision'] == 4
    assert result['memory_update'] is None
    assert '旅行' in result['current_interpretations'][0]


def test_confirm_replay_and_foreign_token():
    async def run():
        _, sid, service = setup()
        _, _, first = await propose(service, sid)
        _, _, second = await propose(service, sid)
        wrong = ConfirmUnderstanding(expected_revision=1, confirmation_token=second.confirmation_token)
        with pytest.raises(RuntimeFailure) as error: await service.confirm(sid, first.understanding.understanding_id, wrong)
        assert error.value.code == 'stale_confirmation'
        request = ConfirmUnderstanding(expected_revision=1, confirmation_token=first.confirmation_token)
        confirmed = await service.confirm(sid, first.understanding.understanding_id, request)
        assert await service.confirm(sid, first.understanding.understanding_id, request) == confirmed
        assert confirmed.confirmation_token is None
    asyncio.run(run())


def test_source_invalid_errors_replay_without_crashing():
    async def run():
        _, sid, service = setup()
        request = ProposeUnderstanding(interpretation='未知来源', origin='agent_hypothesis',
            evidence_refs=[dict(kind='understanding', source_id='missing', revision=1)])
        for _ in range(2):
            with pytest.raises(RuntimeFailure) as error: await service.propose(sid, request)
            assert error.value.code == 'source_invalid'
        assert not service.current(sid)
    asyncio.run(run())
