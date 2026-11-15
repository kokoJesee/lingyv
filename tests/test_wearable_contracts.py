"""Contract tests use fictional data only; no devices, models or clinical assertions."""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.schemas.wearable import (
    DeviceCapabilities, MetricValue, SleepEpisode, TimeWindow,
    WearableDailyRecord, WearableImportBatch,
)
from app.schemas.state_analysis import StateAnalysis
from app.schemas.understanding import Understanding
from app.wearables.metric_registry import METRIC_REGISTRY

EXAMPLES = Path(__file__).resolve().parents[1] / 'examples' / 'wearable'


def example(name):
    return json.loads((EXAMPLES / f'{name}.json').read_text())


def metric(name='steps'):
    return next(m for m in example('batch')['daily'][0]['metrics'] if m['metric_id'] == name)


@pytest.mark.parametrize('name,model', [
    ('batch', WearableImportBatch), ('capabilities', DeviceCapabilities),
    ('analysis', StateAnalysis), ('understanding', Understanding),
])
def test_examples_roundtrip_and_schema_export(name, model):
    obj = model.model_validate(example(name))
    assert model.model_validate_json(obj.model_dump_json()) == obj
    assert model.model_json_schema()['additionalProperties'] is False


def test_registry_covers_exactly_documented_metrics_and_is_read_only():
    import re
    spec = (EXAMPLES.parents[1] / 'docs' / 'shenglei-wearable-metrics.md').read_text()
    documented = set(re.findall(r'^\| \d+ \| .*?`([^`]+)`', spec, re.M))
    assert set(METRIC_REGISTRY) == documented
    assert len(documented) == 30
    with pytest.raises(TypeError):
        METRIC_REGISTRY['extra'] = METRIC_REGISTRY['steps']


@pytest.mark.parametrize('name', list(METRIC_REGISTRY))
def test_each_metric_has_valid_example(name):
    assert MetricValue.model_validate(metric(name)).metric_id == name


@pytest.mark.parametrize('change', [
    {'metric_id': 'anxiety_score'}, {'unit': 'kcal'}, {'value_type': 'number'},
    {'value': '100'}, {'value': True}, {'value': 1.5}, {'value': -1},
    {'value': float('nan')}, {'value': float('inf')},
    {'value': None}, {'quality': 'missing'}, {'quality': 'unsupported'},
    {'quality': 'unknown'}, {'coverage': 1.1}, {'coverage': '1'},
    {'measurement_kind': 'vendor_score'}, {'hidden_expected_emotion': 'sad'},
    {'score_origin': 'fixture'},
])
def test_reject_bad_metric(change):
    data = metric(); data.update(change)
    with pytest.raises(ValidationError):
        MetricValue.model_validate(data)


@pytest.mark.parametrize('quality', ['missing', 'invalid', 'unknown', 'unsupported'])
def test_explicit_missing_is_not_zero(quality):
    data = metric(); data.update(value=None, quality=quality, quality_reasons=['not_worn'])
    assert MetricValue.model_validate(data).value is None
    data.update(value=0, quality='valid', quality_reasons=[])
    assert MetricValue.model_validate(data).value == 0


@pytest.mark.parametrize('value', ['2026-11-03T08:00:00', '1760000000', 1760000000, True])
def test_timestamp_metric_requires_iso_offset(value):
    data = metric('sleep_onset_at'); data['value'] = value
    with pytest.raises(ValidationError):
        MetricValue.model_validate(data)


@pytest.mark.parametrize('change', [
    {'measurement_method': 'sdnn'}, {'window_seconds': None}, {'window_seconds': 0},
    {'measurement_method': 'unknown'},
])
def test_hrv_not_interchangeable(change):
    data = metric('night_hrv_rmssd_ms'); data.update(change)
    with pytest.raises(ValidationError):
        MetricValue.model_validate(data)


def test_unknown_hrv_kept_but_not_valid():
    data = metric('night_hrv_rmssd_ms')
    data.update(measurement_method='unknown', quality='unknown', quality_reasons=['method_not_reported'])
    assert MetricValue.model_validate(data).quality == 'unknown'


