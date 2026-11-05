"""HTTP read contracts exercised with synthetic imports and actual service state."""
import asyncio
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app import main
from app.schemas.support_views import TrendQuery
from app.schemas.workflow import WorkflowPhase
from app.services.support_views import SupportViewService
from app.wearables.metric_registry import METRIC_REGISTRY

AS_OF = '2026-11-05T12:00:00+08:00'


def open_session(client):
    sid = client.post('/sessions').json()['session_id']
    return sid, f'/sessions/{sid}'


def simulate(client, base, scenario='S01', **changes):
    body = dict(expected_revision=0, operation_id=str(uuid4()), as_of=AS_OF,
                scenario_id=scenario, seed=42, days=21)
    body.update(changes)
    return client.post(base+'/wearables/simulate', json=body)


def consent(client, base, *, revision=1, metrics=None, scopes=None):
    return client.post(base+'/wearables/consent', json={
        'expected_revision': revision, 'provider': 'mock',
        'allowed_metrics': list(METRIC_REGISTRY) if metrics is None else metrics,
        'scopes': ['local_analysis'] if scopes is None else scopes,
    })


def trends(client, base, **parameters):
    return client.get(base+'/wearables/trends', params={'as_of': AS_OF, 'days': 14, **parameters})


def series(body, name):
    return next(item for item in body['series'] if item['metric_id'] == name)


def test_catalog_is_public_transport_metadata_without_hidden_scenario_answers(client):
    response = client.get('/wearables/catalog')
    assert response.status_code == 200
    body = response.json()
    assert len(body['metrics']) == 30 and len(body['scenarios']) == 10
    assert body['is_simulated'] is True
    assert set(body) == {'metrics', 'scenarios', 'is_simulated'}
    assert all(set(item) == {'scenario_id', 'label'} for item in body['scenarios'])
    assert all(item['label'].startswith('模拟样本') for item in body['scenarios'])
    assert 'expected' not in response.text and 'evaluation' not in response.text


def test_simulation_import_is_idempotent_not_an_authorization(client):
    _, base = open_session(client)
    operation = str(uuid4())
    first = simulate(client, base, operation_id=operation)
    assert first.status_code == 200 and first.json()['revision'] == 1
    assert first.json()['accepted'] == 21 * 4
    assert simulate(client, base, operation_id=operation).json() == first.json()
    assert simulate(client, base, operation_id=operation, seed=99).status_code == 409
    assert simulate(client, base, seed=100).status_code == 409
    state = client.get(base+'/support-state').json()
    assert state['wearables']['revision'] == 1
    provider = state['wearables']['providers'][0]
    assert provider['provider'] == 'mock' and provider['record_count'] == 84
    assert provider['allowed_metrics'] == provider['scopes'] == []
    assert provider['is_simulated'] is True
    assert set(provider['available_metrics']) == set(METRIC_REGISTRY)
    assert all(s['status'] == 'not_authorized' and not s['points'] for s in trends(client, base).json()['series'])


def test_trends_keep_units_dates_and_use_only_local_scope(client):
    _, base = open_session(client)
    assert simulate(client, base).status_code == 200
    consent(client, base, scopes=['model_context'])
    assert all(not s['points'] for s in trends(client, base).json()['series'])
    consent(client, base, revision=2, metrics=['sleep_duration_minutes', 'night_hrv_rmssd_ms', 'resting_heart_rate_bpm'])
    response = trends(client, base)
    assert response.status_code == 200
    body = response.json()
    assert body['revision'] == 3 and body['scope'] == 'local_analysis'
    sleep = series(body, 'sleep_duration_minutes')
    assert sleep['unit'] == 'min' and sleep['status'] == 'ready'
    assert len(sleep['points']) == 14
    assert sleep['points'][0]['date'] == '2026-10-22' and sleep['points'][-1]['date'] == '2026-11-04'
    assert all(400 < p['value'] < 500 and p['quality'] == 'valid' for p in sleep['points'])
    hrv = series(body, 'night_hrv_rmssd_ms')
    assert hrv['unit'] == 'ms' and hrv['source']['measurement_method'] == 'rmssd'
    assert hrv['source']['is_simulated'] is True
    assert series(body, 'steps')['status'] == 'not_authorized'


