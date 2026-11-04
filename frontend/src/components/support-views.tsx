"use client";

import { useState, type ReactNode } from "react";
import {
  Activity, ArrowDownRight, ArrowRight, AudioLines, BookOpen, Check, CircleHelp,
  CloudMoon, Fingerprint, FlaskConical, Footprints, Heart, Layers3,
  Orbit, Pause, Play, ScanLine, Sparkles, Wind, X,
} from "lucide-react";
import {
  hypothesisKeys, supportChoices,
  type ExperienceCard, type HypothesisKey, type Reflection, type SupportChoice,
} from "../lib/agent";
import "./support-views.css";

const layers = {
  facts: { name: "事实", english: "What happened", icon: ScanLine, tint: "lilac", hint: "你描述了什么？先区分原话和推测。" },
  interpretation: { name: "解释", english: "How it lands", icon: Layers3, tint: "peach", hint: "你当时是怎么理解这件事的？也可以有别的读法。" },
  emotion: { name: "情绪", english: "What you feel", icon: Heart, tint: "rose", hint: "哪些感受贴近你？可以同时有几种感受。" },
  needs: { name: "需要", english: "What matters", icon: Fingerprint, tint: "mint", hint: "现在最希望得到什么？你也可以暂时不知道。" },
} satisfies Record<HypothesisKey, { name: string; english: string; icon: typeof ScanLine; tint: string; hint: string }>;

const reflectionStatus = { proposed: "待确认假设", confirmed: "你已确认", corrected: "以你的修正为准" };
const choiceIcons = { keep: Heart, release: Wind, next: Footprints };

function SectionHead({ icon, eyebrow, title, description, badge }: {
  icon: ReactNode; eyebrow: string; title: string; description: string; badge: string;
}) {
  return (
    <header className="lsv-head">
      <div className="lsv-overline"><span className="lsv-icon">{icon}</span>{eyebrow}</div>
      <h1>{title}</h1>
      <p>{description}</p>
      <span className="lsv-badge"><i />{badge}</span>
    </header>
  );
}

export function MindCanvasView({ draft, onOpenAgent }: { draft: Reflection | null; onOpenAgent: () => void }) {
  const [active, setActive] = useState<HypothesisKey>("facts");
  const [paused, setPaused] = useState(false);
  const layer = layers[active];
  const Icon = layer.icon;

  return (
    <section className="ls-support lsv-canvas">
      <SectionHead icon={<Orbit />} eyebrow="MIND CANVAS" title="心象画布" description="把一段经历摊开看。你可以留住贴近的理解，也可以改写任何一层。" badge={draft ? reflectionStatus[draft.status] : "等候第一段经历"} />
      <div className="lsv-canvas-layout">
        <div className="lsv-panel lsv-atlas">
          <div className="lsv-panel-kicker"><span>01 / REFLECTION MAP</span><span>演示画布</span></div>
          <div className="lsv-map" data-paused={paused}>
            <div className="lsv-map-orbits" aria-hidden="true"><i /><i /><i /><b /></div>
            <div className="lsv-map-core" aria-hidden="true" data-tint={layer.tint}><Icon key={active} className="lsv-layer-enter" /></div>
            <div className="lsv-map-nodes" role="group" aria-label="理解层">
              {hypothesisKeys.map((key) => {
                const item = layers[key];
                const NodeIcon = item.icon;
                return (
                  <button type="button" key={key} className={`lsv-node lsv-node-${key}`} data-tint={item.tint} aria-pressed={active === key} onClick={() => setActive(key)}>
                    <NodeIcon size={16} />{item.name}
                  </button>
                );
              })}
            </div>
          </div>
          <div className="lsv-map-footer">
            <p className="lsv-map-caption">选择一层，看看它在这段经历里的位置。</p>
            <button type="button" className="lsv-motion-toggle" onClick={() => setPaused((value) => !value)}>
              {paused ? <Play size={13} /> : <Pause size={13} />}{paused ? "播放轨道" : "暂停轨道"}
            </button>
          </div>
        </div>
        <article className="lsv-panel lsv-layer-detail" data-tint={layer.tint}>
          <span className="lsv-icon"><Icon size={21} /></span>
          <div className="lsv-layer-copy" aria-live="polite">
            <div key={active} className="lsv-layer-enter">
              <div className="lsv-overline">{layer.english}</div>
              <h2>{layer.name}</h2>
              <p>{layer.hint}</p>
              {draft && <blockquote>{draft.hypotheses[active] || "你选择了留白。"}</blockquote>}
            </div>
          </div>
          <p className="lsv-note">{draft ? "草稿会随你在对话中的确认或纠正而更新。情绪与需要由你自己判断。" : "和引导角色聊一小段之后，这里就会显示四层理解草稿。"}</p>
          <button type="button" className="lsv-cta" onClick={onOpenAgent}>
            {draft ? "回到对话调整" : "开始一段对话"}<ArrowRight size={16} />
          </button>
        </article>
      </div>
    </section>
  );
}

