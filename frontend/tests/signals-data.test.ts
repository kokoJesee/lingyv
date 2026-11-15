import assert from "node:assert/strict";
import { test } from "node:test";
import type { TrendSeries, WearableTrends } from "../src/lib/support-types";
import { metricNames } from "../src/components/support-journey-state";
import { localSleepMinutes, signalAxis, signalGroup, signalGroups, signalPlot, signalReading, validSignalDays } from "../src/components/signals-data";

function series(overrides: Partial<TrendSeries> = {}): TrendSeries {
  return { metric_id: "sleep_onset_at", unit: "timestamp", value_type: "timestamp", status: "ready", source: null, points: [
    { date: "2026-11-03", value: "2026-11-02T15:50:00Z", quality: "valid", record_id: "one" },
    { date: "2026-11-04", value: "2026-11-03T16:10:00Z", quality: "valid", record_id: "two" },
  ], ...overrides };
}

test("sleep timestamps use the requested local timezone and stay continuous across midnight", () => {
  const sample = series();
  assert.equal(localSleepMinutes("2026-11-02T15:50:00Z", "2026-11-03", "Asia/Shanghai"), -10);
  assert.equal(localSleepMinutes("2026-11-03T16:10:00Z", "2026-11-04", "Asia/Shanghai"), 10);
  assert.deepEqual(signalPlot(sample, "Asia/Shanghai").points.map((point) => point.value), [-10, 10]);
  assert.deepEqual(signalReading(sample, sample.points[0], "Asia/Shanghai"), { text: "23:50", unit: "", context: "前一日 · Asia/Shanghai" });
  assert.equal(signalAxis(-10, true), "23:50");
  assert.equal(signalAxis(10, true), "00:10");
});

test("wake readings respect timezone DST and invalid timestamp or timezone is not plotted", () => {
  assert.equal(localSleepMinutes("2026-03-08T11:00:00Z", "2026-03-08", "America/New_York"), 420);
  assert.equal(localSleepMinutes("invalid", "2026-03-08", "Asia/Shanghai"), null);
  assert.equal(localSleepMinutes("2026-03-08T11:00:00Z", "2026-03-08", "invalid/zone"), null);
  const sample = series({ metric_id: "final_wake_at", points: [{ date: "2026-11-04", value: "2026-11-03T23:15:00Z", quality: "valid", record_id: "wake" }] });
  assert.equal(signalReading(sample, sample.points[0], "Asia/Shanghai").text, "07:15");
});

test("missing, invalid or unauthorized points never become zero or connect valid segments", () => {
  const sample = series({ metric_id: "steps", value_type: "integer", unit: "count", points: [
    { date: "2026-11-01", value: 0, quality: "valid", record_id: "zero" },
    { date: "2026-11-02", value: null, quality: "missing", record_id: null },
    { date: "2026-11-03", value: 8000, quality: "valid", record_id: "eight" },
    { date: "2026-11-04", value: 9000, quality: "invalid", record_id: "invalid" },
  ] });
  const plot = signalPlot(sample, "Asia/Shanghai");
  assert.deepEqual(plot.points.map((point) => point.value), [0, null, 8000, null]);
  assert.equal(plot.segments.length, 2);
  assert.equal(signalReading(sample, sample.points[1], "Asia/Shanghai").text, "—");
  assert.equal(signalPlot({ ...sample, status: "not_authorized" }, "Asia/Shanghai").segments.length, 0);
  assert.equal(signalPlot({ ...sample, status: "source_conflict" }, "Asia/Shanghai").segments.length, 0);
  assert.equal(validSignalDays({ series: [sample], timezone: "Asia/Shanghai" } as WearableTrends), 2);
});

test("all 30 known metrics remain reachable through the five groups", () => {
  assert.equal(Object.keys(metricNames).length, 30);
  const counts = Object.fromEntries(signalGroups.map(({ id }) => [id, Object.keys(metricNames).filter((metric) => signalGroup(metric) === id).length]));
  assert.deepEqual(counts, { sleep: 11, heart: 6, activity: 5, night: 3, vendor: 5 });
});
