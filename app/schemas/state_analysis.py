"""Deterministic analysis results; no clinical or safety classification."""
from datetime import date
from uuid import UUID
from typing import Literal

from pydantic import AwareDatetime, Field, StrictBool, model_validator

from app.schemas.wearable import ContractModel, EvidenceReference, Finite, Revision, Text
from app.wearables.metric_registry import get_metric


class DateWindow(ContractModel):
    start: date
    end: date  # inclusive local calendar dates

    @model_validator(mode="after")
    def ordered(self):
        if self.start > self.end:
            raise ValueError("date window is reversed")
        return self


class StateObservation(ContractModel):
    metric_id: Text
    baseline_window: DateWindow
    recent_window: DateWindow
    baseline_valid_days: int = Field(strict=True, ge=1)
    recent_valid_days: int = Field(strict=True, ge=1)
    baseline_median: Finite
    recent_median: Finite
    delta: Finite
    relative_delta: Finite | None = None
    unit: Text
    comparison_method: Literal["numeric_median", "sleep_day_relative_minutes"]
    provider: Text
    device_id: Text
    measurement_method: Text
    source_algorithm_version: Text
    baseline_ref: Text | None = None
    timezone: Text = "UTC"
    aggregation_method: Text = "unspecified"
    window_seconds: Finite | None = None
    measurement_site: Text | None = None
    evidence_group: Literal["sleep", "cardiovascular", "activity", "other_night"] = "sleep"
    inquiry_candidate: bool = False
    record_refs: list[EvidenceReference] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def coherent_comparison(self):
        from math import isclose

        definition = get_metric(self.metric_id)
        if definition.usage in ("display", "compatibility"):
            raise ValueError("display/compatibility metric is not a core change observation")
        timestamp = definition.value_type == "timestamp"
        expected_unit = "min_from_sleep_day" if timestamp else definition.unit
        expected_method = "sleep_day_relative_minutes" if timestamp else "numeric_median"
        if self.unit != expected_unit or self.comparison_method != expected_method:
            raise ValueError("comparison unit or method differs from metric semantics")
        if self.baseline_window.end >= self.recent_window.start:
            raise ValueError("baseline and recent windows must not overlap")
        for window, count in ((self.baseline_window, self.baseline_valid_days), (self.recent_window, self.recent_valid_days)):
            if count > (window.end - window.start).days + 1:
                raise ValueError("valid days exceed window length")
        if not isclose(self.delta, self.recent_median - self.baseline_median, abs_tol=1e-8):
            raise ValueError("delta is inconsistent with medians")
        if definition.baseline_required and not self.baseline_ref:
            raise ValueError("temperature comparison requires baseline reference")
        if definition.required_method and self.measurement_method != definition.required_method:
            raise ValueError("comparison requires known matching HRV method")
        if self.relative_delta is not None:
            if timestamp or definition.baseline_required or self.baseline_median == 0:
                raise ValueError("relative change is undefined for this baseline/metric")
            if not isclose(self.relative_delta, self.delta / self.baseline_median, abs_tol=1e-8):
                raise ValueError("relative delta is inconsistent")
        refs = [(r.kind, r.source_id, r.revision) for r in self.record_refs]
        if any(r.kind != "wearable_record" for r in self.record_refs) or len(set(refs)) != len(refs):
            raise ValueError("observations require distinct wearable references")
        return self


class MetricAvailability(ContractModel):
    metric_id: Text
    status: Literal["ready", "not_authorized", "no_data", "baseline_insufficient", "recent_insufficient", "quality_insufficient", "source_conflict"]
    baseline_valid_days: int = Field(strict=True, ge=0)
    recent_valid_days: int = Field(strict=True, ge=0)
    excluded_days: int = Field(strict=True, ge=0)


class StateAnalysis(ContractModel):
    schema_version: Literal["state_analysis.v0.1"] = "state_analysis.v0.1"
    analysis_id: Text
    session_id: UUID | None = None
    evidence_revision: int = Field(strict=True, ge=0)
    algorithm_version: Text
    is_simulated: StrictBool
    as_of: AwareDatetime
    status: Literal["ready", "insufficient_data", "not_authorized", "source_conflict"]
    scope: Literal["local_analysis", "model_context"] = "local_analysis"
    timezone: Text = "UTC"
    metric_statuses: list[MetricAvailability] = Field(default_factory=list, max_length=30)
    observations: list[StateObservation] = Field(default_factory=list, max_length=30)
    limitations: list[Text] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def result_state(self):
        if (self.status == "ready") != bool(self.observations):
            raise ValueError("only ready analysis contains observations, and requires at least one")
        if self.status != "ready" and not self.limitations:
            raise ValueError("unavailable analysis requires explanation")
        return self
