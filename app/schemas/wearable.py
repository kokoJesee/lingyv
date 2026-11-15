"""Validated transport data. Acceptance does not grant data-use permission."""
from datetime import date, datetime, timezone
from math import isclose
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StrictFloat,
    StrictInt, StringConstraints, field_validator, model_validator,
)

from app.wearables.metric_registry import MeasurementKind, ValueType, get_metric

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
Revision = Annotated[int, Field(strict=True, ge=1)]
Finite = Annotated[StrictFloat, Field(allow_inf_nan=False)]
Nonnegative = Annotated[Finite, Field(ge=0)]


def offset_datetime(value):
    """Accept ISO offset timestamps, never numeric epochs; preserve original offset."""
    if not isinstance(value, (str, datetime)):
        raise ValueError("expected ISO offset datetime")
    try:
        parsed = datetime.fromisoformat(value if isinstance(value, str) else value.isoformat())
    except ValueError:
        raise ValueError("expected ISO offset datetime") from None
    if parsed.utcoffset() is None:
        raise ValueError("timestamp requires timezone offset")
    return parsed


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, allow_inf_nan=False)

    @field_validator("*", mode="before")
    @classmethod
    def offset_fields(cls, value, info):
        if info.field_name in {
            "observed_start", "observed_end", "ingested_at", "sleep_onset_at", "final_wake_at",
            "as_of", "created_at", "updated_at", "confirmed_at",
        } or (cls.__name__ == "TimeWindow" and info.field_name in {"start", "end"}):
            return None if value is None else offset_datetime(value)
        return value


class EvidenceReference(ContractModel):
    kind: Literal["wearable_record", "user_report", "understanding", "consent"]
    source_id: Text
    revision: Revision


class TimeWindow(ContractModel):
    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode="after")
    def ordered(self):
        if self.start >= self.end:
            raise ValueError("window start must precede end")
        return self


class ScoreScale(ContractModel):
    minimum: Finite
    maximum: Finite
    direction: Literal["higher_is_more", "higher_is_less", "unspecified"]

    @model_validator(mode="after")
    def ordered(self):
        if self.minimum >= self.maximum:
            raise ValueError("score scale must have a positive span")
        return self


class MetricValue(ContractModel):
    metric_id: Text
    value_type: ValueType
    value: Finite | StrictInt | AwareDatetime | None
    unit: Text
    measurement_kind: MeasurementKind
    measurement_method: Text
    aggregation_method: Text
    window: TimeWindow
    window_seconds: Nonnegative | None = None  # HRV sampling window, not full record duration
    source_algorithm_version: Text
    quality: Literal["valid", "missing", "incomplete", "invalid", "unknown", "unsupported"]
    quality_reasons: list[Text] = Field(default_factory=list, max_length=10)
    coverage: Annotated[Finite, Field(ge=0, le=1)] | None = None
    baseline_ref: Text | None = None
    measurement_site: Text | None = None
    namespace: Text | None = None
    score_scale: ScoreScale | None = None
    score_origin: Literal["fixture", "vendor"] | None = None
    dependency_refs: list[EvidenceReference] = Field(default_factory=list, max_length=200)

    @model_validator(mode="before")
    @classmethod
    def raw_value_type(cls, data):
        # Reject coercion of bool/string to number and numeric epochs to datetime.
        if isinstance(data, dict) and data.get("value") is not None:
            definition = get_metric(data.get("metric_id"))
            value = data["value"]
            if definition.value_type == "integer" and type(value) is not int:
                raise ValueError("count must be an integer")
            if definition.value_type == "number" and type(value) not in (int, float):
                raise ValueError("numeric metric must be a number")
            if definition.value_type == "timestamp":
                data = {**data, "value": offset_datetime(value)}
        return data

    @model_validator(mode="after")
    def matches_registry(self):
        definition = get_metric(self.metric_id)
        if (self.unit, self.value_type, self.measurement_kind) != (
            definition.unit, definition.value_type, definition.measurement_kind
        ):
            raise ValueError("unit, type or measurement kind differs from registry")
        if self.quality != "valid" and not self.quality_reasons:
            raise ValueError("non-valid metrics require a quality reason")
        if self.value is None and self.quality not in ("missing", "invalid", "unknown", "unsupported"):
            raise ValueError("valid/incomplete metric requires a value")
        if self.value is not None and self.quality in ("missing", "unsupported"):
            raise ValueError("missing/unsupported metrics must have null values")
        if isinstance(self.value, datetime):
            if not self.window.start <= self.value <= self.window.end:
                raise ValueError("timestamp is outside measurement window")
        elif self.value is not None:
            if definition.minimum is not None and self.value < definition.minimum:
                raise ValueError("metric below structural minimum")
            if definition.maximum is not None and self.value > definition.maximum:
                raise ValueError("metric above structural maximum")
        if definition.required_method:
            if self.measurement_method not in (definition.required_method, "unknown"):
                raise ValueError("HRV method does not match metric")
            if self.measurement_method == "unknown" and self.quality in ("valid", "incomplete"):
                raise ValueError("unknown HRV method cannot be usable")
            if self.value is not None and (self.window_seconds is None or self.window_seconds <= 0):
                raise ValueError("HRV requires positive sampling window seconds")
        if definition.baseline_required and self.value is not None:
            if not self.baseline_ref or not self.measurement_site:
                raise ValueError("temperature deviation requires baseline and site")
        if self.metric_id.startswith("vendor.") and not self.namespace:
            raise ValueError("vendor metric requires namespace")
        if definition.measurement_kind == "vendor_score":
            if self.score_scale is None or self.score_origin is None:
                raise ValueError("vendor score requires scale and origin")
            if self.value is not None and not self.score_scale.minimum <= self.value <= self.score_scale.maximum:
                raise ValueError("score is outside its declared scale")
        elif self.score_scale is not None or self.score_origin is not None:
            raise ValueError("score metadata is only valid for scores")
        if definition.measurement_kind == "derived_statistic" and self.value is not None and not self.dependency_refs:
            raise ValueError("derived statistics require source dependencies")
        return self


