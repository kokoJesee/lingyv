"""Deterministic personal changes, never mental-health or medical inference."""
from datetime import datetime, time, timedelta
import hashlib
import json
from statistics import median
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator

from app.schemas.state_analysis import StateAnalysis, StateObservation, MetricAvailability
from app.schemas.wearable import ContractModel, offset_datetime
from app.wearables.metric_registry import METRIC_REGISTRY

ALGORITHM_VERSION = 'personal_delta.v1'
CORE_METRICS = tuple(name for name, definition in METRIC_REGISTRY.items() if definition.usage == 'core')


class AnalysisConfig(ContractModel):
    timezone: str = 'Asia/Shanghai'
    baseline_days: int = Field(default=14, strict=True, ge=1, le=30)
    recent_days: int = Field(default=3, strict=True, ge=1, le=7)
    min_baseline_days: int = Field(default=10, strict=True, ge=1)
    min_recent_days: int = Field(default=2, strict=True, ge=1)
    minimum_coverage: float = Field(default=.8, strict=True, ge=0, le=1)
    sleep_inquiry_minutes: float = Field(default=60., strict=True, gt=0)
    steps_inquiry_absolute: float = Field(default=1500., strict=True, gt=0)
    steps_inquiry_relative: float = Field(default=.3, strict=True, gt=0)

    @field_validator('timezone')
    @classmethod
    def valid_zone(cls, value):
        try:
            ZoneInfo(value)
        except (KeyError, ValueError):
            raise ValueError('expected IANA timezone') from None
        return value

    @model_validator(mode='after')
    def sufficient_window(self):
        if self.min_baseline_days > self.baseline_days or self.min_recent_days > self.recent_days:
            raise ValueError('minimum valid days exceeds calendar window')
        return self


def _group(record, metric):
    return (record.provider, record.device_id, record.timezone, record.is_simulated,
            metric.measurement_method, metric.aggregation_method, metric.window_seconds,
            metric.source_algorithm_version, metric.baseline_ref, metric.measurement_site)


def _evidence_group(name):
    if name.startswith('sleep_'): return 'sleep'
    if name in ('steps', 'exercise_minutes'): return 'activity'
    if name in ('resting_heart_rate_bpm', 'night_hrv_rmssd_ms'): return 'cardiovascular'
    return 'other_night'