def test_derived_metric_requires_all_dependency_permissions(client):
    _, base = open_session(client)
    simulate(client, base)
    consent(client, base, metrics=['steps'])
    item = series(trends(client, base).json(), 'steps')
    assert item['status'] == 'not_authorized' and item['points'] == []
    consent(client, base, revision=2, metrics=['steps', 'active_energy_kcal', 'sedentary_minutes'])
    item = series(trends(client, base).json(), 'steps')
    assert item['status'] == 'ready' and all(p['value'] > 0 for p in item['points'])


def test_revocation_and_separate_session_hide_trend_values(client):
    _, base = open_session(client)
    _, other = open_session(client)
    simulate(client, base)
    consent(client, base)
    assert series(trends(client, base).json(), 'sleep_duration_minutes')['status'] == 'ready'
    assert client.get(other+'/support-state').json()['wearables']['record_count'] == 0
    assert all(not s['points'] for s in trends(client, other).json()['series'])
    consent(client, base, revision=2, metrics=[], scopes=[])
    body = trends(client, base).json()
    assert body['revision'] == 3
    assert all(not s['points'] and s['source'] is None for s in body['series'])


def test_missing_readings_remain_null_not_zero(client):
    _, base = open_session(client)
    simulate(client, base, scenario='S07')
    consent(client, base)
    item = series(trends(client, base, days=7).json(), 'resting_heart_rate_bpm')
    assert item['status'] == 'ready'
    assert all(p['value'] is not None for p in item['points'][:-3])
    assert all(p['value'] is None and p['quality'] == 'missing' for p in item['points'][-3:])
    steps = series(trends(client, base, days=7).json(), 'steps')
    assert all(p['value'] is None and p['quality'] == 'incomplete' for p in steps['points'][-3:])


@pytest.mark.parametrize('scenario', ['S04', 'S08'])
def test_changed_timezone_or_device_is_not_stitched_into_one_curve(client, scenario):
    _, base = open_session(client)
    simulate(client, base, scenario=scenario)
    consent(client, base)
    item = series(trends(client, base).json(), 'sleep_duration_minutes')
    assert item['status'] == 'source_conflict' and item['source'] is None
    assert all(p['value'] is None and p['record_id'] is None for p in item['points'])


def test_duplicate_day_observations_are_not_silently_averaged(client):
    _, base = open_session(client)
    simulate(client, base)
    assert simulate(client, base, seed=43, expected_revision=1).status_code == 200
    consent(client, base, revision=2)
    item = series(trends(client, base).json(), 'sleep_duration_minutes')
    assert item['status'] == 'source_conflict'


def test_future_ingestion_is_not_returned_before_its_as_of(client):
    _, base = open_session(client)
    simulate(client, base)
    consent(client, base)
    item = series(trends(client, base, as_of='2026-11-05T11:00:00+08:00').json(), 'sleep_duration_minutes')
    assert item['status'] == 'no_data' and all(p['value'] is None for p in item['points'])


@pytest.mark.parametrize('params', [{'days': 8}, {'timezone': 'Mars/Base'}, {'as_of': '2026-11-05T00:00:00'}, {'unlisted': 1}])
def test_invalid_query_is_rejected(client, params):
    _, base = open_session(client)
    assert trends(client, base, **params).status_code == 422


@pytest.mark.parametrize('change', [{'scenario_id': 'S99'}, {'days': 31}, {'seed': -1}, {'as_of': '2026-11-05'}, {'timezone': 'bad'}])
def test_invalid_simulation_is_rejected_without_mutation(client, change):
    _, base = open_session(client)
    assert simulate(client, base, **change).status_code == 422
    assert client.get(base+'/support-state').json()['wearables']['revision'] == 0