class RecordBase(ContractModel):
    schema_version: Literal["wearable.v0.1"] = "wearable.v0.1"
    record_id: Text
    record_revision: Revision
    provider: Text
    provider_record_id: Text
    device_id: Text
    is_simulated: StrictBool
    timezone: Text
    observed_start: AwareDatetime
    observed_end: AwareDatetime
    ingested_at: AwareDatetime
    metrics: list[MetricValue] = Field(min_length=1, max_length=30)

    @field_validator("timezone")
    @classmethod
    def iana_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("timezone must be an IANA timezone") from None
        return value

    @model_validator(mode="after")
    def coherent_record(self):
        if not self.observed_start < self.observed_end <= self.ingested_at:
            raise ValueError("record window must be ordered and end before ingestion")
        ids = [metric.metric_id for metric in self.metrics]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate metric in record")
        for metric in self.metrics:
            if not self.observed_start <= metric.window.start < metric.window.end <= self.observed_end:
                raise ValueError("metric window must be contained by record")
            if metric.score_origin and (metric.score_origin == "fixture") != self.is_simulated:
                raise ValueError("score origin conflicts with simulation flag")
        return self


class WearableDailyRecord(RecordBase):
    record_type: Literal["daily"] = "daily"
    local_date: date
    day_complete: StrictBool

    @model_validator(mode="after")
    def daily_assignment(self):
        # Record may include the preceding night; day_complete describes the local day.
        zone = ZoneInfo(self.timezone)
        local_end = self.observed_end.astimezone(zone)
        end_date = local_end.date()
        midnight = local_end.hour == local_end.minute == local_end.second == local_end.microsecond == 0
        if self.day_complete:
            if (end_date - self.local_date).days != 1 or not midnight:
                raise ValueError("complete daily record must end at next local midnight")
            start = self.observed_start.astimezone(zone)
            if start.date() > self.local_date or (start.date() == self.local_date and start.hour + start.minute + start.second + start.microsecond != 0):
                raise ValueError("complete daily record must cover full local day")
        elif end_date != self.local_date:
            raise ValueError("partial daily record must end on its local date")
        return self


class WearableIntervalRecord(RecordBase):
    record_type: Literal["interval"] = "interval"


class SleepStage(ContractModel):
    stage: Literal["deep", "rem", "light", "awake", "unknown"]
    window: TimeWindow