const wearableMetrics = {
  sleep: { label: "睡眠时长", unit: "小时", latest: "7.4", icon: CloudMoon, tint: "lilac", domain: [5, 9], values: [6.4, 7.1, 6.8, 7.2, 6.9, 7.6, 7.4, 6.3, 6.6, 7.3, 6.8, 7.1, 7.8, 7.4], note: "睡眠时长可以作为回顾日常节奏的线索。" },
  hrv: { label: "心率变异性", unit: "ms", latest: "48", icon: AudioLines, tint: "mint", domain: [20, 70], values: [38, 44, 37, 52, 41, 47, 50, 43, 39, 46, 51, 42, 54, 48], note: "用同一个设备的连续读数来观察自己的节奏。" },
  heart: { label: "静息心率", unit: "bpm", latest: "63", icon: Heart, tint: "rose", domain: [50, 80], values: [67, 64, 63, 65, 70, 66, 64, 65, 62, 66, 63, 67, 62, 63], note: "记录中的变化也可能受到活动或测量条件影响。" },
};

type MetricKey = keyof typeof wearableMetrics;
const metricKeys: MetricKey[] = ["sleep", "hrv", "heart"];
type Range = 7 | 14;

export function WearableView() {
  const [metric, setMetric] = useState<MetricKey>("sleep");
  const [range, setRange] = useState<Range>(7);
  const [activeDay, setActiveDay] = useState(6);
  const item = wearableMetrics[metric];
  const values = item.values.slice(-range);
  const [min, max] = item.domain;
  const coordinates = values.map((value, index) => ({ x: 15 + 570 * index / (values.length - 1), y: 146 - 127 * (value - min) / (max - min) }));
  const line = coordinates.map(({ x, y }) => `${x},${y}`).join(" ");
  const focusPoint = coordinates[activeDay];

  function updateRange(next: Range) {
    setRange(next);
    setActiveDay(next - 1);
  }

  return (
    <section className="ls-support lsv-wearable">
      <SectionHead icon={<Activity />} eyebrow="BODY RHYTHM" title="身体节奏" description="身体信号提供另一种观察角度。把数字放在生活里看，感受仍由你定义。" badge="模拟数据 · 无设备连接" />
      <div className="lsv-metrics" role="group" aria-label="选择模拟指标">
        {metricKeys.map((key) => {
          const data = wearableMetrics[key];
          const Icon = data.icon;
          return (
            <button type="button" key={key} className="lsv-panel lsv-metric" data-tint={data.tint} aria-pressed={metric === key} onClick={() => setMetric(key)}>
              <span className="lsv-icon"><Icon size={18} /></span><span className="lsv-metric-label">{data.label}</span>
              <span className="lsv-value">{data.latest} <small>{data.unit}</small></span>
            </button>
          );
        })}
      </div>
      <div className="lsv-panel lsv-chart" data-tint={item.tint}>
        <div className="lsv-chart-header">
          <div><div className="lsv-overline">SIMULATED TREND</div><h2>{item.label}</h2></div>
          <div className="lsv-segment" role="group" aria-label="趋势时间范围">
            <button type="button" aria-pressed={range === 7} onClick={() => updateRange(7)}>7 天</button>
            <button type="button" aria-pressed={range === 14} onClick={() => updateRange(14)}>14 天</button>
          </div>
        </div>
        <div className="lsv-chart-number" aria-live="polite">第 {15 - range + activeDay} 天 <strong key={`${metric}-${range}-${activeDay}`}>{values[activeDay]} <small>{item.unit}</small></strong></div>
        <svg className="lsv-plot" viewBox="0 0 600 158" role="img" aria-label={`${item.label}最近 ${range} 天的模拟趋势；可通过图表下方按钮查看每日值`}>
          <defs><linearGradient id="lsv-trend-fill" x1="0" x2="0" y1="0" y2="1" gradientUnits="objectBoundingBox"><stop stopColor="currentColor" stopOpacity=".23" /><stop offset="1" stopColor="currentColor" stopOpacity="0" /></linearGradient></defs>
          {[29, 68, 107, 146].map((y) => <line key={y} x1="15" x2="585" y1={y} y2={y} className="lsv-gridline" />)}
          <g key={`${metric}-${range}`}>
            <polygon points={`15,146 ${line} 585,146`} className="lsv-trend-area" fill="url(#lsv-trend-fill)" />
            <polyline points={line} pathLength="1" className="lsv-trend-line" />
          </g>
          <line x1={focusPoint.x} x2={focusPoint.x} y1={focusPoint.y} y2="146" className="lsv-active-line" />
          <circle className="lsv-trend-halo" cx={focusPoint.x} cy={focusPoint.y} r="10" />
          <circle className="lsv-trend-point" cx={focusPoint.x} cy={focusPoint.y} r="5" />
        </svg>
        <div className="lsv-days" role="group" aria-label="查看单日模拟数据">
          {values.map((value, index) => (
            <button type="button" key={index} aria-pressed={activeDay === index} aria-label={`第 ${15 - range + index} 天，${value} ${item.unit}`} onClick={() => setActiveDay(index)}>{15 - range + index}</button>
          ))}
        </div>
        <p className="lsv-note">{item.note} 当前读数均为固定演示样本，未参与 Agent 理解。</p>
      </div>
    </section>
  );
}

