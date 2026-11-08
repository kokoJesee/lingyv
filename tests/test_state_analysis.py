import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.agent.errors import RuntimeFailure
from app.schemas.wearable import WearableDailyRecord
from app.schemas.wearable_service import SetWearableConsent
from app.services.state_analysis import analyze_changes, StateAnalysisService, AnalysisConfig
from app.services.wearable_service import WearableSnapshot, WearableService
from app.services.session_service import SessionService
from app.wearables.mock import MockWearableAdapter
from app.wearables.base import MockQuery

AS_OF = '2026-11-04T00:00:00+08:00'


def fixture(metric='sleep_duration_minutes', before=450, after=360):
    example = json.loads((Path(__file__).parents[1]/'examples/wearable/batch.json').read_text())['daily'][0]
    template = next(m for m in example['metrics'] if m['metric_id'] == metric)
    records = []
    zone = timezone(timedelta(hours=8))
    for i in range(17):
        start = datetime(2026, 10, 18, tzinfo=zone)+timedelta(days=i)
        end = start+timedelta(days=1)
        value = before if i < 14 else after
        metric_start = start
        if metric == 'sleep_onset_at':
            value = start+timedelta(minutes=value)
            metric_start = start-timedelta(hours=12)
        m = {**template, 'value': value, 'window': {'start': metric_start, 'end': end}}
        records.append(WearableDailyRecord(**{**example, 'record_id': f'day-{i}', 'provider_record_id': f'day-{i}',
            'local_date': start.date(), 'observed_start': metric_start, 'observed_end': end,
            'ingested_at': end, 'metrics': [m]}))
    return WearableSnapshot(uuid4(), 1, 'local_analysis', tuple(records))


def result(snapshot, **kwargs):
    return analyze_changes(snapshot, as_of=AS_OF, **kwargs)


def observation(snapshot, **kwargs):
    return result(snapshot, **kwargs).observations[0]


def metric_status(analysis, name='sleep_duration_minutes'):
    return next(s for s in analysis.metric_statuses if s.metric_id == name)


def test_hand_calculated_medians_windows_refs_and_repeatability():
    snapshot = fixture()
    o = observation(snapshot)
    assert (o.baseline_median, o.recent_median, o.delta, o.relative_delta) == (450, 360, -90, -.2)
    assert (o.baseline_valid_days, o.recent_valid_days) == (14, 3)
    assert str(o.baseline_window.start) == '2026-10-18'
    assert str(o.recent_window.start) == '2026-11-01'
    assert len(o.record_refs) == 17
    assert o.inquiry_candidate and o.evidence_group == 'sleep'
    assert result(snapshot).model_dump_json() == result(snapshot).model_dump_json()


def test_median_not_mean_and_even_sample():
    snapshot = fixture()
    snapshot.records[0].metrics[0].value = 900
    assert observation(snapshot).baseline_median == 450
    for r in snapshot.records[:7]: r.metrics[0].value = 400
    assert observation(snapshot).baseline_median == 425


@pytest.mark.parametrize('metric,before,after,relative,candidate', [
    ('steps', 5000, 6500, .3, True), ('steps', 1000, 1400, .4, False),
    ('steps', 10000, 12000, .2, False), ('steps', 0, 2000, None, False),
    ('skin_temp_deviation_c', -.1, .4, None, False),
    ('sleep_onset_at', -10, 10, None, False),
])
def test_units_zero_baseline_midnight_and_inquiry_rules(metric, before, after, relative, candidate):
    o = observation(fixture(metric, before, after))
    assert o.delta == pytest.approx(after-before)
    assert o.relative_delta == relative
    assert o.inquiry_candidate is candidate
    if metric == 'sleep_onset_at': assert o.unit == 'min_from_sleep_day' and o.delta == 20


@pytest.mark.parametrize('field,value', [
    ('device_id', 'new-device'), ('timezone', 'Asia/Tokyo'),
    ('provider', 'another-provider'), ('is_simulated', False),
])
def test_record_source_changes_not_combined(field, value):
    snapshot = fixture()
    setattr(snapshot.records[-1], field, value)
    assert result(snapshot).status == 'source_conflict'


@pytest.mark.parametrize('field,value', [
    ('measurement_method', 'other'), ('aggregation_method', 'other'),
    ('source_algorithm_version', 'v2'), ('window_seconds', 600),
    ('baseline_ref', 'new-reference'), ('measurement_site', 'finger'),
])
def test_measurement_semantics_not_combined(field, value):
    snapshot = fixture('skin_temp_deviation_c', .1, .2)
    setattr(snapshot.records[-1].metrics[0], field, value)
    assert result(snapshot).status == 'source_conflict'


def test_duplicate_date_not_extra_evidence():
    s = fixture()
    duplicate = s.records[-1].model_copy(deep=True)
    duplicate.record_id = 'duplicate'
    duplicated = WearableSnapshot(s.session_id, 1, s.scope, s.records+(duplicate,))
    assert result(duplicated).status == 'source_conflict'


