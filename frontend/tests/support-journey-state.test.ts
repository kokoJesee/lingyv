import assert from "node:assert/strict";
import { test } from "node:test";
import { cancellableRun, chartSegments, limitationText, metricDisplay, metricText, sleepClock } from "../src/components/support-journey-state";
import type { SupportState, TrendSeries } from "../src/lib/support-types";

test("a stop button can target only this page's current running request", () => {
  const state = { active_request_id: "this-request", session: { workflow: { phase: "RUNNING", run_id: "this-run" } } } as SupportState;
  assert.equal(cancellableRun(state, "this-request"), "this-run");
  assert.equal(cancellableRun(state, "another-request"), null);
  assert.equal(cancellableRun(state, undefined), null);
  assert.equal(cancellableRun({ ...state, session: { ...state.session, workflow: { ...state.session.workflow, phase: "COMPLETED" } } }, "this-request"), null);
});

test("the chart leaves missing and invalid readings as visible gaps", () => {
  const series: TrendSeries = { metric_id: "sleep_duration_minutes", unit: "min", value_type: "number", status: "ready", source: null,
    points: [
      { date: "2026-11-01", value: 420, quality: "valid", record_id: "one" },
      { date: "2026-11-02", value: null, quality: "no_data", record_id: null },
      { date: "2026-11-03", value: 450, quality: "valid", record_id: "three" },
      { date: "2026-11-04", value: 460, quality: "quality_insufficient", record_id: "four" },
      { date: "2026-11-05", value: 480, quality: "valid", record_id: "five" },
    ] };
  const segments = chartSegments(series);
  assert.deepEqual(segments.map((group) => group.map((p) => p.value)), [[7], [7.5], [8]]);
  assert.deepEqual(segments.map((group) => group[0].date), ["2026-11-01", "2026-11-03", "2026-11-05"]);
  assert.deepEqual(chartSegments({ ...series, status: "not_authorized", points: [] }), []);
});

test("sleep duration converts minutes to hours without changing HRV or other minutes", () => {
  assert.deepEqual(metricDisplay("sleep_duration_minutes", 420, "min"), { value: 7, unit: "小时" });
  assert.deepEqual(metricDisplay("night_hrv_rmssd_ms", 42, "ms"), { value: 42, unit: "毫秒" });
  assert.deepEqual(metricDisplay("exercise_minutes", 42, "min"), { value: 42, unit: "分钟" });
});


test("sleep-day offsets show local clock time and preserve which side of midnight they belong to", () => {
  assert.equal(sleepClock(-51), "前一日 23:09");
  assert.equal(sleepClock(33), "当天 00:33");
  assert.equal(sleepClock(-10), "前一日 23:50");
  assert.equal(sleepClock(10), "当天 00:10");
  assert.equal(sleepClock(1440), "次日 00:00");
  assert.deepEqual(metricText("sleep_onset_at", -51, "min_from_sleep_day"), { text: "前一日 23:09", unit: "" });
});

test("human-facing units preserve measurement meaning and unreviewed limitations", () => {
  assert.deepEqual(metricDisplay("steps", 5000, "count"), { value: 5000, unit: "步" });
  assert.deepEqual(metricDisplay("awakening_count", 2, "count"), { value: 2, unit: "次" });
  assert.deepEqual(metricDisplay("night_resp_rate_mean", 15, "breaths/min"), { value: 15, unit: "次/分" });
  assert.deepEqual(metricDisplay("skin_temp_deviation_c", -.2, "degC_delta"), { value: -.2, unit: "°C（相对基线）" });
  assert.match(limitationText("Deterministic observations, not diagnosis or safety classification."), /不用于诊断或安全分类/);
  assert.match(limitationText("Inquiry candidates are unvalidated product defaults; correlated metrics are grouped, not summed."), /未经验证.*不相加/);
  assert.equal(limitationText("New limitation requiring review"), "New limitation requiring review");
});