@pytest.mark.parametrize('field', ['baseline_ref', 'measurement_site'])
def test_temperature_requires_reference_and_site(field):
    data = metric('skin_temp_deviation_c'); data[field] = None
    with pytest.raises(ValidationError):
        MetricValue.model_validate(data)


@pytest.mark.parametrize('change', [
    {'value': 101.0}, {'namespace': None}, {'score_scale': None}, {'score_origin': None},
    {'score_scale': {'minimum': 10, 'maximum': 10, 'direction': 'unspecified'}},
])
def test_score_requires_own_scale(change):
    data = metric('vendor.sleep_score'); data.update(change)
    with pytest.raises(ValidationError):
        MetricValue.model_validate(data)


def test_scores_can_use_different_vendor_scales():
    data = metric('vendor.sleep_score')
    data.update(value=4.0, score_scale={'minimum': 1, 'maximum': 5, 'direction': 'higher_is_more'})
    assert MetricValue.model_validate(data).value == 4.0


def test_derived_statistic_requires_dependencies():
    data = metric('sleep_efficiency_pct'); data['dependency_refs'] = []
    with pytest.raises(ValidationError):
        MetricValue.model_validate(data)


@pytest.mark.parametrize('change', [
    {'timezone': 'Invalid/Zone'}, {'observed_end': '2026-11-04T00:00:00'},
    {'ingested_at': 12345678}, {'observed_start': '2026-11-05T00:00:00+08:00'},
    {'ingested_at': '2026-11-03T00:00:00+08:00'},
    {'local_date': '2026-11-01'}, {'session_id': 'someone-else'},
    {'is_simulated': False}, {'record_revision': 0}, {'record_revision': True},
    {'is_simulated': 'true'},
])
def test_record_rejects_invalid_times_ownership_and_origin(change):
    data = example('batch')['daily'][0]; data.update(change)
    with pytest.raises(ValidationError):
        WearableDailyRecord.model_validate(data)


def test_duplicate_metrics_and_outside_windows_rejected():
    data = example('batch')['daily'][0]
    data['metrics'].append(deepcopy(data['metrics'][0]))
    with pytest.raises(ValidationError):
        WearableDailyRecord.model_validate(data)
    data['metrics'] = [metric()]
    data['metrics'][0]['window']['end'] = '2026-11-05T00:00:00+08:00'
    with pytest.raises(ValidationError):
        WearableDailyRecord.model_validate(data)


@pytest.mark.parametrize('local_date,start,end,hours', [
    ('2026-03-08', '2026-03-08T00:00:00-05:00', '2026-03-09T00:00:00-04:00', 23),
    ('2026-11-01', '2026-11-01T00:00:00-04:00', '2026-11-02T00:00:00-05:00', 25),
])
def test_dst_complete_days_do_not_assume_24_hours(local_date, start, end, hours):
    data = example('batch')['daily'][0]
    data.update(timezone='America/New_York', local_date=local_date, observed_start=start,
                observed_end=end, ingested_at=end, metrics=[metric()])
    data['metrics'][0]['window'] = {'start': start, 'end': end}
    parsed = WearableDailyRecord.model_validate(data)
    assert (parsed.observed_end - parsed.observed_start).total_seconds() == hours * 3600


def test_native_zoneinfo_elapsed_time_preserves_dst_offset():
    zone = ZoneInfo('America/New_York')
    window = TimeWindow(start=datetime(2026, 3, 8, 0, tzinfo=zone), end=datetime(2026, 3, 9, 0, tzinfo=zone))
    assert (window.end - window.start).total_seconds() == 23 * 3600


@pytest.mark.parametrize('change', [
    {'local_date': '2026-11-02'}, {'stages': []},
    {'sleep_onset_at': '2026-11-04T00:00:00+08:00'},
])
def test_bad_sleep_structure(change):
    data = example('batch')['sleeps'][0]; data.update(change)
    with pytest.raises(ValidationError):
        SleepEpisode.model_validate(data)


