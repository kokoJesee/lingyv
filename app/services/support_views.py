"""Synchronous bounded read views and explicit simulation import for the local UI.

These reads contain no await: polling a running turn never waits on its session
lock. Purpose filtering and source validation remain owned by the core services.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.agent.errors import RuntimeFailure
from app.schemas.support_views import (
    ProviderOverview, SupportStateView, TrendPoint, TrendSeries, TrendSource,
    WearableOverview, WearableTrends, TrendQuery,
)
from app.schemas.wearable_service import ReplaceSimulatedWearables
from app.services.understanding_service import UnderstandingService
from app.services.wearable_service import WearableService
from app.wearables.metric_registry import METRIC_REGISTRY
from app.wearables.mock import MockWearableAdapter


def wearable_catalog():
    # Public transport metadata only. Never return the generator's evaluation,
    # manifest, hidden user story, or expected route/interpretation.
    return {
        'is_simulated': True,
        'metrics': [dict(metric_id=m.metric_id, unit=m.unit, value_type=m.value_type,
                         usage=m.usage, required_method=m.required_method)
                    for m in METRIC_REGISTRY.values()],
        'scenarios': [dict(scenario_id=f'S{index:02}', label=f'模拟样本 {index:02}')
                      for index in range(1, 11)],
    }


class SupportViewService:
    def __init__(self, sessions):
        self.sessions = sessions
        self.wearables = WearableService(sessions)
        self.understandings = UnderstandingService(sessions)

    def controls(self, session_id):
        record = self.sessions.require(session_id)
        if record.lifecycle_pending:
            return []
        views = []
        for identity, entry in record.understandings.entries.items():
            if entry.value.status not in ('proposed', 'confirmed'):
                continue
            if entry.wearable_revision is not None and entry.wearable_revision != record.wearables.revision:
                continue
            try:
                self.understandings._check_refs(record, entry.value.evidence_refs)
            except RuntimeFailure:
                continue
            views.append(self.understandings.get(session_id, identity))
        return views

    def state(self, session_id):
        record = self.sessions.require(session_id)
        data = record.wearables
        providers = sorted(set(r.provider for r in data.records.values()) | set(data.consents))
        overview = []
        for provider in providers:
            rows = [r for r in data.records.values() if r.provider == provider]
            consent = data.consents.get(provider)
            overview.append(ProviderOverview(
                provider=provider, record_count=len(rows),
                available_metrics=sorted({m.metric_id for r in rows for m in r.metrics}),
                allowed_metrics=sorted(consent.allowed_metrics) if consent else [],
                scopes=sorted(consent.scopes) if consent else [],
                is_simulated=bool(rows) and all(r.is_simulated for r in rows),
            ))
        # Discover only sources the user has allowed for local reading. Keep
        # lifecycle polling non-blocking and avoid exposing stale source choices.
        authorized_daily = [] if record.lifecycle_pending else [
            row for row in self.wearables.snapshot(session_id, 'local_analysis').records
            if row.record_type == 'daily'
        ]
        available_timezones = list(dict.fromkeys(
            row.timezone for row in sorted(authorized_daily,
                key=lambda row: (row.local_date, row.observed_end, row.timezone), reverse=True)
        ))
        return SupportStateView(
            session=record.session.model_copy(deep=True), support_mode=record.support_mode,
            active_request_id=getattr(record, 'active_request_id', None),
            support_route=record.support_route, understandings=self.controls(session_id),
            wearables=WearableOverview(revision=data.revision, record_count=len(data.records),
                providers=overview, available_timezones=available_timezones),
        )

    async def simulate(self, session_id, request):
        # Generation only produces a batch. The existing service enforces revisions,
        # busy/lifecycle checks, idempotency, budgets and atomic import; no grant.
        self.sessions.require(session_id)
        if request.replace_existing:
            batch = MockWearableAdapter().fetch(request.query())
            return await self.wearables.replace_simulated(session_id, ReplaceSimulatedWearables(
                operation_id=request.operation_id, expected_revision=request.expected_revision, batch=batch,
            ))
        return await MockWearableAdapter().import_into(
            self.wearables, session_id, request.query(),
            expected_revision=request.expected_revision, operation_id=request.operation_id,
        )

    def trends(self, session_id, query: TrendQuery):
        query = TrendQuery.model_validate_json(query.model_dump_json())
        record = self.sessions.require(session_id)
        snapshot = self.wearables.snapshot(session_id, 'local_analysis')
        sources = {r.record_id: r for r in snapshot.records}
        zone = ZoneInfo(query.timezone)
        last = query.as_of.astimezone(zone).date() - timedelta(days=1)
        dates = [last - timedelta(days=query.days-index-1) for index in range(query.days)]
        # Each query selects one source timezone. Travel records in other zones
        # remain available through their own query; never stitch their dates.
        daily = [r for r in snapshot.records if r.record_type == 'daily'
                 and r.timezone == query.timezone
                 and dates[0] <= r.local_date <= last
                 and r.observed_end <= query.as_of and r.ingested_at <= query.as_of]
        permitted = {name for c in record.wearables.consents.values()
                     if 'local_analysis' in c.scopes for name in c.allowed_metrics}
        series = []
        for metric_id, definition in METRIC_REGISTRY.items():
            base = dict(metric_id=metric_id, unit=definition.unit, value_type=definition.value_type)
            if metric_id not in permitted:
                series.append(TrendSeries(**base, status='not_authorized', points=[]))
                continue
            rows = [(r, m) for r in daily for m in r.metrics if m.metric_id == metric_id]
            raw_has_metric = any(r.record_type == 'daily' and dates[0] <= r.local_date <= last
                                 and r.timezone == query.timezone
                                 and r.observed_end <= query.as_of and r.ingested_at <= query.as_of
                                 and any(m.metric_id == metric_id for m in r.metrics)
                                 for r in record.wearables.records.values())
            if not rows and raw_has_metric:
                # Selection can fail on a derived metric's dependency permissions.
                # Do not expose those records or pretend their values are missing.
                series.append(TrendSeries(**base, status='not_authorized', points=[]))
                continue
            keys = {self._source_key(r, m) for r, m in rows}
            by_date = {r.local_date: (r, m) for r, m in rows}
            conflict = (len(keys) > 1 or len(by_date) != len(rows)
                        or any(r.timezone != query.timezone for r, _ in rows))
            if conflict:
                series.append(TrendSeries(**base, status='source_conflict', points=[
                    TrendPoint(date=day, value=None, quality='source_conflict') for day in dates]))
                continue
            points = []
            for day in dates:
                row = by_date.get(day)
                if row is None:
                    points.append(TrendPoint(date=day, value=None, quality='no_data'))
                    continue
                r, m = row
                quality = self._quality(r, m, sources, query.as_of, set(), [1000])
                value = m.value if quality == 'valid' else None
                if isinstance(value, datetime):
                    value = value.isoformat()
                points.append(TrendPoint(date=day, value=value, quality=quality, record_id=r.record_id))
            status = 'ready' if any(p.value is not None for p in points) else ('quality_insufficient' if rows else 'no_data')
            source = self._source(*rows[0]) if rows else None
            series.append(TrendSeries(**base, status=status, points=points, source=source))
        return WearableTrends(session_id=session_id, revision=snapshot.revision,
                              as_of=query.as_of, timezone=query.timezone, days=query.days, series=series)

    @staticmethod
    def _quality(record, metric, sources, as_of, trail, budget):
        budget[0] -= 1
        node = (record.record_id, metric.metric_id)
        if budget[0] < 0 or len(trail) >= 64 or node in trail:
            return 'source_conflict'
        if metric.quality != 'valid':
            return metric.quality
        if getattr(record, 'day_complete', True) is False:
            return 'incomplete'
        if (metric.value is None or (metric.coverage is not None and metric.coverage < .8)
                or record.observed_end > as_of or record.ingested_at > as_of):
            return 'quality_insufficient'
        for ref in metric.dependency_refs:
            source = sources.get(ref.source_id)
            if source is None or source.record_revision != ref.revision:
                return 'source_conflict'
            if any(SupportViewService._quality(source, item, sources, as_of, trail | {node}, budget) != 'valid'
                   for item in source.metrics):
                return 'quality_insufficient'
        return 'valid'

    @staticmethod
    def _source_key(record, metric):
        scale = metric.score_scale.model_dump_json() if metric.score_scale else None
        return (record.provider, record.device_id, record.timezone, record.is_simulated,
                metric.measurement_method, metric.aggregation_method, metric.window_seconds,
                metric.source_algorithm_version, metric.baseline_ref, metric.measurement_site,
                metric.namespace, scale)

    @staticmethod
    def _source(record, metric):
        return TrendSource(provider=record.provider, device_id=record.device_id,
            measurement_method=metric.measurement_method, source_algorithm_version=metric.source_algorithm_version,
            is_simulated=record.is_simulated, aggregation_method=metric.aggregation_method,
            window_seconds=metric.window_seconds, baseline_ref=metric.baseline_ref,
            measurement_site=metric.measurement_site, namespace=metric.namespace,
            score_scale=metric.score_scale.model_dump() if metric.score_scale else None)
