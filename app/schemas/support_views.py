"""Read models for the local support UI; values never carry execution authority."""
from datetime import date
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, Field, StrictBool, field_validator

from app.schemas.session import Session
from app.schemas.understanding_service import UnderstandingView
from app.schemas.wearable import ContractModel
from app.schemas.wearable_service import WearableOperation
from app.wearables.base import MockQuery


class ProviderOverview(ContractModel):
    provider: str
    record_count: int
    available_metrics: list[str]
    allowed_metrics: list[str]
    scopes: list[str]
    is_simulated: bool


class WearableOverview(ContractModel):
    revision: int
    record_count: int
    providers: list[ProviderOverview]
    available_timezones: list[str] = Field(default_factory=list)


class SupportStateView(ContractModel):
    session: Session
    active_request_id: UUID | None = None
    support_mode: bool
    support_route: str | None
    review_status: Literal['unreviewed'] = 'unreviewed'
    understandings: list[UnderstandingView]
    wearables: WearableOverview


class TrendQuery(ContractModel):
    as_of: AwareDatetime
    days: int = 14
    timezone: str = 'Asia/Shanghai'

    @field_validator('days')
    @classmethod
    def valid_days(cls, value):
        if value not in (7, 14, 30):
            raise ValueError('days must be 7, 14 or 30')
        return value

    @field_validator('timezone')
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (KeyError, ValueError):
            raise ValueError('expected IANA timezone') from None
        return value


class TrendPoint(ContractModel):
    date: date
    value: float | int | str | None
    quality: str
    record_id: str | None = None


class TrendSource(ContractModel):
    provider: str
    device_id: str
    measurement_method: str
    source_algorithm_version: str
    is_simulated: bool
    aggregation_method: str
    window_seconds: float | None
    baseline_ref: str | None
    measurement_site: str | None
    namespace: str | None
    score_scale: dict | None


class TrendSeries(ContractModel):
    metric_id: str
    unit: str
    value_type: str
    status: Literal['ready', 'not_authorized', 'no_data', 'quality_insufficient', 'source_conflict']
    points: list[TrendPoint]
    source: TrendSource | None = None


class WearableTrends(ContractModel):
    session_id: UUID
    revision: int
    scope: Literal['local_analysis'] = 'local_analysis'
    as_of: AwareDatetime
    timezone: str
    days: Literal[7, 14, 30]
    series: list[TrendSeries]


class SimulateWearablesRequest(WearableOperation):
    as_of: AwareDatetime
    scenario_id: Literal['S01', 'S02', 'S03', 'S04', 'S05', 'S06', 'S07', 'S08', 'S09', 'S10'] = 'S01'
    seed: int = Field(default=42, strict=True, ge=0, le=2**32-1)
    days: int = Field(default=21, strict=True, ge=1, le=30)
    timezone: str = 'Asia/Shanghai'
    replace_existing: StrictBool = False

    @field_validator('timezone')
    @classmethod
    def valid_timezone(cls, value):
        return TrendQuery.valid_timezone(value)

    def query(self):
        # Exclude high-frequency curves; this UI uses bounded complete-day trends.
        return MockQuery(as_of=self.as_of, scenario_id=self.scenario_id,
                         seed=self.seed, days=self.days, timezone=self.timezone,
                         include_intervals=False)
