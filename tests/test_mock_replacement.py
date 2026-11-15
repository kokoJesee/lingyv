import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from app.agent.errors import RuntimeFailure
from app.schemas.session import SessionStatus
from app.schemas.understanding_service import AddUserReport, ProposeUnderstanding, ConfirmUnderstanding
from app.schemas.wearable import EvidenceReference, WearableImportBatch
from app.schemas.wearable_service import ImportWearables, SetWearableConsent
from app.schemas.workflow import WorkflowPhase
from app.services.session_service import SessionService
from app.services.understanding_service import UnderstandingService
from app.services.wearable_service import WearableService
from app.wearables.base import MockQuery
from app.wearables.metric_registry import METRIC_REGISTRY
from app.wearables.mock_generator import generate


def batch(scenario='S04'):
    return generate(MockQuery(as_of='2026-11-05T12:00:00+08:00', days=21,
                              scenario_id=scenario, seed=42, include_intervals=False)).batch


def request(revision, scenario='S02'):
    return ImportWearables(expected_revision=revision, batch=batch(scenario))


async def populated():
    sessions = SessionService()
    sid = sessions.create().session_id
    service = WearableService(sessions)
    await service.import_records(sid, request(0, 'S04'))
    return sessions, sid, service


async def confirmed(understandings, sid, refs, text):
    proposed = await understandings.propose(sid, ProposeUnderstanding(
        interpretation=text, origin='agent_hypothesis', evidence_refs=refs))
    return await understandings.confirm(sid, proposed.understanding.understanding_id,
        ConfirmUnderstanding(expected_revision=proposed.understanding.revision,
                             confirmation_token=proposed.confirmation_token))


def test_switch_replaces_only_mock_rows_preserves_conversation_and_invalidates_old_sources():
    async def run():
        sessions, sid, service = await populated()
        record = sessions.require(sid)
        # Include an unrelated source; replacing a simulator must not clear it.
        other = next(iter(record.wearables.records.values())).model_copy(deep=True)
        other.record_id = other.provider_record_id = 'other-device-daily'
        other.provider = 'other'
        other.metrics = [m for m in other.metrics if m.metric_id == 'resting_heart_rate_bpm']
        await service.import_records(sid, ImportWearables(expected_revision=1,
            batch=WearableImportBatch(daily=[other])))
        await service.set_consent(sid, SetWearableConsent(expected_revision=2, provider='mock',
            allowed_metrics=list(METRIC_REGISTRY), scopes=['local_analysis', 'model_context']))
        record.session.messages = [{'role': 'user', 'content': '虚构旧对话'}]
        old_messages = deepcopy(record.session.messages)
        understandings = UnderstandingService(sessions)
        report = await understandings.add_report(sid, AddUserReport(content='用户自述应当保留'))
        independent = await confirmed(understandings, sid,
            [EvidenceReference(kind='user_report', source_id=report.report_id, revision=1)], '独立自述理解')
        old_id = next(identity for identity, row in record.wearables.records.items() if row.provider == 'mock')
        dependent = await confirmed(understandings, sid,
            [EvidenceReference(kind='wearable_record', source_id=old_id, revision=1)], '设备相关理解')
        old_ids = {identity for identity, row in record.wearables.records.items() if row.provider == 'mock'}
        snapshot = service.snapshot(sid, 'local_analysis')
        result = await service.replace_simulated(sid, request(3))
        assert result['replaced'] == 84 and result['accepted'] == 84
        assert result['revision'] == 4
        assert len(record.wearables.records) == 85
        assert other.record_id in record.wearables.records
        assert not old_ids.intersection(record.wearables.records)
        assert old_ids.issubset(record.wearables.retired_ids)
        assert record.session.messages == old_messages
        assert record.understandings.reports[report.report_id].content == report.content
        assert understandings.get(sid, independent.understanding.understanding_id).understanding.status == 'confirmed'
        assert understandings.get(sid, dependent.understanding.understanding_id).understanding.status == 'invalidated'
        assert service.consent(sid, 'mock').scopes == frozenset({'local_analysis'})
        assert not service.snapshot(sid, 'model_context').records
        with pytest.raises(RuntimeFailure, match='失效'):
            service.validate_snapshot(sid, snapshot)
        with pytest.raises(RuntimeFailure) as failure:
            await service.import_records(sid, request(4, 'S04'))
        assert failure.value.code == 'record_deleted'
    asyncio.run(run())


def test_replacement_never_grants_a_new_scope_or_expands_metrics():
    async def run():
        sessions, sid, service = await populated()
        await service.replace_simulated(sid, request(1))
        assert service.consent(sid, 'mock') is None
        assert not service.snapshot(sid, 'local_analysis').records
        await service.set_consent(sid, SetWearableConsent(expected_revision=2, provider='mock',
            allowed_metrics=['steps'], scopes=['local_analysis', 'model_context']))
        await service.replace_simulated(sid, request(3, 'S03'))
        consent = service.consent(sid, 'mock')
        assert consent.allowed_metrics == frozenset({'steps'})
        assert consent.scopes == frozenset({'local_analysis'})
    asyncio.run(run())


def test_replacement_checks_retained_record_versions_and_does_not_repeat_an_identical_batch():
    async def run():
        sessions, sid, service = await populated()
        state = sessions.require(sid).wearables
        req = request(1, 'S04')
        result = await service.replace_simulated(sid, req)
        assert result['unchanged'] == 84 and result['accepted'] == result['replaced'] == 0
        assert state.revision == 1
        changed = request(1, 'S04')
        changed.batch.daily[0].metrics[0].value += 1
        before = deepcopy(state.records)
        with pytest.raises(RuntimeFailure) as failure:
            await service.replace_simulated(sid, changed)
        assert failure.value.code == 'stale_record_revision'
        assert state.revision == 1 and state.records == before
    asyncio.run(run())