const archiveFilters = ["all", "keep", "release", "next"] as const;
const archiveDate = new Intl.DateTimeFormat("zh-CN", { month: "long", day: "numeric" });

export function GrowthView({ cards, onOpenAgent }: { cards: readonly ExperienceCard[]; onOpenAgent: () => void }) {
  const [filter, setFilter] = useState<"all" | SupportChoice>("all");
  const visibleCards = [...cards].filter((card) => filter === "all" || filter === card.choice)
    .sort((left, right) => right.createdAt.localeCompare(left.createdAt));

  return (
    <section className="ls-support lsv-growth">
      <SectionHead icon={<BookOpen />} eyebrow="GROWTH ARCHIVE" title="成长档案" description="把你愿意带走的理解留成一张卡，在这次体验里随时回看。" badge="演示档案 · 当前页面内存" />
      <div className="lsv-archive-tools">
        <div className="lsv-segment" role="group" aria-label="按选择筛选经历卡片">
          {archiveFilters.map((key) => <button type="button" key={key} aria-pressed={key === filter} onClick={() => setFilter(key)}>{key === "all" ? "全部" : supportChoices[key].label}</button>)}
        </div>
        <span>{visibleCards.length} 张经历卡片</span>
      </div>
      {visibleCards.length === 0 ? (
        <div className="lsv-panel lsv-empty">
          <div className="lsv-empty-icon"><Sparkles size={30} /></div>
          <h2>{cards.length ? "这个筛选下还没有卡片" : "第一张卡，等你写下"}</h2>
          <p>{cards.length ? "试试其他选择，或回到对话留下一张新的经历卡。" : "在对话中确认或修正理解，选择 Keep / Release / Next，再把这段经历存进本页。"}</p>
          <button type="button" className="lsv-cta" onClick={onOpenAgent}>前往 Agent 对话<ArrowRight size={16} /></button>
        </div>
      ) : (
        <div className="lsv-card-list" key={filter}>
          {visibleCards.map((card) => {
            const Icon = choiceIcons[card.choice];
            return (
              <details className="lsv-panel lsv-experience" key={card.id}>
                <summary>
                  <span className="lsv-icon"><Icon size={18} /></span>
                  <span className="lsv-experience-title"><strong>{card.title}</strong><small>{archiveDate.format(new Date(card.createdAt))} · {supportChoices[card.choice].label} · {reflectionStatus[card.reflectionStatus]}</small></span>
                  <ArrowDownRight className="lsv-details-arrow" size={18} />
                </summary>
                <div className="lsv-experience-body">
                  <p>{card.action}</p>
                  <dl>{hypothesisKeys.map((key) => <div key={key}><dt>{layers[key].name}</dt><dd>{card.hypotheses[key] || "留白"}</dd></div>)}</dl>
                </div>
              </details>
            );
          })}
        </div>
      )}
      <p className="lsv-note lsv-footnote">卡片只在本次演示页面中保留。刷新页面后，档案会清空。</p>
    </section>
  );
}

type Candidate = { evidence: string; hypothesis: string; question: string; offers: readonly string[]; canDefer: boolean };
const syntheticCases = [
  {
    id: "CF-01", label: "没得到回应", input: "我发完方案后，群里一直没有人回复。我不知道是不是没讲明白。",
    sample: { evidence: "群里一直没有人回复", hypothesis: "也许你一边在等反馈，一边担心表达没有被接住。", question: "这种感觉贴近你吗？", offers: ["整理一个具体想问的问题", "先给自己一点等待的时间"], canDefer: true },
    baseline: { evidence: "群里的人否定了方案", hypothesis: "你太在意别人怎么评价你。", question: "", offers: ["马上催群里给结论"], canDefer: false },
  },
  {
    id: "CF-02", label: "安排被打乱", input: "朋友临时改约，我回了没关系，回到家还是有点闷。",
    sample: { evidence: "朋友临时改约", hypothesis: "可能是对原本期待的见面有些失落，也可能只是当时没顾上照顾自己。", question: "你更想怎么描述那一点闷？", offers: ["把期待写下来", "先休息一会儿再想"], canDefer: true },
    baseline: { evidence: "朋友临时改约", hypothesis: "可能对见面有点失落。", question: "你愿意再说一点吗？", offers: ["马上重新约时间"], canDefer: false },
  },
] satisfies { id: string; label: string; input: string; sample: Candidate; baseline: Candidate }[];

