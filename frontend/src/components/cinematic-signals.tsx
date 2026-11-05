"use client";

import { Activity, ArrowDown, ArrowUp, ArrowUpRight, AudioLines, CloudMoon, Heart, Orbit } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { type ReactNode, useId, useRef, useState } from "react";
import { WorldMedia } from "./world-media";
import type { JourneyController } from "./use-support-journey";
import type { Scope, WearableProvider } from "../lib/support-types";
import { availabilityNames, limitationText, metricName, metricText } from "./support-journey-state";
import { signalAxis, signalGroup, signalGroups, signalPlot, signalQuality, signalReading, validSignalDays, type SignalGroup } from "./signals-data";
import "./cinematic-signals.css";

const media = {
  hero: "https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260324_151826_c7218672-6e92-402c-9e45-f1e0f454bdc4.mp4",
  feature: "https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260314_131748_f2ca2a28-fed7-44c8-b9a9-bd9acdd5ec31.mp4",
  statement: "https://stream.mux.com/9njY8qDfS02Uvbll018C8CK39p5EksK7mn02DDC1zYvppI.m3u8",
  join: "https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260306_074215_04640ca7-042c-45d6-bb56-58b1e8a42489.mp4",
};

const metrics = {
  sleep: { label: "睡眠时长", unit: "小时", icon: CloudMoon, domain: [5, 9], values: [6.4, 7.1, 6.8, 7.2, 6.9, 7.6, 7.4, 6.3, 6.6, 7.3, 6.8, 7.1, 7.8, 7.4], note: "把睡眠时长放回当天的作息里看。" },
  hrv: { label: "心率变异性", unit: "ms", icon: AudioLines, domain: [20, 70], values: [38, 44, 37, 52, 41, 47, 50, 43, 39, 46, 51, 42, 54, 48], note: "观察一段时间的变化时，尽量使用同一设备的记录。" },
  heart: { label: "静息心率", unit: "bpm", icon: Heart, domain: [50, 80], values: [67, 64, 63, 65, 70, 66, 64, 65, 62, 66, 63, 67, 62, 63], note: "回顾读数时，也可以想想当天的活动与测量条件。" },
};
type Metric = keyof typeof metrics;
type Range = 7 | 14;
const metricKeys = ["sleep", "hrv", "heart"] as const;
const ranges = [7, 14] as const;
const sampleDays = metrics.sleep.values.length;
const ease = [0.22, 1, 0.36, 1] as const;

function Reveal({ children, className, delay = 0 }: { children: ReactNode; className?: string; delay?: number }) {
  const reduced = useReducedMotion();
  return (
    <motion.div
      className={className}
      data-signals-reveal
      initial={reduced ? false : { opacity: 0, y: 24 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.15 }}
      transition={{ duration: reduced ? 0 : 0.72, delay: reduced ? 0 : delay, ease }}
    >
      {children}
    </motion.div>
  );
}

