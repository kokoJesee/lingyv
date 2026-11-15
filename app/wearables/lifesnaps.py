"""Map curated LifeSnaps research measurements onto a replay calendar."""
import csv
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from app.schemas.wearable import MetricValue, WearableDailyRecord, WearableImportBatch
from app.wearables.metric_registry import get_metric

CASES = ('LS01', 'LS02', 'LS03', 'LS04')

PROVIDER = 'lifesnaps_v4_replay'
REPLAY_START = datetime(2022, 1, 1, tzinfo=timezone.utc)
REPLAY_AS_OF = REPLAY_START + timedelta(days=21)
SLEEP = 'sleep_duration_minutes'
COLUMNS = {
    SLEEP: 'sleep_minutes',
    'sleep_latency_minutes': 'minutes_to_fall_asleep',
    'resting_heart_rate_bpm': 'resting_hr_bpm',
    'steps': 'steps',
    'sedentary_minutes': 'sedentary_minutes',
    'night_resp_rate_mean': 'sleep_respiration_breaths_per_min',
    'night_spo2_mean_pct': 'spo2_pct',
}


REPLAY_TREND_METRICS = tuple(metric for metric in COLUMNS if metric != "sleep_latency_minutes")

def check(condition, description):
    if not condition:
        raise ValueError(description)


def load_batches(path: Path, *, as_of=REPLAY_AS_OF, timezone_name='UTC') -> dict[str, WearableImportBatch]:
    zone = ZoneInfo(timezone_name)
    end_day = as_of.astimezone(zone).date()
    by_case = defaultdict(dict)
    with path.open(newline='', encoding='utf-8-sig') as handle:
        rows = csv.DictReader(handle)
        required = {'case_id', 'day', *COLUMNS.values()}
        check(required <= set(rows.fieldnames or ()), 'CSV lacks required device columns')
        for row in rows:
            case, day = row['case_id'], int(row['day'])
            check(case in CASES and 1 <= day <= 21, 'Unknown case or replay day')
            check(day not in by_case[case], 'Duplicate case day')
            by_case[case][day] = row

    check(set(by_case) == set(CASES), 'Expected the four curated LifeSnaps cases')
    batches = {}
    for case, days in sorted(by_case.items()):
        check(set(days) == set(range(1, 22)), f'{case}: expected 21 calendar days')
        daily = []
        for day, row in sorted(days.items()):
            start = datetime.combine(end_day - timedelta(days=22 - day), datetime.min.time(), zone)
            end = start + timedelta(days=1)
            record_id = f'{case}-day-{day:02d}'
            metrics = []
            for metric, column in COLUMNS.items():
                definition = get_metric(metric)
                text = (row[column] or '').strip()
                value = float(text) if text else None
                if value is not None and definition.value_type == 'integer':
                    check(value.is_integer(), f'{case} day {day}: {column} must be a count')
                    value = int(value)
                metrics.append(MetricValue(
                    metric_id=metric, value_type=definition.value_type, value=value,
                    unit=definition.unit, measurement_kind=definition.measurement_kind,
                    measurement_method='fitbit_daily_export', aggregation_method='published_daily_summary',
                    window={'start': start, 'end': end}, source_algorithm_version='lifesnaps_v4',
                    quality='missing' if value is None else 'valid',
                    quality_reasons=['source_csv_empty'] if value is None else [],
                ))
            daily.append(WearableDailyRecord(
                record_id=record_id, record_revision=1, provider_record_id=record_id,
                provider=PROVIDER, device_id=f'fitbit-sense-{case}', is_simulated=False,
                timezone=timezone_name, local_date=start.date(), day_complete=True,
                observed_start=start, observed_end=end, ingested_at=as_of, metrics=metrics,
            ))
        batches[case] = WearableImportBatch(daily=daily)
    return batches