@pytest.mark.parametrize('corrupt_existing', [False, True])
def test_non_simulated_mock_rows_or_incoming_other_provider_fail_atomically(corrupt_existing):
    async def run():
        sessions, sid, service = await populated()
        current = sessions.require(sid).wearables
        req = request(1)
        if corrupt_existing:
            next(iter(current.records.values())).is_simulated = False
        else:
            req.batch.daily[0].provider = 'real-device'
        before = deepcopy(current.records)
        with pytest.raises(RuntimeFailure) as failure:
            await service.replace_simulated(sid, req)
        assert failure.value.code == 'invalid_simulated_replacement'
        assert current.records == before
        assert current.revision == 1 and not current.retired_ids
    asyncio.run(run())


def test_replacement_replays_once_and_conflicting_operation_id_cannot_replace_again():
    async def run():
        sessions, sid, service = await populated()
        req = request(1)
        receipts = await asyncio.gather(service.replace_simulated(sid, req), service.replace_simulated(sid, req))
        assert receipts[0] == receipts[1]
        assert service.revision(sid) == 2
        assert len(sessions.require(sid).wearables.records) == 84
        changed = request(2, 'S03'); changed.operation_id = req.operation_id
        with pytest.raises(RuntimeFailure) as failure:
            await service.replace_simulated(sid, changed)
        assert failure.value.code == 'operation_id_conflict'
        assert service.revision(sid) == 2
    asyncio.run(run())


@pytest.mark.parametrize('condition,code', [
    ('stale', 'stale_revision'), ('busy', 'session_busy'),
    ('ended', 'session_ended'), ('limit', 'request_limit_exceeded'),
])
def test_replacement_respects_lifecycle_revision_and_budget(condition, code):
    async def run():
        sessions, sid, service = await populated()
        record = sessions.require(sid)
        req = request(0 if condition == 'stale' else 1)
        if condition == 'busy': record.session.workflow.phase = WorkflowPhase.RUNNING
        if condition == 'ended': record.session.status = SessionStatus.ENDED
        if condition == 'limit': service.settings = service.settings.model_copy(update={'runtime_max_requests_per_session': 1})
        before = deepcopy(record.wearables.records)
        with pytest.raises(RuntimeFailure) as failure:
            await service.replace_simulated(sid, req)
        assert failure.value.code == code
        assert record.wearables.records == before and record.wearables.revision == 1
    asyncio.run(run())


def test_replacement_budget_failure_keeps_old_data_and_authorizations(monkeypatch):
    async def run():
        sessions, sid, service = await populated()
        await service.set_consent(sid, SetWearableConsent(expected_revision=1, provider='mock',
            allowed_metrics=['steps'], scopes=['model_context']))
        current = sessions.require(sid).wearables
        before = deepcopy(current.records), deepcopy(current.consents)
        monkeypatch.setattr('app.services.wearable_service.MAX_RECORDS', 10)
        with pytest.raises(RuntimeFailure) as failure:
            await service.replace_simulated(sid, request(2))
        assert failure.value.code == 'wearable_storage_limit'
        assert (current.records, current.consents) == before
        assert current.revision == 2 and not current.retired_ids
    asyncio.run(run())


def test_http_switch_s04_s02_s04_replaces_not_appends_and_updates_authorized_timezones(client):
    sid = client.post('/sessions').json()['session_id']
    base = f'/sessions/{sid}'
    instant = datetime.fromisoformat('2026-11-05T12:00:00+08:00')
    revision = 0
    for index, scenario in enumerate(('S04', 'S02', 'S04')):
        at = (instant + timedelta(seconds=index)).isoformat()
        response = client.post(base + '/wearables/simulate', json={
            'operation_id': str(uuid4()), 'expected_revision': revision,
            'as_of': at, 'scenario_id': scenario, 'seed': 42, 'days': 21,
            'replace_existing': index > 0,
        })
        assert response.status_code == 200, response.text
        revision = response.json()['revision']
        if index == 0:
            assert client.get(base + '/support-state').json()['wearables']['available_timezones'] == []
            granted = client.post(base + '/wearables/consent', json={
                'expected_revision': revision, 'provider': 'mock',
                'allowed_metrics': list(METRIC_REGISTRY),
                'scopes': ['local_analysis', 'model_context'],
            })
            assert granted.status_code == 200, granted.text
            revision = granted.json()['revision']
        state = client.get(base + '/support-state').json()['wearables']
        assert state['record_count'] == 84 and state['revision'] == revision
        provider = state['providers'][0]
        assert set(provider['allowed_metrics']) == set(METRIC_REGISTRY)
        assert provider['record_count'] == 84
        if index:
            assert provider['scopes'] == ['local_analysis']
        expected_zones = ['Asia/Tokyo', 'Asia/Shanghai'] if scenario == 'S04' else ['Asia/Shanghai']
        assert state['available_timezones'] == expected_zones
        chart = client.get(base + '/wearables/trends', params={
            'as_of': at, 'timezone': expected_zones[0], 'days': 14,
        })
        assert chart.status_code == 200, chart.text
        sleep = next(item for item in chart.json()['series'] if item['metric_id'] == 'sleep_duration_minutes')
        assert sleep['status'] == 'ready'
        assert sum(point['value'] is not None for point in sleep['points']) == (3 if scenario == 'S04' else 14)