function SampleMetricChart() {
  const [metric, setMetric] = useState<Metric>("sleep");
  const [range, setRange] = useState<Range>(7);
  const [day, setDay] = useState(6);
  const fill = useId();
  const reduced = useReducedMotion();
  const sample = metrics[metric];
  const values = sample.values.slice(-range);
  const [min, max] = sample.domain;
  const baseline = 112;
  const coordinates = values.map((value, index) => ({
    x: 32 + 448 * index / (values.length - 1),
    y: baseline - 88 * (value - min) / (max - min),
  }));
  const points = coordinates.map(({ x, y }) => `${x},${y}`).join(" ");
  const selected = coordinates[day];
  const firstDay = sampleDays - range + 1;

  return <div className="signals-chart" data-metric={metric}>
    <div className="signals-chart-options">
      <div className="signals-metrics" role="group" aria-label="选择模拟指标">
        {metricKeys.map((key) => {
          const entry = metrics[key];
          const Icon = entry.icon;
          return <button type="button" key={key} aria-pressed={metric === key} onClick={() => setMetric(key)}>
            <Icon size={13} aria-hidden="true" />{entry.label}
          </button>;
        })}
      </div>
      <div className="signals-ranges" role="group" aria-label="趋势时间范围">
        {ranges.map((length) => <button type="button" key={length} aria-pressed={range === length} onClick={() => { setRange(length); setDay(length - 1); }}>
          {length} 天
        </button>)}
      </div>
    </div>
    <div className="signals-reading" aria-live="polite" aria-atomic="true">
      <span>第 {firstDay + day} 天 · 模拟读数</span>
      <strong>{values[day]} <small>{sample.unit}</small></strong>
    </div>
    <svg className="signals-plot" viewBox="0 0 492 122" role="img" aria-label={`${sample.label}，${range} 天模拟趋势。下方按钮可查看逐日读数。`}>
      <defs>
        <linearGradient id={fill} x1="0" x2="0" y1="0" y2="1">
          <stop stopColor="currentColor" stopOpacity=".27" />
          <stop offset="1" stopColor="currentColor" stopOpacity=".015" />
        </linearGradient>
      </defs>
      {[24, 68, baseline].map((y) => <line key={y} x1="32" y1={y} x2="480" y2={y} className="signals-plot-guide" />)}
      <text x="2" y="27">{max}</text><text x="2" y="115">{min}</text>
      <polygon points={`32,${baseline} ${points} 480,${baseline}`} fill={`url(#${fill})`} />
      <motion.polyline
        key={`${metric}-${range}`}
        points={points}
        className="signals-plot-line"
        initial={reduced ? false : { pathLength: 0 }}
        animate={{ pathLength: 1 }}
        transition={{ duration: reduced ? 0 : 0.65, ease }}
      />
      <line x1={selected.x} x2={selected.x} y1={selected.y} y2={baseline} className="signals-plot-cross" />
      <circle cx={selected.x} cy={selected.y} r="10" className="signals-plot-halo" />
      <circle cx={selected.x} cy={selected.y} r="4.5" className="signals-plot-dot" />
    </svg>
    <div className="signals-days" data-range={range} role="group" aria-label="查看单日模拟数据">
      {values.map((value, index) => <button
        type="button" key={firstDay + index} aria-pressed={day === index}
        aria-label={`第 ${firstDay + index} 天，${value} ${sample.unit}`}
        onClick={() => setDay(index)}
      >{firstDay + index}</button>)}
    </div>
    <p className="signals-chart-note">{sample.note} 固定样本，未连接设备。</p>
  </div>;
}

function MetricChart({ journey }: { journey?: JourneyController }) {
  return journey ? <ConnectedMetricChart journey={journey} /> : <SampleMetricChart />;
}

