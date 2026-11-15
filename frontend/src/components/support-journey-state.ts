import type { SupportState, TrendSeries } from "../lib/support-types";

export const metricNames: Record<string, string> = {
  sleep_duration_minutes: "睡眠时长", time_in_bed_minutes: "卧床时长", sleep_onset_at: "入睡时间", final_wake_at: "醒来时间",
  sleep_efficiency_pct: "睡眠效率", sleep_latency_minutes: "入睡耗时", waso_minutes: "睡后清醒时长",
  deep_sleep_minutes: "深睡时长", rem_sleep_minutes: "REM 时长", light_sleep_minutes: "浅睡时长", awakening_count: "醒来次数",
  resting_heart_rate_bpm: "静息心率", night_hr_mean_bpm: "夜间平均心率", night_hr_min_bpm: "夜间最低心率", day_hr_mean_bpm: "白天平均心率",
  night_hrv_rmssd_ms: "夜间 HRV · RMSSD", hrv_sdnn_ms: "HRV · SDNN", night_resp_rate_mean: "夜间呼吸频率", night_spo2_mean_pct: "夜间血氧",
  skin_temp_deviation_c: "皮温相对基线", steps: "步数", active_energy_kcal: "活动能量", exercise_minutes: "运动时长", sedentary_minutes: "久坐时长", workout_count: "运动次数",
  "vendor.sleep_score": "厂商睡眠评分", "vendor.readiness_score": "厂商准备度评分", "vendor.physiological_stress_score": "厂商生理压力评分",
  "vendor.energy_reserve_score": "厂商能量储备评分", "vendor.stress_duration_minutes": "厂商压力时长",
};
export const availabilityNames: Record<string, string> = {
  ready: "可查看", not_authorized: "尚未授权本地分析", no_data: "暂无数据", baseline_insufficient: "个人基线不足",
  recent_insufficient: "近期记录不足", quality_insufficient: "数据质量不足", source_conflict: "来源不一致，暂不合并", insufficient_data: "有效记录不足",
};
export function metricName(id: string): string { return metricNames[id] ?? id; }
const displayUnits: Record<string, string> = {
  min: "分钟", ms: "毫秒", bpm: "次/分", "breaths/min": "次/分", kcal: "千卡",
  degC_delta: "°C（相对基线）", score: "分（厂商量表）",
};
export function metricDisplay(id: string, value: number, unit: string): { value: number; unit: string } {
  if (id === "sleep_duration_minutes" && unit === "min") return { value: value / 60, unit: "小时" };
  const label = unit === "count" ? id === "steps" ? "步" : "次" : displayUnits[unit] ?? unit;
  return { value, unit: label };
}

/** Backend timestamps are wall-clock minutes relative to the wake-date midnight. */
export function sleepClock(minutes: number): string {
  const rounded = Math.round(minutes);
  const day = Math.floor(rounded / 1440);
  const minuteOfDay = ((rounded % 1440) + 1440) % 1440;
  const time = `${String(Math.floor(minuteOfDay / 60)).padStart(2, "0")}:${String(minuteOfDay % 60).padStart(2, "0")}`;
  const date = day === -1 ? "前一日" : day === 0 ? "当天" : day === 1 ? "次日" : day < 0 ? `前 ${-day} 日` : `后 ${day} 日`;
  return `${date} ${time}`;
}

export function metricText(id: string, value: number, unit: string): { text: string; unit: string } {
  if (unit === "min_from_sleep_day") return { text: sleepClock(value), unit: "" };
  const display = metricDisplay(id, value, unit);
  return { text: display.value.toLocaleString("zh-CN", { maximumFractionDigits: 1 }), unit: display.unit };
}

const limitationLabels: Record<string, string> = {
  "Deterministic observations, not diagnosis or safety classification.": "这里只展示按固定方法计算的变化观察，不用于诊断或安全分类。",
  "Inquiry candidates are unvalidated product defaults; correlated metrics are grouped, not summed.": "提示进一步询问的阈值仍是未经验证的产品默认值；相关指标按组处理，不相加。",
};
export function limitationText(value: string): string { return limitationLabels[value] ?? value; }

/** Keep each gap explicit: neither interpolate missing readings nor join across them. */
export function chartSegments(series: TrendSeries): { x: number; y: number; value: number; date: string }[][] {
  const numeric = series.points.flatMap((point) => typeof point.value === "number" && point.quality === "valid" ? [metricDisplay(series.metric_id, point.value, series.unit).value] : []);
  if (!numeric.length) return [];
  const low = Math.min(...numeric), high = Math.max(...numeric), spread = Math.max(high - low, 1);
  const groups: { x: number; y: number; value: number; date: string }[][] = [];
  let group: { x: number; y: number; value: number; date: string }[] = [];
  series.points.forEach((point, index) => {
    if (typeof point.value !== "number" || point.quality !== "valid") {
      if (group.length) groups.push(group);
      group = [];
      return;
    }
    const value = metricDisplay(series.metric_id, point.value, series.unit).value;
    group.push({ x: 18 + 444 * index / Math.max(series.points.length - 1, 1), y: 120 - 88 * (value - low) / spread, value, date: point.date });
  });
  if (group.length) groups.push(group);
  return groups;
}
export function cancellableRun(state: SupportState | null, requestId: string | undefined): string | null {
  return requestId && state?.active_request_id === requestId && state.session.workflow.phase === "RUNNING"
    ? state.session.workflow.run_id : null;
}