class SleepEpisode(RecordBase):
    record_type: Literal["sleep"] = "sleep"
    sleep_kind: Literal["main", "nap"]
    local_date: date  # date of final wake, in declared timezone
    sleep_onset_at: AwareDatetime
    final_wake_at: AwareDatetime
    stages_complete: StrictBool
    stages: list[SleepStage] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def sleep_consistency(self):
        if not self.observed_start <= self.sleep_onset_at < self.final_wake_at <= self.observed_end:
            raise ValueError("sleep timestamps must be ordered within bed window")
        if self.final_wake_at.astimezone(ZoneInfo(self.timezone)).date() != self.local_date:
            raise ValueError("sleep belongs to local wake date")
        cursor = self.sleep_onset_at
        totals = dict.fromkeys(("deep", "rem", "light", "awake", "unknown"), 0.0)
        for stage in self.stages:
            if stage.window.start < cursor or stage.window.end > self.final_wake_at:
                raise ValueError("sleep stages overlap, are unordered or outside sleep window")
            if self.stages_complete and (stage.window.start != cursor or stage.stage == "unknown"):
                raise ValueError("complete stages must be contiguous and known")
            totals[stage.stage] += (stage.window.end.astimezone(timezone.utc) - stage.window.start.astimezone(timezone.utc)).total_seconds() / 60
            cursor = stage.window.end
        if self.stages_complete and cursor != self.final_wake_at:
            raise ValueError("complete stages must cover onset to final wake")
        values = {m.metric_id: m.value for m in self.metrics if m.quality == "valid"}
        expected = {
            "time_in_bed_minutes": (self.observed_end - self.observed_start).total_seconds() / 60,
            "sleep_latency_minutes": (self.sleep_onset_at - self.observed_start).total_seconds() / 60,
            "sleep_onset_at": self.sleep_onset_at, "final_wake_at": self.final_wake_at,
        }
        if self.stages_complete:
            duration = sum(totals[k] for k in ("deep", "rem", "light"))
            expected.update(sleep_duration_minutes=duration, waso_minutes=totals["awake"],
                            deep_sleep_minutes=totals["deep"], rem_sleep_minutes=totals["rem"],
                            light_sleep_minutes=totals["light"],
                            sleep_efficiency_pct=100 * duration / expected["time_in_bed_minutes"])
        stage_sum = sum(totals[k] for k in ("deep", "rem", "light"))
        reported_duration = values.get("sleep_duration_minutes")
        if reported_duration is not None and stage_sum > reported_duration + 0.01:
            raise ValueError("known sleep stages exceed reported total sleep")
        for key, expected_value in expected.items():
            if key in values:
                actual = values[key]
                matches = actual == expected_value if isinstance(expected_value, datetime) else isclose(actual, expected_value, abs_tol=0.01)
                if not matches:
                    raise ValueError("sleep summary conflicts with timestamps/stages")
        return self


class WorkoutEvent(RecordBase):
    record_type: Literal["workout"] = "workout"
    activity: Text


class WearableImportBatch(ContractModel):
    daily: list[WearableDailyRecord] = Field(default_factory=list, max_length=60)
    intervals: list[WearableIntervalRecord] = Field(default_factory=list, max_length=1000)
    sleeps: list[SleepEpisode] = Field(default_factory=list, max_length=200)
    workouts: list[WorkoutEvent] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def distinct_records(self):
        records = self.daily + self.intervals + self.sleeps + self.workouts
        if not records:
            raise ValueError("batch cannot be empty")
        keys = [(r.provider, r.provider_record_id) for r in records]
        if len(set(keys)) != len(keys) or len({r.record_id for r in records}) != len(records):
            raise ValueError("duplicate record identity in batch")
        return self


class MetricCapability(ContractModel):
    metric_id: Text
    supported: StrictBool
    unit: Text
    measurement_method: Text
    aggregation_method: Text
    sample_interval_seconds: Annotated[Finite, Field(gt=0)] | None = None
    score_scale: ScoreScale | None = None

    @model_validator(mode="after")
    def matches_metric(self):
        definition = get_metric(self.metric_id)
        if self.unit != definition.unit:
            raise ValueError("capability unit differs from registry")
        if self.supported and definition.required_method and self.measurement_method != definition.required_method:
            raise ValueError("supported HRV capability must declare its method")
        if definition.measurement_kind == "vendor_score":
            if self.supported and self.score_scale is None:
                raise ValueError("supported score requires a scale")
        elif self.score_scale is not None:
            raise ValueError("only score capabilities have scales")
        return self


class DeviceCapabilities(ContractModel):
    capability_profile: Literal["mock_basic_band", "mock_advanced_wearable", "mock_research_combined"]
    is_simulated: Literal[True] = True
    device_id: Text
    capabilities: list[MetricCapability] = Field(min_length=30, max_length=30)

    @model_validator(mode="after")
    def all_metrics_declared(self):
        if len({item.metric_id for item in self.capabilities}) != 30:
            raise ValueError("declare all 30 metrics exactly once, including unsupported ones")
        return self