function ConnectedMetricChart({ journey }: { journey: JourneyController }) {
  const [group, setGroup] = useState<SignalGroup>("sleep");
  const [metric, setMetric] = useState("sleep_duration_minutes");
  const [day, setDay] = useState(journey.range - 1);
  const fill = useId();
  const reduced = useReducedMotion();
  const available = journey.catalog?.metrics.filter((item) => signalGroup(item.metric_id) === group) ?? [];
  const series = journey.trends?.series.find((item) => item.metric_id === metric);
  const timezone = journey.trends?.timezone ?? "Asia/Shanghai";
  const plot = series ? signalPlot(series, timezone) : null;
  const index = series ? Math.min(day, series.points.length - 1) : -1;
  const point = series?.points[index];
  const selected = plot?.points[index];
  const reading = series ? signalReading(series, point, timezone) : { text: "—", unit: "", context: "" };
  const timestamp = series?.value_type === "timestamp";
  const status = series?.status;
  const stateText = !journey.health ? journey.error ? "数据服务暂不可用" : "正在连接数据服务" : !journey.health.runtime.psychology_enabled ? "数据服务尚未启用" : !journey.state ? "请先在下方开启旅程" : !journey.state.wearables.record_count ? "尚未导入记录" : !series ? "正在读取当前用途的数据" : availabilityNames[series.status] ?? "当前记录不可用";

  function chooseGroup(next: SignalGroup) {
    setGroup(next);
    const first = journey.catalog?.metrics.find((item) => signalGroup(item.metric_id) === next);
    if (first) setMetric(first.metric_id);
  }

  return <div className="signals-chart" data-metric={group === "heart" ? "hrv" : group}>
    <div className="signals-chart-options">
      <div className="signals-metrics" role="group" aria-label="选择指标分组">
        {signalGroups.map((item) => <button type="button" key={item.id} aria-pressed={group === item.id} disabled={!journey.catalog} onClick={() => chooseGroup(item.id)}>{item.label}</button>)}
      </div>
      <div className="signals-ranges" role="group" aria-label="趋势时间范围">
        {([7, 14, 30] as const).map((length) => <button type="button" key={length} aria-pressed={journey.range === length} onClick={() => { journey.setRange(length); setDay(length - 1); }}>{length} 天</button>)}
      </div>
    </div>
    <label className="signals-metric-select"><span>查看指标</span><select value={metric} disabled={!available.length} onChange={(event) => setMetric(event.target.value)}>{available.length ? available.map((item) => <option value={item.metric_id} key={item.metric_id}>{metricName(item.metric_id)}</option>) : <option value={metric}>等待指标目录</option>}</select></label>
    <div className="signals-reading" aria-live="polite" aria-atomic="true">
      <span>{point ? `${point.date} · ${series?.source?.is_simulated ? "模拟读数" : "设备读数"}` : stateText}</span>
      <strong>{reading.text} <small>{reading.unit}</small></strong>
    </div>
    <svg className="signals-plot" viewBox="0 0 492 122" role="img" aria-label={`${metricName(metric)}，${journey.range} 天授权趋势。缺失读数留空，可在下方查看单日。`}>
      <defs><linearGradient id={fill} x1="0" x2="0" y1="0" y2="1"><stop stopColor="currentColor" stopOpacity=".27" /><stop offset="1" stopColor="currentColor" stopOpacity=".015" /></linearGradient></defs>
      {[24, 68, 112].map((y) => <line key={y} x1="32" y1={y} x2="480" y2={y} className="signals-plot-guide" />)}
      <text x="2" y="15">{plot?.segments.length ? signalAxis(plot.max, !!timestamp) : "—"}</text><text x="2" y="121">{plot?.segments.length ? signalAxis(plot.min, !!timestamp) : "—"}</text>
      {plot?.segments.map((segment, segmentIndex) => <g key={`${metric}-${journey.range}-${segmentIndex}`}>
        <polygon points={`${segment[0].x},112 ${segment.map((p) => `${p.x},${p.y}`).join(" ")} ${segment.at(-1)!.x},112`} fill={`url(#${fill})`} />
        <motion.polyline points={segment.map((p) => `${p.x},${p.y}`).join(" ")} className="signals-plot-line" initial={reduced ? false : { pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ duration: reduced ? 0 : .65, ease }} />
        {segment.length === 1 && <circle cx={segment[0].x} cy={segment[0].y!} r="3" className="signals-plot-dot" />}
      </g>)}
      {selected?.y !== null && selected?.y !== undefined && <><line x1={selected.x} x2={selected.x} y1={selected.y} y2="112" className="signals-plot-cross" /><circle cx={selected.x} cy={selected.y} r="10" className="signals-plot-halo" /><circle cx={selected.x} cy={selected.y} r="4.5" className="signals-plot-dot" /></>}
    </svg>
    <div className="signals-days" data-range={journey.range} role="group" aria-label="查看单日授权记录">
      {series?.points.map((item, i) => { const display = signalReading(series, item, timezone); return <button type="button" key={item.date} aria-pressed={index === i} data-missing={plot?.points[i].value === null} aria-label={`${item.date}，${display.text} ${display.unit} ${display.context}`} onClick={() => setDay(i)}>{item.date.slice(-2)}</button>; })}
    </div>
    <p className="signals-chart-note">{point && reading.text === "—" ? `${signalQuality[point.quality] ?? "读数不可用"}。` : ""}{reading.context && `${reading.context}。`}{status !== "ready" ? `${stateText}。` : "同一来源的授权记录；缺测处断开，不补为零。"}{timestamp && "入睡与醒来时间按记录所在地时区展示，跨午夜的变化连续比较。"}{series?.source?.score_scale && ` 厂商量表范围 ${series.source.score_scale.minimum}–${series.source.score_scale.maximum}，分值不跨厂商比较。`}</p>
  </div>;
}

