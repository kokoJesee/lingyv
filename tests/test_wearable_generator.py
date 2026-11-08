import asyncio
from datetime import datetime
import json
from math import isclose
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.wearable import WearableImportBatch
from app.schemas.wearable_service import SetWearableConsent
from app.services.session_service import SessionService
from app.services.wearable_service import WearableService
from app.wearables.base import MockQuery
from app.wearables.mock import MockWearableAdapter
from app.wearables.mock_generator import generate, minutes
from app.wearables.metric_registry import METRIC_REGISTRY

PROFILES = ['mock_basic_band', 'mock_advanced_wearable', 'mock_research_combined']
SCENARIOS = [f'S{i:02}' for i in range(1, 11)]


def query(**kwargs):
    return MockQuery(**{'as_of': '2026-11-04T00:00:00+08:00', **kwargs})


def values(record):
    return {m.metric_id: m.value for m in record.metrics}


@pytest.mark.parametrize('scenario', SCENARIOS)
@pytest.mark.parametrize('profile', PROFILES)
def test_all_scenarios_profiles_validate_and_have_complete_sources(scenario, profile):
    result = generate(query(scenario_id=scenario, capability_profile=profile))
    batch = WearableImportBatch.model_validate_json(result.batch.model_dump_json())
    assert len(batch.daily) == 30
    assert len({d.local_date for d in batch.daily}) == 30
    all_records = batch.daily + batch.sleeps + batch.workouts + batch.intervals
    by_id = {r.record_id: r for r in all_records}
    caps = {c.device_id: {m.metric_id: m for m in c.capabilities} for c in result.capabilities}
    for record in all_records:
        assert record.is_simulated
        assert record.observed_end <= query().as_of
        for metric in record.metrics:
            if not caps[record.device_id][metric.metric_id].supported:
                assert metric.quality == 'unsupported' and metric.value is None
            for ref in metric.dependency_refs:
                assert ref.source_id in by_id
                assert by_id[ref.source_id].record_revision == ref.revision
    for d in batch.daily:
        assert set(values(d)) == set(METRIC_REGISTRY)
    encoded = batch.model_dump_json()
    for forbidden in ('scenario_id', 'family_id', 'expected_behavior', 'user_report', 'S01', 'S10'):
        assert forbidden not in encoded


@pytest.mark.parametrize('scenario', SCENARIOS)
@pytest.mark.parametrize('variant', ['missing', 'source_conflict'])
def test_variants_remain_valid_and_grouped_by_family(scenario, variant):
    result = generate(query(scenario_id=scenario, variant=variant))
    assert result.manifest['family_id'] == f'wearable-{scenario}'
    if variant == 'missing':
        latest = {m.metric_id: m for m in result.batch.daily[-1].metrics}
        assert latest['night_hr_mean_bpm'].value is None
        assert latest['steps'].quality == 'incomplete'
        assert not result.batch.sleeps[-1].stages_complete
    else:
        assert len(result.capabilities) == 2
        assert result.batch.daily[0].device_id != result.batch.daily[-1].device_id


def test_reproducible_seed_and_no_global_random_effect():
    import random
    random.seed(7)
    state = random.getstate()
    first = generate(query())
    second = generate(query())
    assert first.batch.model_dump_json() == second.batch.model_dump_json()
    assert first.manifest == second.manifest
    assert random.getstate() == state
    assert values(generate(query(seed=9)).batch.daily[0]) != values(first.batch.daily[0])


@pytest.mark.parametrize('scenario', ['S01', 'S02', 'S03', 'S05'])
def test_sleep_activity_identities_and_scene_changes(scenario):
    result = generate(query(scenario_id=scenario))
    for daily, sleep, workout in zip(result.batch.daily, result.batch.sleeps, result.batch.workouts):
        v = values(daily)
        assert isclose(v['deep_sleep_minutes']+v['rem_sleep_minutes']+v['light_sleep_minutes'], v['sleep_duration_minutes'])
        assert isclose(v['time_in_bed_minutes'], v['sleep_latency_minutes']+v['sleep_duration_minutes']+v['waso_minutes']+minutes(sleep.final_wake_at, sleep.observed_end))
        assert isclose(v['sleep_efficiency_pct'], 100*v['sleep_duration_minutes']/v['time_in_bed_minutes'])
        assert v['awakening_count'] == sum(s.stage == 'awake' for s in sleep.stages)
        assert v['exercise_minutes'] == minutes(workout.observed_start, workout.observed_end)
        assert v['workout_count'] == 1
        assert v['night_hr_min_bpm'] <= v['night_hr_mean_bpm']
    before, after = values(result.batch.daily[0]), values(result.batch.daily[-1])
    if scenario == 'S02': assert after['sleep_duration_minutes'] < before['sleep_duration_minutes']-50
    if scenario == 'S03': assert after['exercise_minutes'] > before['exercise_minutes']
    if scenario == 'S05': assert after['waso_minutes'] > before['waso_minutes']