def test_sleep_stage_total_must_match_summary():
    data = example('batch')['sleeps'][0]; data['metrics'][0]['value'] = 500.0
    with pytest.raises(ValidationError):
        SleepEpisode.model_validate(data)


def test_sleep_stage_overlap_rejected():
    data = example('batch')['sleeps'][0]
    data['stages'].append(deepcopy(data['stages'][0]))
    with pytest.raises(ValidationError):
        SleepEpisode.model_validate(data)


def test_batch_limits_and_duplicate_identity():
    data = example('batch'); data['daily'].append(deepcopy(data['daily'][0]))
    with pytest.raises(ValidationError):
        WearableImportBatch.model_validate(data)
    data['daily'] *= 31
    with pytest.raises(ValidationError):
        WearableImportBatch.model_validate(data)
    with pytest.raises(ValidationError):
        WearableImportBatch.model_validate({})


def test_capabilities_must_cover_all_metrics():
    data = example('capabilities'); data['capabilities'].pop()
    with pytest.raises(ValidationError):
        DeviceCapabilities.model_validate(data)


@pytest.mark.parametrize('change', [
    {'delta': -80.0}, {'relative_delta': -0.1}, {'unit': 'minute'},
    {'baseline_valid_days': 100}, {'record_refs': []},
    {'baseline_window': {'start': '2026-11-01', 'end': '2026-11-03'}},
])
def test_analysis_rejects_inconsistent_statistics(change):
    data = example('analysis'); data['observations'][0].update(change)
    with pytest.raises(ValidationError):
        StateAnalysis.model_validate(data)


def test_temperature_cannot_have_relative_delta():
    data = example('analysis'); data['observations'][0].update(
        metric_id='skin_temp_deviation_c', unit='degC_delta', baseline_ref='baseline-1')
    with pytest.raises(ValidationError):
        StateAnalysis.model_validate(data)
    data['observations'][0]['relative_delta'] = None
    assert StateAnalysis.model_validate(data).observations[0].relative_delta is None


def test_unavailable_analysis_requires_reason_and_no_observation():
    data = example('analysis'); data['status'] = 'insufficient_data'
    with pytest.raises(ValidationError):
        StateAnalysis.model_validate(data)
    data.update(observations=[], limitations=['Not enough valid days'])
    assert StateAnalysis.model_validate(data).status == 'insufficient_data'


@pytest.mark.parametrize('change', [
    {'status': 'confirmed'}, {'confirmed_revision': 1},
    {'status': 'invalidated'}, {'origin': 'user_report'}, {'evidence_refs': []},
    {'updated_at': '2026-11-01T00:00:00+08:00'}, {'memory_update': 'write it'},
])
def test_understanding_rejects_unbound_confirmation_and_mixed_authority(change):
    data = example('understanding'); data.update(change)
    with pytest.raises(ValidationError):
        Understanding.model_validate(data)


def test_confirmation_binds_current_revision():
    data = example('understanding'); data.update(status='confirmed', confirmed_revision=1, confirmed_at=data['updated_at'])
    assert Understanding.model_validate(data).confirmed_revision == 1
    data['revision'] = 2
    with pytest.raises(ValidationError):
        Understanding.model_validate(data)


@pytest.mark.parametrize('name,model', [
    ('wearable-batch', WearableImportBatch), ('wearable-capabilities', DeviceCapabilities),
    ('state-analysis', StateAnalysis), ('understanding', Understanding),
])
def test_exported_schemas_stay_in_sync(name, model):
    path = EXAMPLES.parents[1] / 'docs' / 'schemas' / f'{name}.schema.json'
    assert json.loads(path.read_text()) == model.model_json_schema()


def test_partial_stages_cannot_exceed_total_sleep():
    data = example('batch')['sleeps'][0]
    data['stages_complete'] = False
    data['metrics'][0]['value'] = 400.0
    with pytest.raises(ValidationError):
        SleepEpisode.model_validate(data)


def test_malformed_metric_id_produces_validation_error():
    data = metric(); data['metric_id'] = []
    with pytest.raises(ValidationError):
        MetricValue.model_validate(data)