function ProviderSettings({ provider, journey }: { provider: WearableProvider; journey: JourneyController }) {
  const [metrics, setMetrics] = useState(provider.allowed_metrics.length ? provider.allowed_metrics : provider.available_metrics);
  const [scopes, setScopes] = useState<Scope[]>(provider.scopes);
  const disabled = !!journey.action || journey.state?.session.status !== "ACTIVE";
  return <div className="signals-provider-settings">
    <p>{provider.is_simulated ? "模拟设备" : "设备来源"} · {provider.record_count} 条记录</p>
    <label><input type="checkbox" disabled={disabled} checked={scopes.includes("local_analysis")} onChange={(e) => setScopes(e.target.checked ? [...scopes, "local_analysis"] : scopes.filter((s) => s !== "local_analysis"))} />允许本机展示与分析</label>
    <label><input type="checkbox" disabled={disabled} checked={scopes.includes("model_context")} onChange={(e) => setScopes(e.target.checked ? [...scopes, "model_context"] : scopes.filter((s) => s !== "model_context"))} />允许必要设备观察用于支持对话</label>
    <details><summary>选择使用的指标 · {metrics.length} 项</summary><div className="signals-settings-metrics">{provider.available_metrics.map((id) => <label key={id}><input type="checkbox" disabled={disabled} checked={metrics.includes(id)} onChange={(e) => setMetrics(e.target.checked ? [...metrics, id] : metrics.filter((m) => m !== id))} />{metricName(id)}</label>)}</div></details>
    <div className="signals-settings-actions"><button className="signals-button signals-outline" type="button" disabled={disabled} onClick={() => void journey.consent(provider, metrics, scopes)}>保存用途选择</button><button className="signals-button signals-outline" type="button" disabled={disabled || !provider.scopes.length} onClick={() => void journey.consent(provider, [], [])}>撤回用途</button></div>
  </div>;
}

function DataSettings({ journey }: { journey: JourneyController }) {
  const [scenario, setScenario] = useState("S02");
  const active = journey.state?.session.status === "ACTIVE";
  const disabled = !!journey.action || journey.pending;
  return <details className="signals-data-settings"><summary>数据与使用设置</summary>
    <p>请只使用虚构记录。先开启旅程，再导入模拟数据；导入不会自动授予展示或对话用途。</p>
    {!journey.health?.runtime.psychology_enabled && <p>数据服务尚未就绪，可刷新连接后重试。</p>}
    <div className="signals-settings-actions">{!active && <button className="signals-button signals-outline" type="button" disabled={disabled || !journey.health?.runtime.psychology_enabled} onClick={() => void journey.start()}>开启本次旅程</button>}<button className="signals-button signals-outline" type="button" disabled={disabled} onClick={() => void journey.refreshAll()}>刷新连接</button></div>
    <label className="signals-metric-select"><span>模拟样本</span><select value={scenario} disabled={disabled || !active || !!journey.state?.wearables.record_count} onChange={(e) => setScenario(e.target.value)}>{(journey.catalog?.scenarios ?? []).map((item) => <option value={item.scenario_id} key={item.scenario_id}>{item.label}</option>)}</select></label>
    <button className="signals-button signals-outline" type="button" disabled={disabled || !active || !journey.catalog || !!journey.state?.wearables.record_count} onClick={() => void journey.simulate(scenario)}>导入 21 天模拟记录</button>
    {!!journey.state?.wearables.record_count && <p>当前已有记录。更换样本需先清空或开启新旅程，避免混合来源。</p>}
    {journey.state?.wearables.providers.map((provider) => <ProviderSettings key={`${provider.provider}:${journey.state?.wearables.revision}`} provider={provider} journey={journey} />)}
    {journey.action && <p role="status">{journey.action}…</p>}{journey.notice && <p role="status">{journey.notice}</p>}{journey.error && <p role="alert">{journey.error}</p>}
  </details>;
}

function BaselineDetails({ journey }: { journey: JourneyController }) {
  const metrics = journey.catalog?.metrics.filter((item) => item.usage === "core") ?? [];
  return <details className="signals-baseline-details"><summary>与自己的基线比较</summary>
    <p>比较此前 14 日与近期 3 日；只使用已允许本机分析且质量足够的记录。</p>
    <div className="signals-baseline-list">{metrics.map((metric) => {
      const item = journey.analysis?.observations.find((observation) => observation.metric_id === metric.metric_id);
      const status = journey.analysis?.metric_statuses.find((entry) => entry.metric_id === metric.metric_id)?.status;
      const baseline = item && metricText(item.metric_id, item.baseline_median, item.unit);
      const recent = item && metricText(item.metric_id, item.recent_median, item.unit);
      return <div key={metric.metric_id}><strong>{metricName(metric.metric_id)}</strong>{baseline && recent ? <p>基线 {baseline.text} → 近期 {recent.text} {recent.unit}</p> : <p>{availabilityNames[status ?? "no_data"] ?? "暂无可用分析"}</p>}</div>;
    })}</div>
    {journey.analysis?.limitations.map((value) => <p key={value}>{limitationText(value)}</p>)}
  </details>;
}

