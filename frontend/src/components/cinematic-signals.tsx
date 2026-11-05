"use client";

import { Activity, ArrowDown, ArrowUp, ArrowUpRight, AudioLines, CloudMoon, Heart, Orbit } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { type ReactNode, useId, useRef, useState } from "react";
import { WorldMedia } from "./world-media";
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

function MetricChart() {
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

export function CinematicSignals({ onOpenAgent }: { onOpenAgent: () => void }) {
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
          <div className="signals-rise signals-hero-action"><button className="signals-button signals-glass" type="button" onClick={() => moveTo(chartTitle.current)}>查看模拟趋势 <ArrowDown size={16} aria-hidden="true" /></button></div>
        </div>
        <div className="signals-hero-label"><span aria-hidden="true" /> 模拟数据 <i aria-hidden="true" /> 无设备连接</div>
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
            <p className="signals-feature-description">切换三种指标和时间范围，看看同一段日子里的不同起伏。</p>
            <MetricChart />
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
          <p className="signals-summary-caption">模拟样本 · 第 {sampleDays} 天</p>
          <div className="signals-summaries" role="group" aria-label="最后一天的模拟读数与样本范围">
            {metricKeys.map((key) => {
              const sample = metrics[key];
              return <div className="signals-summary" key={key}>
                <p className="signals-summary-value">{sample.values.at(-1)}<small>{sample.unit}</small></p>
                <span>{sample.label} · 模拟</span>
              </div>;
            })}
            <div className="signals-summary"><p className="signals-summary-value">{sampleDays}<small>天</small></p><span>固定模拟样本</span></div>
          </div>
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
            <button className="signals-button signals-outline" type="button" onClick={() => moveTo(chartTitle.current)}>再看模拟趋势</button>
          </div>
        </Reveal>
      </section>

      <footer className="signals-footer" aria-label="身心状态页导航">
        <div className="signals-footer-grid">
          <div><p className="signals-eyebrow">06 / LOVE STONE</p><p className="signals-footer-display">A rhythm<br />of your own.</p></div>
          <div className="signals-footer-links" role="group" aria-label="身心状态页内跳转">
            <button type="button" onClick={backToTop}>返回身心状态页首 <ArrowUp size={14} aria-hidden="true" /></button>
            <button type="button" onClick={() => moveTo(chartTitle.current)}>查看模拟趋势 <ArrowUpRight size={14} aria-hidden="true" /></button>
            <button type="button" onClick={() => moveTo(statementTitle.current)}>读懂数据边界 <ArrowUpRight size={14} aria-hidden="true" /></button>
          </div>
          <div className="signals-footer-reflect"><p>用自己的话，给这一天留一点记录。</p><button className="signals-button signals-glass" type="button" onClick={onOpenAgent}>开始对话 <ArrowUpRight size={15} aria-hidden="true" /></button></div>
        </div>
        <div className="signals-footer-base"><span>LoveStone</span><p>模拟数据 · 无设备连接 · 不用于诊断，也不会作为对话输入</p></div>
      </footer>
    </div>
  );
}