def test_controls_discover_pending_confirmed_and_drop_invalidated(client):
    _, base = open_session(client)
    report = client.post(base+'/reports', json={'content': '用户虚构自述'}).json()
    made = client.post(base+'/understandings', json={
        'interpretation': '等用户确认的理解', 'origin': 'user_report',
        'evidence_refs': [{'kind': 'user_report', 'source_id': report['report_id'], 'revision': 1}],
    }).json()
    assert client.get(base+'/understandings').json() == []
    controls = client.get(base+'/understandings/control')
    assert controls.status_code == 200 and controls.json() == [made]
    assert client.get(base+'/support-state').json()['understandings'] == [made]
    identity = made['understanding']['understanding_id']
    confirmed = client.post(base+f'/understandings/{identity}/confirm', json={
        'expected_revision': 1, 'confirmation_token': made['confirmation_token'],
    }).json()
    assert confirmed['understanding']['status'] == 'confirmed'
    assert client.get(base+'/support-state').json()['understandings'] == [confirmed]
    assert client.post(base+f"/reports/{report['report_id']}/delete", json={'expected_revision': 1}).status_code == 200
    assert client.get(base+'/understandings/control').json() == []


def test_state_poll_does_not_wait_for_active_lock_and_exposes_workflow(client):
    sid, base = open_session(client)
    record = main.session_service.require(UUID(sid))
    record.session.workflow.phase = WorkflowPhase.RUNNING
    run_id = uuid4()
    record.session.workflow.run_id = run_id
    record.support_mode = True
    record.support_route = 'clarify_safety'
    async def locked_read():
        async with record.lock:
            view = SupportViewService(main.session_service).state(UUID(sid))
            assert view.session.workflow.run_id == run_id
            assert view.support_mode is True and view.support_route == 'clarify_safety'
    asyncio.run(locked_read())
    record.lifecycle_pending += 1
    response = client.get(base+'/support-state')
    assert response.status_code == 200 and response.json()['session']['workflow']['phase'] == 'RUNNING'
    assert response.json()['understandings'] == []
    record.lifecycle_pending -= 1


def test_controls_dont_leak_invalid_source_text_or_cross_session_objects(client):
    sid, base = open_session(client)
    _, other = open_session(client)
    report = client.post(base+'/reports', json={'content': '待撤回的虚构自述'}).json()
    made = client.post(base+'/understandings', json={
        'interpretation': '不应继续出现的旧理解', 'origin': 'user_report',
        'evidence_refs': [{'kind': 'user_report', 'source_id': report['report_id'], 'revision': 1}],
    }).json()
    assert client.get(other+'/understandings/control').json() == []
    # Source validity is checked at read time even if a storage adapter made it stale.
    main.session_service.require(UUID(sid)).understandings.reports[report['report_id']].revision += 1
    response = client.get(base+'/support-state')
    assert response.json()['understandings'] == []
    assert made['understanding']['interpretation'] not in response.text


def test_derived_reading_with_low_quality_dependency_is_hidden(client):
    sid, base = open_session(client)
    simulate(client, base)
    consent(client, base)
    record = main.session_service.require(UUID(sid))
    daily = max((r for r in record.wearables.records.values() if r.record_type == 'daily'), key=lambda r: r.local_date)
    steps = next(m for m in daily.metrics if m.metric_id == 'steps')
    dependency = record.wearables.records[steps.dependency_refs[0].source_id]
    dependency.metrics[0].quality = 'invalid'
    dependency.metrics[0].quality_reasons = ['synthetic_bad_quality']
    item = series(trends(client, base).json(), 'steps')
    assert item['points'][-1]['value'] is None
    assert item['points'][-1]['quality'] == 'quality_insufficient'
    assert all(p['value'] is not None for p in item['points'][:-1])