@pytest.mark.parametrize('as_of,count', [('2026-03-09T00:00:00-04:00', 276), ('2026-11-02T00:00:00-05:00', 300)])
def test_dst_bins_and_sleep_elapsed(as_of, count):
    result = generate(query(as_of=as_of, timezone='America/New_York', days=1))
    bins = [r for r in result.batch.intervals if '-bin-' in r.record_id]
    assert len(bins) == count
    assert all(minutes(r.observed_start, r.observed_end) == 5 for r in bins)
    assert all(left.observed_end == right.observed_start for left, right in zip(bins, bins[1:]))
    s = result.batch.sleeps[0]
    assert isclose(sum(minutes(x.window.start, x.window.end) for x in s.stages), minutes(s.sleep_onset_at, s.final_wake_at))


def test_method_switch_and_scale_are_explicit():
    result = generate(query(scenario_id='S08', capability_profile='mock_advanced_wearable'))
    old = {m.metric_id: m for m in result.batch.daily[0].metrics}
    new = {m.metric_id: m for m in result.batch.daily[-1].metrics}
    assert old['night_hrv_rmssd_ms'].quality == 'valid'
    assert new['night_hrv_rmssd_ms'].quality == 'unsupported'
    assert old['hrv_sdnn_ms'].quality == 'unsupported'
    assert new['hrv_sdnn_ms'].measurement_method == 'sdnn'
    assert old['vendor.sleep_score'].score_scale.maximum == 100
    assert new['vendor.sleep_score'].score_scale.maximum == 5
    assert old['skin_temp_deviation_c'].baseline_ref != new['skin_temp_deviation_c'].baseline_ref


def test_help_scenario_does_not_encode_risk_in_device_values():
    ordinary, help_case = generate(query(scenario_id='S01')), generate(query(scenario_id='S10'))
    assert [values(r) for r in ordinary.batch.daily] == [values(r) for r in help_case.batch.daily]
    assert help_case.evaluation['user_report'] is not None
    assert help_case.evaluation['user_report'] not in help_case.batch.model_dump_json()


@pytest.mark.parametrize('change', [{'as_of': '2026-11-04'}, {'timezone': 'not/a/zone'}, {'days': 0}, {'days': 61}, {'seed': True}, {'scenario_id': 'S99'}])
def test_invalid_queries_rejected(change):
    with pytest.raises(ValidationError):
        query(**change)


def test_import_adapter_idempotent_and_does_not_grant_permissions():
    async def run():
        sessions = SessionService(); sid = sessions.create().session_id
        service = WearableService(sessions); adapter = MockWearableAdapter()
        operation_id = uuid4()
        receipt = await adapter.import_into(service, sid, query(), expected_revision=0, operation_id=operation_id)
        assert receipt['accepted'] == 408
        assert await adapter.import_into(service, sid, query(), expected_revision=0, operation_id=operation_id) == receipt
        assert not service.snapshot(sid, 'local_analysis').records
        await service.set_consent(sid, SetWearableConsent(expected_revision=1, provider='mock', allowed_metrics=list(METRIC_REGISTRY), scopes=['local_analysis']))
        assert len(service.snapshot(sid, 'local_analysis').records) == 408
        assert not service.snapshot(sid, 'model_context').records
    asyncio.run(run())


def test_export_and_no_overwrite(tmp_path, capsys):
    from app.wearables.__main__ import main
    output = tmp_path/'export'
    args = ['--as-of', '2026-11-04T00:00:00+08:00', '--output', str(output), '--import-demo']
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)['import_receipt']['accepted'] == 408
    batch = WearableImportBatch.model_validate_json((output/'batch.json').read_text(encoding='utf-8'))
    assert len(batch.daily) == 30
    assert (output/'evaluation-only.json').exists()
    with pytest.raises(SystemExit): main(args)
