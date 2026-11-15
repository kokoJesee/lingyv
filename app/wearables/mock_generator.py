"""Synthetic product fixtures. No medical thresholds or proprietary scoring algorithms."""
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
import hashlib
import json
from pathlib import Path
import random
from zoneinfo import ZoneInfo

from app.schemas.wearable import (
    DeviceCapabilities, MetricValue, WearableDailyRecord, WearableIntervalRecord,
    WearableImportBatch, SleepEpisode, WorkoutEvent,
)
from app.wearables.base import MockQuery
from app.wearables.metric_registry import METRIC_REGISTRY

GENERATOR_VERSION = 'mock_wearable.v1'
UTC = timezone.utc
CATALOG = Path(__file__).resolve().parents[2] / 'examples/wearable/scenarios/catalog.json'
BASIC_UNSUPPORTED = {
    'night_hrv_rmssd_ms', 'hrv_sdnn_ms', 'night_resp_rate_mean', 'night_spo2_mean_pct',
    'skin_temp_deviation_c', 'vendor.readiness_score', 'vendor.physiological_stress_score',
    'vendor.energy_reserve_score', 'vendor.stress_duration_minutes',
}
SLEEP_IDS = tuple(METRIC_REGISTRY)[:11]


def minutes(start, end):
    return (end.astimezone(UTC) - start.astimezone(UTC)).total_seconds() / 60


def advance(value, amount):
    return (value.astimezone(UTC) + timedelta(minutes=amount)).astimezone(value.tzinfo)


def midnight(day, zone):
    return datetime.combine(day, time(), ZoneInfo(zone))


@dataclass
class GeneratedWearables:
    batch: WearableImportBatch
    capabilities: list[DeviceCapabilities]
    manifest: dict  # Replay-only: do not forward to Agent
    evaluation: dict  # Hidden scenario/user-report fixture, never part of batch