const caseRubric: { label: string; description: string; check: (input: string, candidate: Candidate) => boolean }[] = [
  {
    label: "事实有出处",
    description: "事实短句可在案例原话中找到。",
    check: (input, candidate) => Boolean(candidate.evidence) && input.includes(candidate.evidence),
  },
  {
    label: "假设可纠正",
    description: "解释使用不确定措辞，并邀请当事人确认。",
    check: (_, candidate) => /^(可能|也许)/.test(candidate.hypothesis) && /[？?]$/.test(candidate.question),
  },
  {
    label: "支持保留选择权",
    description: "提供至少两种支持选项，并允许暂不决定。",
    check: (_, candidate) => candidate.offers.length >= 2 && candidate.canDefer,
  },
];

export function CaseForgeView() {
  const [caseIndex, setCaseIndex] = useState(0);
  const [variant, setVariant] = useState<"sample" | "baseline">("sample");
  const [result, setResult] = useState<boolean[] | null>(null);
  const currentCase = syntheticCases[caseIndex];
  const candidate = currentCase[variant];

  return (
    <section className="ls-support lsv-caseforge">
      <SectionHead icon={<FlaskConical />} eyebrow="CASEFORGE / INTERNAL LAB" title="CaseForge" description="用独立的合成案例，演示怎样检查回应是否保留证据、修正入口与选择权。" badge="内部研发 · 受控合成案例" />
      <div className="lsv-case-layout">
        <article className="lsv-panel lsv-case-sample">
          <div className="lsv-overline">CONTROLLED FIXTURES</div>
          <h2>选择一条案例</h2>
          <div className="lsv-case-list" role="group" aria-label="合成案例">
            {syntheticCases.map((item, index) => <button type="button" key={item.id} aria-pressed={caseIndex === index} onClick={() => { setCaseIndex(index); setResult(null); }}><span>{item.id}</span>{item.label}</button>)}
          </div>
          <blockquote>{currentCase.input}</blockquote>
          <div className="lsv-segment" role="group" aria-label="待检查回应">
            <button type="button" aria-pressed={variant === "sample"} onClick={() => { setVariant("sample"); setResult(null); }}>示范回应</button>
            <button type="button" aria-pressed={variant === "baseline"} onClick={() => { setVariant("baseline"); setResult(null); }}>反例</button>
          </div>
          <dl className="lsv-case-output">
            <div><dt>事实短句</dt><dd>{candidate.evidence}</dd></div>
            <div><dt>解释与邀请</dt><dd>{candidate.hypothesis} {candidate.question}</dd></div>
            <div><dt>支持选项</dt><dd>{candidate.offers.join("；")}{candidate.canDefer ? "；也可以稍后决定" : ""}</dd></div>
          </dl>
        </article>
        <article className="lsv-panel lsv-checks">
          <div className="lsv-overline">DEMO RUBRIC</div>
          <h2>回应规则检查</h2>
          <ul aria-live="polite">
            {caseRubric.map(({ label, description }, index) => {
              const passed = result?.[index];
              const icon = passed === undefined ? <CircleHelp size={16} /> : passed ? <Check size={16} /> : <X size={16} />;
              const status = passed === undefined ? "待运行" : passed ? "通过" : "需复核";
              return (
                <li key={`${label}-${passed}`} data-result={passed}>
                  <span className="lsv-icon" data-check={String(passed)}>{icon}</span>
                  <span><strong>{label}</strong><small>{description}</small></span>
                  <span className="lsv-check-status">{status}</span>
                </li>
              );
            })}
          </ul>
          <button type="button" className="lsv-cta" onClick={() => setResult(caseRubric.map(({ check }) => check(currentCase.input, candidate)))}>运行本地检查<ArrowRight size={16} /></button>
        </article>
      </div>
      <p className="lsv-note lsv-footnote">固定案例与固定回应仅用于演示规则。此页独立于个人会话，未调用真实模型，也未接入后端批量评测。</p>
    </section>
  );
}