export function CinematicSignals({ onOpenAgent, journey }: { onOpenAgent: () => void; journey?: JourneyController }) {
  const reduced = useReducedMotion();
  const introTitle = useRef<HTMLHeadingElement>(null);
  const chartTitle = useRef<HTMLHeadingElement>(null);
  const statementTitle = useRef<HTMLHeadingElement>(null);

  function moveTo(target: HTMLHeadingElement | null) {
    if (!target) return;
    target.focus({ preventScroll: true });
    target.scrollIntoView({ block: "start", behavior: reduced ? "instant" : "smooth" });
  }

  function backToTop() {
    introTitle.current?.focus({ preventScroll: true });
    window.scrollTo({ top: 0, behavior: reduced ? "instant" : "smooth" });
  }

  return (
    <div className="cinematic-signals">
      <section className="signals-hero signals-cinematic" aria-labelledby="signals-title">
        <WorldMedia src={media.hero} poster="/images/signals-hero.jpg" className="signals-media" />
        <div className="signals-shade signals-hero-shade" aria-hidden="true" />
        <div className="signals-hero-copy">
          <p className="signals-eyebrow signals-rise">01 / LOVE STONE BODY SIGNALS</p>
          <h1 className="signals-display signals-rise" ref={introTitle} id="signals-title" tabIndex={-1}>A quieter way<br />to read your rhythm.</h1>
          <p className="signals-hero-description signals-rise"><strong>身心状态 · 身体节奏</strong><br />从睡眠和心跳的细小起伏里，多看见一点自己的日常。</p>
          <div className="signals-rise signals-hero-action"><button className="signals-button signals-glass" type="button" onClick={() => moveTo(chartTitle.current)}>{journey ? "查看个人趋势" : "查看模拟趋势"} <ArrowDown size={16} aria-hidden="true" /></button></div>
        </div>
        <div className="signals-hero-label"><span aria-hidden="true" /> {journey ? "按用途读取的观察" : "模拟数据"} <i aria-hidden="true" /> {journey?.state?.wearables.record_count ? "含模拟记录 · 无真实设备连接" : "无设备连接"}</div>
      </section>

      <section className="signals-tagline" aria-labelledby="signals-tagline-title">
        <Reveal className="signals-centered">
          <p className="signals-eyebrow"><Orbit size={16} aria-hidden="true" /> 02 / IN YOUR OWN TIME</p>
          <h2 className="signals-display" id="signals-tagline-title">Come back<br />to your own rhythm.</h2>
          <p className="signals-muted">先看见起伏，再想想那天发生了什么。</p>
        </Reveal>
      </section>

      <section className="signals-feature" aria-labelledby="signals-feature-title">
        <Reveal className="signals-feature-grid">
          <div className="signals-feature-card">
            <div className="signals-feature-top">
              <span className="signals-feature-icon"><Activity size={18} aria-hidden="true" /></span>
              <p className="signals-eyebrow">03 / THE RHYTHM ATLAS</p>
            </div>
            <h2 ref={chartTitle} className="signals-display" id="signals-feature-title" tabIndex={-1}>Everyday rhythms.</h2>
            <p className="signals-feature-description">{journey ? "按分组切换指标和时间范围，看看同一段日子里的不同起伏。" : "切换三种指标和时间范围，看看同一段日子里的不同起伏。"}</p>
            <MetricChart journey={journey} />
            {journey && <DataSettings journey={journey} />}
          </div>
          <div className="signals-feature-film signals-cinematic">
            <WorldMedia src={media.feature} poster="/images/signals-feature.jpg" className="signals-media" />
            <div className="signals-shade signals-feature-shade" aria-hidden="true" />
            <div className="signals-film-caption">
              <p className="signals-eyebrow">FIELD NOTES / 01</p>
              <p>Signals in the everyday.</p>
            </div>
          </div>
        </Reveal>
      </section>

      <section className="signals-statement signals-cinematic" aria-labelledby="signals-statement-title">
        <WorldMedia src={media.statement} poster="/images/signals-statement.jpg" className="signals-media" />
        <div className="signals-shade signals-statement-shade" aria-hidden="true" />
        <Reveal className="signals-centered signals-statement-copy">
          <p className="signals-eyebrow">04 / KEEP THE CONTEXT</p>
          <h2 ref={statementTitle} className="signals-display" id="signals-statement-title" tabIndex={-1}>Signals are clues.<br />You give them context.</h2>
          <p className="signals-muted">身体信号可以陪你回顾一段日子。哪天发生了什么、当时感觉如何，仍由你自己描述。</p>
          <p className="signals-summary-caption">{journey ? `已授权观察 · 最近 ${journey.range} 天` : `模拟样本 · 第 ${sampleDays} 天`}</p>
          <div className="signals-summaries" role="group" aria-label={journey ? "最近可用授权读数与有效记录日" : "最后一天的模拟读数与样本范围"}>
            {journey ? ["sleep_duration_minutes", "night_hrv_rmssd_ms", "resting_heart_rate_bpm"].map((metric) => {
              const series = journey.trends?.series.find((item) => item.metric_id === metric);
              const point = series?.status === "ready" ? [...series.points].reverse().find((item) => item.value !== null && item.quality === "valid") : undefined;
              const reading = series ? signalReading(series, point, journey.trends?.timezone ?? "Asia/Shanghai") : { text: "—", unit: "" };
              return <div className="signals-summary" key={metric}><p className="signals-summary-value">{reading.text}<small>{reading.unit}</small></p><span>{metricName(metric)}</span><span className="signals-summary-date">{point?.date ?? "暂无授权读数"}{point && series?.source?.is_simulated ? " · 模拟" : ""}</span></div>;
            }) : metricKeys.map((key) => {
              const sample = metrics[key];
              return <div className="signals-summary" key={key}>
                <p className="signals-summary-value">{sample.values.at(-1)}<small>{sample.unit}</small></p>
                <span>{sample.label} · 模拟</span>
              </div>;
            })}
            <div className="signals-summary"><p className="signals-summary-value">{journey ? validSignalDays(journey.trends) : sampleDays}<small>天</small></p><span>{journey ? "有效记录日" : "固定模拟样本"}</span></div>
          </div>
          {journey && <BaselineDetails journey={journey} />}
          <button className="signals-button signals-glass" type="button" onClick={() => moveTo(chartTitle.current)}>回看逐日趋势 <ArrowUp size={15} aria-hidden="true" /></button>
        </Reveal>
      </section>

      <section className="signals-join signals-cinematic" aria-labelledby="signals-join-title">
        <WorldMedia src={media.join} poster="/images/signals-join.jpg" className="signals-media" />
        <div className="signals-shade signals-join-shade" aria-hidden="true" />
        <Reveal className="signals-centered signals-join-copy">
          <p className="signals-eyebrow">05 / RETURN TO YOURSELF</p>
          <h2 className="signals-display" id="signals-join-title">Pause.<br />Check in with yourself.</h2>
          <p className="signals-muted">如果某一天的变化让你想起一件事，可以把感受写进对话。拾光会从你愿意分享的文字开始。</p>
          <div className="signals-actions">
            <button className="signals-button signals-glass" type="button" onClick={onOpenAgent}>和拾光聊聊 <ArrowUpRight size={16} aria-hidden="true" /></button>
            <button className="signals-button signals-outline" type="button" onClick={() => moveTo(chartTitle.current)}>{journey ? "再看个人趋势" : "再看模拟趋势"}</button>
          </div>
        </Reveal>
      </section>

      <footer className="signals-footer" aria-label="身心状态页导航">
        <div className="signals-footer-grid">
          <div><p className="signals-eyebrow">06 / LOVE STONE</p><p className="signals-footer-display">A rhythm<br />of your own.</p></div>
          <div className="signals-footer-links" role="group" aria-label="身心状态页内跳转">
            <button type="button" onClick={backToTop}>返回身心状态页首 <ArrowUp size={14} aria-hidden="true" /></button>
            <button type="button" onClick={() => moveTo(chartTitle.current)}>{journey ? "查看个人趋势" : "查看模拟趋势"} <ArrowUpRight size={14} aria-hidden="true" /></button>
            <button type="button" onClick={() => moveTo(statementTitle.current)}>读懂数据边界 <ArrowUpRight size={14} aria-hidden="true" /></button>
          </div>
          <div className="signals-footer-reflect"><p>用自己的话，给这一天留一点记录。</p><button className="signals-button signals-glass" type="button" onClick={onOpenAgent}>开始对话 <ArrowUpRight size={15} aria-hidden="true" /></button></div>
        </div>
        <div className="signals-footer-base"><span>LoveStone</span><p>{journey ? "无真实设备连接 · 记录不用于诊断 · 只有单独允许的设备观察才用于对话" : "模拟数据 · 无设备连接 · 不用于诊断，也不会作为对话输入"}</p></div>
      </footer>
    </div>
  );
}