class _Builder:
    def __init__(self, query):
        self.q = MockQuery.model_validate_json(query.model_dump_json())
        self.rng = random.Random(self.q.seed)
        fingerprint = GENERATOR_VERSION + self.q.model_dump_json()
        self.prefix = hashlib.sha256(fingerprint.encode()).hexdigest()[:20]
        self.devices = {}

    def identity(self, kind, day, suffix=''):
        return f'mock-{self.prefix}-{kind}-{day}{suffix}'

    def supported(self, name, switched):
        if self.q.capability_profile == 'mock_basic_band' and name in BASIC_UNSUPPORTED:
            return False
        if name == 'night_hrv_rmssd_ms' and switched:
            return False
        if name == 'hrv_sdnn_ms' and not switched and self.q.capability_profile == 'mock_advanced_wearable':
            return False
        return True

    def scale(self, name, switched):
        return dict(minimum=0.0, maximum=5.0 if switched else 100.0,
                    direction='unspecified')

    def metric(self, name, value, start, end, switched=False, quality='valid', reason=None, refs=()):
        definition = METRIC_REGISTRY[name]
        if not self.supported(name, switched):
            value, quality, reason = None, 'unsupported', 'capability_not_supported'
        kwargs = dict(metric_id=name, value_type=definition.value_type, value=value, unit=definition.unit,
                      measurement_kind=definition.measurement_kind,
                      measurement_method=definition.required_method or 'mock_estimate.v1',
                      aggregation_method='fixture_summary', window=dict(start=start, end=end),
                      source_algorithm_version='mock.v2' if switched else 'mock.v1',
                      quality=quality, quality_reasons=[reason] if reason else [],
                      coverage=0.0 if value is None else (0.5 if quality == 'incomplete' else 1.0),
                      dependency_refs=[dict(kind='wearable_record', source_id=ref, revision=1) for ref in refs])
        if definition.required_method:
            kwargs.update(window_seconds=300.0, aggregation_method='mean_of_5min_windows')
        if definition.baseline_required and value is not None:
            kwargs.update(baseline_ref='mock-temperature-reference-v2' if switched else 'mock-temperature-reference-v1', measurement_site='wrist')
        if name.startswith('vendor.'):
            kwargs['namespace'] = 'mock.fixture.v2' if switched else 'mock.fixture.v1'
        if definition.measurement_kind == 'vendor_score':
            kwargs.update(score_origin='fixture', score_scale=self.scale(name, switched))
        return MetricValue(**kwargs)

    def common(self, kind, day, zone, start, end, switched, metrics, suffix=''):
        device = 'mock-device-2' if switched else 'mock-device-1'
        self.devices[device] = switched
        identity = self.identity(kind, day, suffix)
        return dict(record_id=identity, provider_record_id=identity, record_revision=1, provider='mock',
                    device_id=device, is_simulated=True, timezone=zone, observed_start=start,
                    observed_end=end, ingested_at=self.q.as_of, metrics=metrics)

    def day(self, day, zone, recent, switched, with_intervals):
        q, rng = self.q, self.rng
        scenario = q.scenario_id if recent else 'S01'
        missing = recent and (q.variant == 'missing' or scenario == 'S07')
        start, end = midnight(day, zone), midnight(day + timedelta(days=1), zone)
        wake = datetime.combine(day, time(7), ZoneInfo(zone))
        sleep_minutes = 450 + rng.randint(-15, 15) - (90 if scenario == 'S02' else 0)
        waso = 80 if scenario == 'S05' else 15
        wake_count = 4 if scenario == 'S05' else 1
        latency, after_wake = 15, 10
        onset = advance(wake, -sleep_minutes-waso)
        bed, out = advance(onset, -latency), advance(wake, after_wake)
        deep, rem = int(sleep_minutes * .2), int(sleep_minutes * .23)
        light = sleep_minutes-deep-rem
        # Interleave distinct awake episodes; all durations derive from this timeline.
        parts = [('deep', deep), ('rem', rem)]
        for index in range(wake_count):
            parts += [('awake', waso/wake_count), ('light', light/wake_count)]
        cursor, stages = onset, []
        for stage, duration in parts:
            next_time = advance(cursor, duration)
            stages.append(dict(stage=stage, window=dict(start=cursor, end=next_time)))
            cursor = next_time
        # Keep supplied timezone offsets, perform elapsed arithmetic in UTC.
        sleep_values = dict(zip(SLEEP_IDS, [sleep_minutes, minutes(bed, out), onset, wake,
                          100*sleep_minutes/minutes(bed, out), latency, waso, deep, rem, light, wake_count]))
        sleep_metrics = [self.metric(name, sleep_values[name], bed, out, switched)
                         for name in SLEEP_IDS if name != 'sleep_efficiency_pct']
        if missing:
            # Partial staging is observed data, not the hidden complete generated timeline.
            stages = stages[1:]
            for m in sleep_metrics:
                if m.metric_id == 'deep_sleep_minutes':
                    m.value = None; m.quality = 'missing'; m.quality_reasons = ['partial_staging']; m.coverage = 0.0
        sleep = SleepEpisode(**self.common('sleep', day, zone, bed, out, switched, sleep_metrics),
                             sleep_kind='main', local_date=day, sleep_onset_at=onset, final_wake_at=wake,
                             stages_complete=not missing, stages=stages)
        workout_minutes = 60 if scenario == 'S03' else 20
        exercise_start = datetime.combine(day, time(18), ZoneInfo(zone))
        exercise_end = advance(exercise_start, workout_minutes)
        hr = 68.0 + rng.uniform(-2, 2)
        workout = WorkoutEvent(**self.common('workout', day, zone, exercise_start, exercise_end, switched,
                              [self.metric('day_hr_mean_bpm', hr+40, exercise_start, exercise_end, switched)]), activity='walk')
        steps = 6500 + rng.randint(-500, 500) + (3500 if scenario in ('S03', 'S04') else 0)
        energy = round(steps*.035 + workout_minutes*2, 2)
        sedentary = 420.0
        activity_values = {'steps': steps, 'active_energy_kcal': energy, 'sedentary_minutes': sedentary}
        if missing:
            activity_values['steps'] //= 2
            activity_values['active_energy_kcal'] /= 2
            activity_values['sedentary_minutes'] /= 2
        activity = WearableIntervalRecord(**self.common('activity', day, zone, start, end, switched,
            [self.metric(k, v, start, end, switched, 'incomplete' if missing else 'valid',
                         'partial_wear' if missing else None) for k, v in activity_values.items()]))
        night_hr = hr-7
        stress = (85.0 if scenario == 'S06' else 30.0) + rng.uniform(-2, 2)
        scale_divisor = 20 if switched else 1
        # Generate bounded 5-minute bins along UTC: DST days naturally have 276/300 bins.
        curves, day_hr_values = [], []
        current = start
        while current.astimezone(UTC) < end.astimezone(UTC):
            next_time = min(advance(current, 5), end)
            asleep = onset.astimezone(UTC) <= current.astimezone(UTC) < wake.astimezone(UTC)
            exercising = exercise_start <= current < exercise_end
            value = night_hr if asleep else (hr+40 if exercising else hr)
            absent = missing and current.hour < 12
            if not asleep and not absent:
                day_hr_values.append(value)
            if with_intervals:
                metric_id = 'night_hr_mean_bpm' if asleep else 'day_hr_mean_bpm'
                metrics = [self.metric(metric_id, None if absent else value, current, next_time, switched,
                                      'missing' if absent else 'valid', 'not_worn' if absent else None),
                           self.metric('vendor.physiological_stress_score', None if absent else stress/scale_divisor,
                                       current, next_time, switched, 'missing' if absent else 'valid', 'not_worn' if absent else None)]
                curves.append(WearableIntervalRecord(**self.common('bin', day, zone, current, next_time,
                              switched, metrics, suffix=f'-{len(curves):03}')))
            current = next_time
        values = {**sleep_values, **activity_values,
                  'resting_heart_rate_bpm': hr-5+(7 if scenario == 'S09' else 0),
                  'night_hr_mean_bpm': night_hr, 'night_hr_min_bpm': night_hr,
                  'day_hr_mean_bpm': sum(day_hr_values)/len(day_hr_values),
                  'night_hrv_rmssd_ms': 45+rng.uniform(-5, 5), 'hrv_sdnn_ms': 55+rng.uniform(-5, 5),
                  'night_resp_rate_mean': 15+(3 if scenario == 'S09' else 0),
                  'night_spo2_mean_pct': 97.0, 'skin_temp_deviation_c': .6 if scenario == 'S09' else rng.uniform(-.1, .1),
                  'exercise_minutes': float(workout_minutes), 'workout_count': 1,
                  'vendor.sleep_score': (65 if scenario == 'S05' else 80)/scale_divisor,
                  'vendor.readiness_score': 75/scale_divisor,
                  'vendor.physiological_stress_score': stress/scale_divisor,
                  'vendor.energy_reserve_score': 70/scale_divisor,
                  'vendor.stress_duration_minutes': 90.0 if scenario == 'S06' else 30.0}
        all_metrics = []
        for name, value in values.items():
            refs = ()
            if name == 'sleep_efficiency_pct': refs = (sleep.record_id,)
            if name in ('exercise_minutes', 'workout_count'): refs = (workout.record_id,)
            if name in activity_values: refs = (activity.record_id,)
            metric_start, metric_end = (bed, out) if name in SLEEP_IDS else (start, end)
            if name.startswith('night_') or name in ('resting_heart_rate_bpm', 'hrv_sdnn_ms', 'skin_temp_deviation_c'):
                metric_start, metric_end = onset, wake
            quality, reason = 'valid', None
            if missing and (name in activity_values or name == 'day_hr_mean_bpm'):
                quality, reason = 'incomplete', 'partial_wear'
            if missing and name in ('resting_heart_rate_bpm', 'night_hr_mean_bpm', 'night_hr_min_bpm',
                                    'night_hrv_rmssd_ms', 'hrv_sdnn_ms', 'night_spo2_mean_pct', 'deep_sleep_minutes'):
                value, quality, reason = None, 'missing', 'not_worn_or_sync_failed'
            all_metrics.append(self.metric(name, value, metric_start, metric_end, switched, quality, reason, refs))
        daily = WearableDailyRecord(**self.common('daily', day, zone, min(start, bed), end, switched, all_metrics),
                                   local_date=day, day_complete=True)
        return daily, sleep, workout, activity, curves

    def generate(self):
        q = self.q
        # Last date must be complete in BOTH zones for travel fixtures.
        zones = [q.timezone] + (['Asia/Tokyo'] if q.scenario_id == 'S04' else [])
        last = min(q.as_of.astimezone(ZoneInfo(z)).date() for z in zones) - timedelta(days=1)
        daily, sleeps, workouts, intervals = [], [], [], []
        for i in range(q.days):
            day = last-timedelta(days=q.days-i-1)
            recent = i >= max(0, q.days-3)
            zone = 'Asia/Tokyo' if q.scenario_id == 'S04' and recent else q.timezone
            switched = recent and (q.scenario_id == 'S08' or q.variant == 'source_conflict')
            d, s, w, a, curves = self.day(day, zone, recent, switched, q.include_intervals and i == q.days-1)
            daily.append(d); sleeps.append(s); workouts.append(w); intervals.extend([a, *curves])
        capabilities = []
        for device, switched in sorted(self.devices.items()):
            capabilities.append(DeviceCapabilities(capability_profile=q.capability_profile, device_id=device,
                capabilities=[dict(metric_id=name, supported=self.supported(name, switched), unit=definition.unit,
                    measurement_method=definition.required_method or 'mock_estimate.v1',
                    aggregation_method='mean_of_5min_windows' if definition.required_method else 'fixture_summary',
                    score_scale=self.scale(name, switched) if definition.measurement_kind == 'vendor_score' else None)
                    for name, definition in METRIC_REGISTRY.items()]))
        batch = WearableImportBatch(daily=daily, sleeps=sleeps, workouts=workouts, intervals=intervals)
        # Full serialization roundtrip validates even intermediate copied/mutated metric objects.
        batch = WearableImportBatch.model_validate_json(batch.model_dump_json())
        evaluation = json.loads(CATALOG.read_text(encoding='utf-8'))[q.scenario_id]
        evaluation = {**evaluation, 'family_id': f'wearable-{q.scenario_id}'}
        manifest = dict(generator_version=GENERATOR_VERSION, **q.model_dump(mode='json'),
                        family_id=evaluation['family_id'], is_simulated=True)
        return GeneratedWearables(batch, capabilities, manifest, evaluation)


def generate(query: MockQuery) -> GeneratedWearables:
    return _Builder(query).generate()
