"""Project metric dictionary, not a vendor API or clinical reference range."""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

ValueType = Literal["number", "integer", "timestamp"]
MeasurementKind = Literal["sensor_estimate", "algorithm_estimate", "derived_statistic", "vendor_score"]


@dataclass(frozen=True)
class MetricDefinition:
    metric_id: str
    unit: str
    value_type: ValueType = "number"
    measurement_kind: MeasurementKind = "algorithm_estimate"
    minimum: float | None = 0
    maximum: float | None = None
    required_method: str | None = None
    baseline_required: bool = False
    usage: Literal["core", "auxiliary", "display", "compatibility"] = "auxiliary"


def _metric(name, unit="min", **kwargs):
    return MetricDefinition(name, unit, **kwargs)


_DEFINITIONS = (
    _metric("sleep_duration_minutes", usage="core"),
    _metric("time_in_bed_minutes"),
    _metric("sleep_onset_at", "timestamp", value_type="timestamp", minimum=None, usage="core"),
    _metric("final_wake_at", "timestamp", value_type="timestamp", minimum=None),
    _metric("sleep_efficiency_pct", "%", measurement_kind="derived_statistic", maximum=100, usage="core"),
    _metric("sleep_latency_minutes"),
    _metric("waso_minutes"),
    _metric("deep_sleep_minutes"),
    _metric("rem_sleep_minutes"),
    _metric("light_sleep_minutes"),
    _metric("awakening_count", "count", value_type="integer"),
    _metric("resting_heart_rate_bpm", "bpm", usage="core"),
    _metric("night_hr_mean_bpm", "bpm", measurement_kind="sensor_estimate"),
    _metric("night_hr_min_bpm", "bpm", measurement_kind="sensor_estimate"),
    _metric("day_hr_mean_bpm", "bpm", measurement_kind="sensor_estimate"),
    _metric("night_hrv_rmssd_ms", "ms", required_method="rmssd", usage="core"),
    _metric("hrv_sdnn_ms", "ms", required_method="sdnn", usage="compatibility"),
    _metric("night_resp_rate_mean", "breaths/min", measurement_kind="sensor_estimate", usage="core"),
    _metric("night_spo2_mean_pct", "%", measurement_kind="sensor_estimate", maximum=100),
    _metric("skin_temp_deviation_c", "degC_delta", measurement_kind="sensor_estimate", minimum=None, baseline_required=True, usage="core"),
    _metric("steps", "count", value_type="integer", usage="core"),
    _metric("active_energy_kcal", "kcal"),
    _metric("exercise_minutes", measurement_kind="derived_statistic", usage="core"),
    _metric("sedentary_minutes"),
    _metric("workout_count", "count", value_type="integer", measurement_kind="derived_statistic"),
    *(_metric(f"vendor.{name}_score", "score", measurement_kind="vendor_score", minimum=None, usage="display")
      for name in ("sleep", "readiness", "physiological_stress", "energy_reserve")),
    _metric("vendor.stress_duration_minutes", usage="display"),
)
METRIC_REGISTRY = MappingProxyType({item.metric_id: item for item in _DEFINITIONS})
REGISTRY_VERSION = "wearable_metrics.v0.1"


def get_metric(metric_id: str) -> MetricDefinition:
    if not isinstance(metric_id, str):
        raise ValueError("metric id must be a string")
    try:
        return METRIC_REGISTRY[metric_id]
    except KeyError:
        raise ValueError("unknown wearable metric") from None