def test_insufficient_baseline_recent_and_no_data():
    s = fixture()
    short = WearableSnapshot(s.session_id, 1, s.scope, s.records[5:])
    assert metric_status(result(short)).status == 'baseline_insufficient'
    short = WearableSnapshot(s.session_id, 1, s.scope, s.records[:-2])
    assert metric_status(result(short)).status == 'recent_insufficient'
    empty = WearableSnapshot(s.session_id, 0, s.scope, ())
    assert metric_status(result(empty)).status == 'no_data'
    assert result(empty).evidence_revision == 0


@pytest.mark.parametrize('change', ['missing', 'incomplete', 'coverage', 'unfinished', 'future_ingestion'])
def test_quality_and_future_data_excluded(change):
    s = fixture()
    for r in s.records[-2:]:
        if change in ('missing', 'incomplete'): r.metrics[0].quality = change
        elif change == 'coverage': r.metrics[0].coverage = .2
        elif change == 'unfinished': r.day_complete = False
        else: r.ingested_at = datetime.fromisoformat(AS_OF)+timedelta(days=1)
    state = metric_status(result(s))
    assert state.status == 'quality_insufficient'
    assert state.recent_valid_days == 1 and state.excluded_days == 2


def test_old_records_do_not_expand_baseline():
    s = fixture()
    old = s.records[0].model_copy(deep=True)
    old.local_date -= timedelta(days=30)
    old.record_id = 'ancient'
    short = WearableSnapshot(s.session_id, 1, s.scope, s.records[5:]+(old,))
    assert metric_status(result(short)).baseline_valid_days == 9
    assert result(short).status == 'insufficient_data'


def test_config_and_result_identity():
    s = fixture()
    with pytest.raises(ValueError): AnalysisConfig(baseline_days=5)
    with pytest.raises(ValueError): AnalysisConfig(timezone='broken/zone')
    with pytest.raises(ValueError): analyze_changes(s, as_of='2026-11-04')
    assert result(s).analysis_id != result(s, config=AnalysisConfig(sleep_inquiry_minutes=100.)).analysis_id
    assert not observation(s, config=AnalysisConfig(sleep_inquiry_minutes=100.)).inquiry_candidate


@pytest.mark.parametrize('scenario', ['S01', 'S02', 'S07', 'S08', 'S09'])
def test_generator_service_integration_and_authorization(scenario):
    async def run():
        sessions = SessionService(); sid = sessions.create().session_id
        data = WearableService(sessions); analysis = StateAnalysisService(data)
        first = await analysis.analyze(sid, as_of=AS_OF)
        assert first.status == 'not_authorized' and first.evidence_revision == 0
        await MockWearableAdapter().import_into(data, sid, MockQuery(as_of=AS_OF, scenario_id=scenario), expected_revision=0, operation_id=uuid4())
        from app.wearables.metric_registry import METRIC_REGISTRY
        await data.set_consent(sid, SetWearableConsent(expected_revision=1, provider='mock', allowed_metrics=list(METRIC_REGISTRY), scopes=['local_analysis']))
        local = await analysis.analyze(sid, as_of=AS_OF)
        assert (await analysis.analyze(sid, as_of=AS_OF, scope='model_context')).status == 'not_authorized'
        assert local.status == ('source_conflict' if scenario == 'S08' else 'ready')
        if scenario == 'S02': assert next(o for o in local.observations if o.metric_id == 'sleep_duration_minutes').inquiry_candidate
        if scenario == 'S07': assert metric_status(local, 'steps').status == 'quality_insufficient'
        if scenario == 'S01':
            steps = next(o for o in local.observations if o.metric_id == 'steps')
            assert len(steps.record_refs) == 34  # 17 summaries + 17 activity sources
        analysis.validate_result(sid, local)
        with pytest.raises(RuntimeFailure): analysis.validate_result(sid, local, scope='model_context')
        other = sessions.create().session_id
        with pytest.raises(RuntimeFailure): analysis.validate_result(other, local)
        await data.set_consent(sid, SetWearableConsent(expected_revision=2, provider='mock', allowed_metrics=[], scopes=[]))
        with pytest.raises(RuntimeFailure): analysis.validate_result(sid, local)
    asyncio.run(run())


def test_dependency_quality_and_future_ingestion_not_trusted():
    from app.schemas.wearable import EvidenceReference
    s = fixture()
    source = s.records[0].model_copy(deep=True)
    source.record_id = 'source'
    # Dependency can be outside the comparison calendar; it must still be available as of analysis.
    source.local_date -= timedelta(days=60)
    s.records[-1].metrics[0].dependency_refs = [EvidenceReference(kind='wearable_record', source_id='source', revision=1)]
    s = WearableSnapshot(s.session_id, 1, s.scope, s.records+(source,))
    source.metrics[0].quality = 'missing'
    assert metric_status(result(s)).status == 'quality_insufficient'
    source.metrics[0].quality = 'valid'
    source.ingested_at = datetime.fromisoformat(AS_OF)+timedelta(days=1)
    assert metric_status(result(s)).status == 'quality_insufficient'


def test_analysis_cli(tmp_path):
    from app.analysis_cli import main
    output = tmp_path/'analysis.json'
    assert main(['--as-of', AS_OF, '--output', str(output)]) == 0
    value = json.loads(output.read_text())
    assert value['status'] == 'ready' and value['scope'] == 'local_analysis'
    assert len(value['observations']) == 9
    with pytest.raises(SystemExit): main(['--as-of', AS_OF, '--output', str(output)])