def analyze_changes(snapshot, *, as_of, config=None, authorized_metrics=None):
    """Pure internal calculation on a trusted, already purpose-filtered snapshot.

    Does not grant permission or validate a client-supplied snapshot. Use
    StateAnalysisService for session-facing calls. No rolling window expansion.
    """
    config = AnalysisConfig.model_validate_json((config or AnalysisConfig()).model_dump_json())
    as_of = offset_datetime(as_of)
    allowed = set(CORE_METRICS if authorized_metrics is None else authorized_metrics)
    zone = ZoneInfo(config.timezone)
    recent_end = as_of.astimezone(zone).date()-timedelta(days=1)
    recent_start = recent_end-timedelta(days=config.recent_days-1)
    baseline_end = recent_start-timedelta(days=1)
    baseline_start = baseline_end-timedelta(days=config.baseline_days-1)
    records = {r.record_id: r for r in snapshot.records}
    observations, statuses = [], []

    def evidence(rows):
        refs, pending = {}, [r.record_id for r, _ in rows]
        while pending:
            identity = pending.pop()
            if identity in refs: continue
            source = records.get(identity)
            if source is None or len(refs) >= 200:
                return None, 'source_conflict'
            if source.observed_end > as_of or source.ingested_at > as_of:
                return None, 'quality_insufficient'
            refs[identity] = dict(kind='wearable_record', source_id=identity, revision=source.record_revision)
            # Include dependencies of the contributing metric, not unrelated fields.
            metrics = [m for r, m in rows if r.record_id == identity] if any(r.record_id == identity for r, _ in rows) else source.metrics
            for metric in metrics:
                for ref in metric.dependency_refs:
                    dependency = records.get(ref.source_id)
                    if ref.kind != 'wearable_record' or dependency is None or dependency.record_revision != ref.revision:
                        return None, 'source_conflict'
                    if any(m.quality != 'valid' or m.value is None or (m.coverage is not None and m.coverage < config.minimum_coverage) for m in dependency.metrics):
                        return None, 'quality_insufficient'
                    pending.append(ref.source_id)
        return [refs[key] for key in sorted(refs)], None

    for name in CORE_METRICS:
        status = dict(metric_id=name, baseline_valid_days=0, recent_valid_days=0, excluded_days=0)
        if name not in allowed:
            statuses.append(MetricAvailability(**status, status='not_authorized'))
            continue
        candidates, excluded = [], set()
        seen = set()
        for record in snapshot.records:
            if record.record_type != 'daily' or not baseline_start <= record.local_date <= recent_end:
                continue
            metric = next((m for m in record.metrics if m.metric_id == name), None)
            if metric is None: continue
            seen.add(record.local_date)
            if (not record.day_complete or record.observed_end > as_of or record.ingested_at > as_of
                    or metric.quality != 'valid' or metric.value is None
                    or (metric.coverage is not None and metric.coverage < config.minimum_coverage)):
                excluded.add(record.local_date)
                continue
            candidates.append((record, metric))
        status['excluded_days'] = len(excluded)
        dates = [r.local_date for r, _ in candidates]
        groups = {_group(r, m) for r, m in candidates}
        if len(dates) != len(set(dates)) or len(groups) > 1 or any(r.timezone != config.timezone for r, _ in candidates):
            statuses.append(MetricAvailability(**status, status='source_conflict'))
            continue
        baseline = [(r, m) for r, m in candidates if r.local_date <= baseline_end]
        recent = [(r, m) for r, m in candidates if r.local_date >= recent_start]
        status.update(baseline_valid_days=len(baseline), recent_valid_days=len(recent))
        if len(baseline) < config.min_baseline_days or len(recent) < config.min_recent_days:
            if not seen: reason = 'no_data'
            elif excluded: reason = 'quality_insufficient'
            elif len(baseline) < config.min_baseline_days: reason = 'baseline_insufficient'
            else: reason = 'recent_insufficient'
            statuses.append(MetricAvailability(**status, status=reason))
            continue
        definition = METRIC_REGISTRY[name]
        def numeric(row):
            record, metric = row
            if definition.value_type == 'timestamp':
                # Local wall-clock minutes relative to wake-date midnight:
                # previous 23:50 = -10, current 00:10 = +10 (not 1430 vs 10).
                local = metric.value.astimezone(ZoneInfo(record.timezone)).replace(tzinfo=None)
                return (local-datetime.combine(record.local_date, time())).total_seconds()/60
            return float(metric.value)
        before, after = median(map(numeric, baseline)), median(map(numeric, recent))
        delta = after-before
        relative = delta/before if before != 0 and definition.value_type != 'timestamp' and not definition.baseline_required else None
        refs, dependency_error = evidence(baseline+recent)
        if refs is None:
            statuses.append(MetricAvailability(**status, status=dependency_error))
            continue
        source, metric = recent[-1]
        candidate = ((name == 'sleep_duration_minutes' and abs(delta) >= config.sleep_inquiry_minutes)
                     or (name == 'steps' and relative is not None and abs(relative) >= config.steps_inquiry_relative
                         and abs(delta) >= config.steps_inquiry_absolute))
        observations.append(StateObservation(
            metric_id=name, baseline_window=dict(start=baseline_start, end=baseline_end),
            recent_window=dict(start=recent_start, end=recent_end), baseline_valid_days=len(baseline),
            recent_valid_days=len(recent), baseline_median=before, recent_median=after, delta=delta,
            relative_delta=relative, unit='min_from_sleep_day' if definition.value_type == 'timestamp' else definition.unit,
            comparison_method='sleep_day_relative_minutes' if definition.value_type == 'timestamp' else 'numeric_median',
            provider=source.provider, device_id=source.device_id, measurement_method=metric.measurement_method,
            source_algorithm_version=metric.source_algorithm_version, baseline_ref=metric.baseline_ref,
            timezone=source.timezone, aggregation_method=metric.aggregation_method, window_seconds=metric.window_seconds,
            measurement_site=metric.measurement_site, evidence_group=_evidence_group(name),
            inquiry_candidate=candidate, record_refs=refs))
        statuses.append(MetricAvailability(**status, status='ready'))
    status = ('ready' if observations else 'not_authorized' if not allowed.intersection(CORE_METRICS)
              else 'source_conflict' if any(s.status == 'source_conflict' for s in statuses) else 'insufficient_data')
    # Include input contents and policy, not merely revision; IDs are reproducible and session-bound.
    fingerprint = json.dumps(dict(session=str(snapshot.session_id), revision=snapshot.revision, scope=snapshot.scope,
        as_of=as_of.isoformat(), config=config.model_dump(), allowed=sorted(allowed),
        records=[r.model_dump(mode='json') for r in sorted(snapshot.records, key=lambda r: r.record_id)]), sort_keys=True)
    identity = hashlib.sha256((ALGORITHM_VERSION+fingerprint).encode()).hexdigest()[:24]
    return StateAnalysis(analysis_id='analysis-'+identity, session_id=snapshot.session_id, evidence_revision=snapshot.revision,
        algorithm_version=ALGORITHM_VERSION, is_simulated=bool(records) and all(r.is_simulated for r in records.values()),
        as_of=as_of, status=status, scope=snapshot.scope, timezone=config.timezone,
        observations=observations, metric_statuses=statuses,
        limitations=['Deterministic observations, not diagnosis or safety classification.',
                     'Inquiry candidates are unvalidated product defaults; correlated metrics are grouped, not summed.'])


class StateAnalysisService:
    def __init__(self, wearables):
        self.wearables = wearables

    async def analyze(self, session_id, *, as_of, scope='local_analysis', config=None):
        record = self.wearables.sessions.require(session_id)
        async with record.lock:
            snapshot = self.wearables.snapshot(session_id, scope)
            self.wearables.validate_snapshot(session_id, snapshot)
            allowed = set().union(*(c.allowed_metrics for c in record.wearables.consents.values() if scope in c.scopes))
            result = analyze_changes(snapshot, as_of=as_of, config=config, authorized_metrics=allowed)
            self.wearables.validate_snapshot(session_id, snapshot)
            return result

    def validate_result(self, session_id, result, *, scope='local_analysis'):
        """Check a service-issued result immediately before consumption, under the session lock."""
        from app.agent.errors import RuntimeFailure
        # Only internal service-issued results are accepted by this integration seam.
        record = self.wearables.sessions.require(session_id)
        from app.schemas.session import SessionStatus
        if (record.lifecycle_pending or result.evidence_revision != record.wearables.revision
                or result.session_id != session_id or result.scope != scope
                or record.session.status != SessionStatus.ACTIVE):
            raise RuntimeFailure('stale_analysis', '分析来源或授权已改变，结果不可继续使用。')
