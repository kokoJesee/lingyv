import type { TrendSeries, WearableTrends } from "../lib/support-types";
import { metricDisplay, sleepClock } from "./support-journey-state";

export const signalGroups = [
  { id: "sleep", label: "睡眠" }, { id: "heart", label: "心率" },
  { id: "activity", label: "活动" }, { id: "night", label: "夜间" }, { id: "vendor", label: "厂商" },
] as const;
export type SignalGroup = (typeof signalGroups)[number]["id"];
export function signalGroup(metric: string): SignalGroup {
  if (metric.startsWith("vendor.")) return "vendor";
  if (/^(sleep_|time_in_bed_|final_wake_|waso_|deep_sleep_|rem_sleep_|light_sleep_|awakening_)/.test(metric)) return "sleep";
  if (/^(resting_heart_|night_hr|day_hr|hrv_)/.test(metric)) return "heart";
  if (/^(steps$|active_energy_|exercise_|sedentary_|workout_)/.test(metric)) return "activity";
  return "night";
}

/** Plot local wall-clock offsets from each sleep/wake date, not absolute epoch time. */
export function localSleepMinutes(timestamp: string, sleepDate: string, timezone: string): number | null {
  const instant = new Date(timestamp);
  if (!Number.isFinite(instant.getTime()) || !/^\d{4}-\d{2}-\d{2}$/.test(sleepDate)) return null;
  try {
    const fields = Object.fromEntries(new Intl.DateTimeFormat("en-CA", {
      timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
    }).formatToParts(instant).map((part) => [part.type, part.value]));
    const local = Date.UTC(Number(fields.year), Number(fields.month) - 1, Number(fields.day), Number(fields.hour), Number(fields.minute), Number(fields.second));
    const [year, month, day] = sleepDate.split("-").map(Number);
    return (local - Date.UTC(year, month - 1, day)) / 60000;
  } catch { return null; }
}

type RawPoint = TrendSeries["points"][number];
export function signalValue(series: TrendSeries, point: RawPoint, timezone: string): number | null {
  if (series.status !== "ready" || point.quality !== "valid") return null;
  if (series.value_type === "timestamp") return typeof point.value === "string" ? localSleepMinutes(point.value, point.date, timezone) : null;
  return typeof point.value === "number" && Number.isFinite(point.value) ? metricDisplay(series.metric_id, point.value, series.unit).value : null;
}
export function signalReading(series: TrendSeries, point: RawPoint | undefined, timezone: string): { text: string; unit: string; context: string } {
  const value = point ? signalValue(series, point, timezone) : null;
  if (value === null) return { text: "—", unit: "", context: "当前读数不可用" };
  if (series.value_type === "timestamp") {
    const label = sleepClock(value);
    const space = label.lastIndexOf(" ");
    return { text: label.slice(space + 1), unit: "", context: `${label.slice(0, space)} · ${timezone}` };
  }
  return { text: value.toLocaleString("zh-CN", { maximumFractionDigits: 1 }), unit: metricDisplay(series.metric_id, 0, series.unit).unit, context: "" };
}

export type SignalPoint = { date: string; value: number | null; x: number; y: number | null };
export function signalPlot(series: TrendSeries, timezone: string): { points: SignalPoint[]; segments: SignalPoint[][]; min: number; max: number } {
  const values = series.points.map((point) => signalValue(series, point, timezone));
  const numeric = values.filter((value): value is number => value !== null);
  const low = numeric.length ? Math.min(...numeric) : 0, high = numeric.length ? Math.max(...numeric) : 1;
  const padding = high === low ? (series.value_type === "timestamp" ? 15 : Math.max(Math.abs(low) * .05, 1)) : (high - low) * .1;
  const min = low - padding, max = high + padding;
  const points = values.map((value, index) => ({ date: series.points[index].date, value, x: 32 + 448 * index / Math.max(values.length - 1, 1), y: value === null ? null : 112 - 88 * (value - min) / (max - min) }));
  const segments: SignalPoint[][] = [];
  let current: SignalPoint[] = [];
  points.forEach((point) => {
    if (point.value === null) { if (current.length) segments.push(current); current = []; }
    else current.push(point);
  });
  if (current.length) segments.push(current);
  return { points, segments, min, max };
}
export function signalAxis(value: number, timestamp: boolean): string {
  if (timestamp) return sleepClock(value).split(" ").at(-1)!;
  return value.toLocaleString("zh-CN", { maximumFractionDigits: 1, notation: Math.abs(value) >= 10000 ? "compact" : "standard" });
}
export function validSignalDays(trends: WearableTrends | null): number {
  if (!trends) return 0;
  return new Set(trends.series.flatMap((series) => series.points.filter((point) => signalValue(series, point, trends.timezone) !== null).map((point) => point.date))).size;
}
export const signalQuality: Record<string, string> = {
  valid: "有效读数", missing: "缺测", no_data: "暂无读数", incomplete: "记录不完整", invalid: "无效读数", unknown: "质量未知",
  unsupported: "设备不支持", quality_insufficient: "质量不足", source_conflict: "来源冲突", not_authorized: "尚未授权", invalid_timestamp: "时间无效",
};
