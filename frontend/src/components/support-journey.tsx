"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { Activity, ArrowRight, Check, ChevronDown, CircleHelp, HeartHandshake, Layers3, LoaderCircle, LockKeyhole, RefreshCw, Sparkles, X } from "lucide-react";
import type { Scope, SendSupportMessage, SupportResponse, TrendSeries, UnderstandingView, WearableProvider } from "../lib/support-types";
import { availabilityNames, chartSegments, limitationText, metricDisplay, metricName, metricText } from "./support-journey-state";
import type { JourneyController } from "./use-support-journey";
import { WorldMedia } from "./world-media";
import "./support-journey.css";

type JourneyView = "agent" | "canvas" | "signals" | "archive";
const scenes = {
  agent: { title: "A space to be heard.", subtitle: "和拾光一起，慢慢校准对你的理解。", kicker: "PERSONAL AGENT", poster: "/images/agent-bg.jpg", video: "https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260328_115001_bcdaa3b4-03de-47e7-ad63-ae3e392c32d4.mp4" },
  canvas: { title: "给理解一个形状。", subtitle: "核对来源，修改理解，再决定哪些可以带入下一次对话。", kicker: "MIND CANVAS", poster: "/images/canvas-bg.jpg", video: "https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260317_100335_dc625816-c3c1-4b00-b93e-4cb301cf5ea5.mp4" },
  signals: { title: "Listen to your rhythm.", subtitle: "设备提供观察，感受仍由你命名。", kicker: "PERSONAL SIGNALS", poster: "/images/signals-hero.jpg", video: "https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260324_151826_c7218672-6e92-402c-9e45-f1e0f454bdc4.mp4" },
  archive: { title: "What you choose to keep.", subtitle: "由你决定保存、打开，或删除一段旅程。", kicker: "GROWTH ARCHIVE", poster: "/images/archive-bg.jpg", video: "https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260330_145725_08886141-ed95-4a8e-8d6d-b75eaadce638.mp4" },
};
const routeNames = { routine_support: "日常支持", clarify_safety: "先核对当前安全情况", safety_support: "优先照顾当前安全", urgent_real_world_help: "优先寻求及时的现实帮助" };
const phaseNames: Record<string, string> = { IDLE: "等待新的输入", RUNNING: "正在准备支持", WAITING_CONFIRMATION: "等待确认", COMPLETED: "本轮已完成", CANCELLED: "本轮已停止", FAILED: "本轮未完成" };
const sourceNames: Record<string, string> = { user_report: "你的自述", wearable_record: "设备观察", understanding: "既有理解", draft: "已确认草稿" };
const dateTime = new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" });
const initialInput = "最近我的作息变了，我想先梳理一下发生了什么。";

function ProviderConsent({ provider, disabled, onChange }: { provider: WearableProvider; disabled: boolean; onChange: (metrics: string[], scopes: Scope[]) => void }) {
  const [metrics, setMetrics] = useState(provider.allowed_metrics.length ? provider.allowed_metrics : provider.available_metrics);
  const [scopes, setScopes] = useState<Scope[]>(provider.scopes);
  return <article className="sj-provider">
    <div className="sj-row"><strong>{provider.is_simulated ? "模拟设备" : "设备来源"} · {provider.provider}</strong><span>{provider.record_count} 条记录</span></div>
    <div className="sj-consents">
      <label><input type="checkbox" checked={scopes.includes("local_analysis")} disabled={disabled} onChange={(e) => setScopes(e.target.checked ? [...scopes, "local_analysis"] : scopes.filter((s) => s !== "local_analysis"))} />允许在本机展示趋势与分析</label>
      <label><input type="checkbox" checked={scopes.includes("model_context")} disabled={disabled} onChange={(e) => setScopes(e.target.checked ? [...scopes, "model_context"] : scopes.filter((s) => s !== "model_context"))} />允许把必要的设备观察用于支持对话</label>
    </div>
    <details><summary>选择允许使用的指标 · {metrics.length} 项 <ChevronDown size={14} /></summary>
      <div className="sj-metric-checks">{provider.available_metrics.map((id) => <label key={id}><input type="checkbox" checked={metrics.includes(id)} disabled={disabled} onChange={(e) => setMetrics(e.target.checked ? [...metrics, id] : metrics.filter((m) => m !== id))} />{metricName(id)}</label>)}</div>
    </details>
    <div className="sj-actions"><button className="sj-button" type="button" disabled={disabled} onClick={() => onChange(metrics, scopes)}>保存用途选择</button><button className="sj-button sj-secondary" type="button" disabled={disabled || !provider.scopes.length} onClick={() => onChange([], [])}>撤回该设备全部用途</button></div>
    <p className="sj-small">勾选后需点击保存才生效。导入、展示和模型使用分别控制；撤回不等于从磁盘存档删除。</p>
  </article>;
}

function TrendChart({ series }: { series: TrendSeries }) {
  const segments = chartSegments(series);
  const latest = [...series.points].reverse().find((p) => typeof p.value === "number" && p.quality === "valid");
  const shown = latest && typeof latest.value === "number" ? metricDisplay(series.metric_id, latest.value, series.unit) : null;
  return <div className="sj-trend">
    <div className="sj-row"><div><p className="sj-eyebrow">{series.source?.is_simulated ? "SIMULATED OBSERVATION" : "DEVICE OBSERVATION"}</p><h3>{metricName(series.metric_id)}</h3></div><span className="sj-tag">{availabilityNames[series.status] ?? series.status}</span></div>
    {shown && <p className="sj-reading">{shown.value.toLocaleString("zh-CN", { maximumFractionDigits: 1 })}<small>{shown.unit} · {latest?.date}</small></p>}
    {segments.length ? <svg viewBox="0 0 480 150" className="sj-chart" role="img" aria-label={`${metricName(series.metric_id)}的授权记录，缺失处断开`}>
      {[32, 76, 120].map((y) => <line key={y} x1="18" x2="462" y1={y} y2={y} className="sj-chart-grid" />)}
      {segments.map((points, i) => <g key={i}><polyline fill="none" points={points.map((p) => `${p.x},${p.y}`).join(" ")} />{points.map((p) => <circle key={p.date} cx={p.x} cy={p.y} r="3"><title>{p.date}: {p.value.toLocaleString("zh-CN", { maximumFractionDigits: 1 })}</title></circle>)}</g>)}
    </svg> : <p className="sj-empty">{availabilityNames[series.status] ?? "当前没有可绘制的授权记录"}。缺失读数不会补为零。</p>}
    <p className="sj-small">{series.source ? `${series.source.provider} · ${series.source.measurement_method} · 同一来源的观察` : "来源尚不可用"}。这些读数不代表心理诊断。</p>
    <details><summary>查看逐日读数 <ChevronDown size={14} /></summary><div className="sj-table-wrap"><table><thead><tr><th>日期</th><th>读数</th><th>质量</th></tr></thead><tbody>{series.points.map((point) => { const display = typeof point.value === "number" ? metricDisplay(series.metric_id, point.value, series.unit) : null; return <tr key={point.date}><td>{point.date}</td><td>{display ? `${display.value.toLocaleString("zh-CN", { maximumFractionDigits: 1 })} ${display.unit}` : point.value ?? "缺失"}</td><td>{point.quality === "valid" ? "有效" : point.quality}</td></tr>; })}</tbody></table></div></details>
  </div>;
}

function UnderstandingCard({ view, journey, onCorrect }: { view: UnderstandingView; journey: JourneyController; onCorrect: (u: UnderstandingView) => void }) {
  const u = view.understanding;
  const [text, setText] = useState(u.interpretation);
  const dirty = text.trim() !== u.interpretation;
  useEffect(() => setText(u.interpretation), [u.revision, u.interpretation]);
  const blocked = !!journey.action || journey.state?.session.status !== "ACTIVE";
  return <article className="sj-card sj-understanding" data-confirmed={u.status === "confirmed"}>
    <div className="sj-row"><span className="sj-tag">{u.status === "confirmed" ? "你已确认" : "待你核对"} · 版本 {u.revision}</span><span className="sj-small">{u.origin === "user_report" ? "来自你的自述" : "Agent 提出的假设"}</span></div>
    <label className="sj-label">当前理解<textarea maxLength={2000} value={text} onChange={(e) => setText(e.target.value)} disabled={blocked} rows={3} /></label>
    <details><summary>查看来源 <ChevronDown size={14} /></summary><ul className="sj-evidence">{u.evidence_refs.map((ref) => <li key={`${ref.kind}:${ref.source_id}`}><span>{sourceNames[ref.kind] ?? ref.kind}</span><code>{ref.source_id}</code><small>版本 {ref.revision}</small></li>)}</ul></details>
    <div className="sj-actions">
      <button className="sj-button" disabled={blocked || !dirty || !text.trim()} type="button" onClick={() => void journey.edit(view, text)}>提交修改</button>
      <button className="sj-button sj-secondary" disabled={blocked || journey.pending || dirty || u.status === "confirmed" || !view.confirmation_token} type="button" onClick={() => void journey.decide(view, "confirm")}><Check size={15} />确认当前版本</button>
      <button className="sj-link" disabled={blocked || dirty} type="button" onClick={() => onCorrect(view)}>在对话中纠正</button>
      <button className="sj-link" disabled={blocked} type="button" onClick={() => void journey.decide(view, "reject")}>拒绝这份理解</button>
    </div>
    <p className="sj-small">修改会撤销旧确认。来源保留，只有重新确认的当前版本才用于后续对话。</p>
  </article>;
}

export function SupportJourney({ view, journey, onNavigate }: { view: JourneyView; journey: JourneyController; onNavigate: (view: JourneyView) => void }) {
  const [input, setInput] = useState("");
  const [sendConsent, setSendConsent] = useState(false);
  const [correction, setCorrection] = useState<UnderstandingView | null>(null);
  const [understandingText, setUnderstandingText] = useState("");
  const [scenario, setScenario] = useState("S02");
  const [metric, setMetric] = useState("sleep_duration_minutes");
  const [saveConsent, setSaveConsent] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const lastMessage = useRef<HTMLDivElement>(null);
  const inputVersion = useRef(0);
  const understandingVersion = useRef(0);
  const session = journey.state?.session;
  const disabled = !!journey.action;
  const active = session?.status === "ACTIVE";
  const ready = journey.health?.runtime.psychology_enabled === true;
  const fixture = journey.health?.runtime.psychology_demo_mode === true;
  const route = journey.state ? journey.state.support_route : null;
  const safety = route !== null && route !== "routine_support";
  const confirmed = journey.state?.understandings.some((item) => item.understanding.status === "confirmed");
  const worldState = safety ? "support" : confirmed ? "confirmed" : journey.state?.understandings.length ? "proposed" : "idle";
  const result = journey.response?.revision === session?.revision ? journey.response?.psychology?.result : null;
  const scene = scenes[view];
  const selectedSeries = journey.trends?.series.find((series) => series.metric_id === metric);
  const correctionCurrent = !correction || !!journey.state?.understandings.some((item) => item.understanding.understanding_id === correction.understanding.understanding_id && item.understanding.revision === correction.understanding.revision);

  useEffect(() => { if (view === "agent" && session?.messages.length) lastMessage.current?.scrollIntoView({ block: "nearest", behavior: "smooth" }); }, [session?.messages.length, view]);
  useEffect(() => { ++inputVersion.current; ++understandingVersion.current; setCorrection(null); setSendConsent(false); setSaveConsent(false); setInput(""); setUnderstandingText(""); }, [session?.session_id]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!input.trim() || !active || !ready || !sendConsent || !correctionCurrent || journey.pending || disabled) return;
    setSendConsent(false);
    const target = correction?.understanding;
    const body: SendSupportMessage = { content: input.trim(), request_id: crypto.randomUUID(), ...(target ? { correction_of: { understanding_id: target.understanding_id, expected_revision: target.revision } } : {}) };
    await finishMessage(journey.send(body), body);
  }

  async function finishMessage(operation: Promise<SupportResponse | undefined>, body: SendSupportMessage) {
    const version = inputVersion.current;
    const submittedInput = input;
    const result = await operation;
    // Do not erase a newer draft, a different retry draft, or an unacknowledged request.
    if (!result || version !== inputVersion.current || submittedInput.trim() !== body.content) return;
    ++inputVersion.current;
    setInput("");
    const target = body.correction_of;
    if (target) setCorrection((current) => current && current.understanding.understanding_id === target.understanding_id
      && current.understanding.revision === target.expected_revision ? null : current);
  }

  async function retry(same: boolean) {
    if (!journey.lastAttempt || !sendConsent) return;
    setSendConsent(false);
    await finishMessage(journey.retry(same), journey.lastAttempt);
  }

  async function propose(event: FormEvent) {
    event.preventDefault();
    const version = understandingVersion.current;
    const result = await journey.propose(understandingText);
    if (result && version === understandingVersion.current) { ++understandingVersion.current; setUnderstandingText(""); }
  }

  function correct(item: UnderstandingView) {
    ++inputVersion.current;
    setCorrection(item); setInput(""); setSendConsent(false); onNavigate("agent");
  }

  return <section className="sj" data-view={view} data-state={worldState} aria-labelledby="sj-title">
    <div className="sj-hero">
      <WorldMedia src={scene.video} poster={scene.poster} position={view === "archive" ? "right" : undefined} />
      <div className="sj-hero-wash" />
      <div className="sj-hero-content"><p className="sj-eyebrow">LOVESTONE / {scene.kicker}</p><h1 id="sj-title">{scene.title}</h1><p>{scene.subtitle}</p>
        <div className="sj-badges"><span className="sj-tag">{fixture ? "离线工程演示 · 固定模型" : "后端支持流程"}</span><span className="sj-tag">专业审核待完成</span></div>
      </div>
    </div>
    <div className="sj-content">
      <div className="sj-toolbar">
        <div><span className="sj-state-dot" /><strong>{session ? session.status === "ENDED" ? "这段旅程已结束" : phaseNames[session.workflow.phase] ?? session.workflow.phase : "准备开启旅程"}</strong><p>{fixture ? "模拟设备与固定回应走完整后端流程，不调用真实模型。" : "请只用虚构输入。本会话的历史与确认理解会参与后续支持。"}</p></div>
        <div className="sj-actions"><button className="sj-icon" aria-label="刷新后端状态" disabled={disabled} onClick={() => void journey.refreshAll()}><RefreshCw size={17} /></button>
          {!active && <button className="sj-button" disabled={disabled || !ready || journey.pending} onClick={() => void journey.start()}>开启新的旅程<ArrowRight size={15} /></button>}
          {active && <button className="sj-button sj-secondary" disabled={disabled} onClick={() => void journey.end()}>结束旅程</button>}
          {session && <button className="sj-link" disabled={disabled} onClick={() => setConfirmClear(!confirmClear)}>清空当前旅程</button>}
        </div>
      </div>
      {!ready && <div className="sj-callout"><CircleHelp size={18} /><div><strong>等待后端支持流程</strong><p>{journey.health ? "请在服务端启用 PSYCHOLOGY_ENABLED。可同时启用 PSYCHOLOGY_DEMO_MODE，使用固定回应验证流程。" : "正在读取本机后端配置。若连接失败，请检查服务和前端中转地址后刷新。"}</p></div></div>}
      {confirmClear && session && <div className="sj-callout"><LockKeyhole size={18} /><div><p>清空将移除这段旅程在后端内存中的对话、理解、设备与授权。已保存的磁盘存档仍保留。</p><div className="sj-actions"><button className="sj-button" disabled={disabled} onClick={() => { setConfirmClear(false); setCorrection(null); void journey.clear(); }}>确认清空后端内容</button><button className="sj-link" onClick={() => setConfirmClear(false)}>保留</button></div></div></div>}
      {journey.error && <div className="sj-alert" role="alert">{journey.error}</div>}
      {journey.notice && <p className="sj-notice" role="status">{journey.notice}</p>}
      {journey.action && <p className="sj-notice" role="status"><LoaderCircle size={16} className="sj-spin" />{journey.action}…</p>}

      <details className={`sj-support-entry ${safety ? "sj-support-priority" : ""}`} open={safety || undefined}>
        <summary><HeartHandshake size={18} /><span>{safety && route ? routeNames[route] : "现实支持入口 · 随时可用"}</span><ChevronDown size={15} /></summary>
        <p>{journey.support.message}</p><ul>{journey.support.actions.map((item) => <li key={item}>{item}</li>)}</ul>
        <p className="sj-small">不需要先填完画布或确认理解。此处不会自动联系他人，支持流程与文案仍待专业审核。</p>
      </details>

      {view === "agent" && <div className="sj-two-column">
        <div className="sj-stack">
          <div className="sj-card sj-conversation" aria-label="当前旅程对话">
            {session?.messages.length ? session.messages.map((message, index) => <article key={`${session.session_id}:${index}`} className={`sj-message sj-${message.role}`}><span>{message.role === "user" ? "你的自述" : "拾光的回应 · 未经专业审核"}</span><p>{message.content}</p></article>) : <p className="sj-empty">说说一段虚构经历，或先去身心状态页导入模拟记录。你可以随时修改拾光的理解。</p>}
            <div ref={lastMessage} />
          </div>
          {result && <div className="sj-card sj-result"><div className="sj-row"><strong>{routeNames[result.route]}</strong><span className="sj-tag">当前回应</span></div>
            {result.uncertainties.length > 0 && <><h3>还不确定的部分</h3><ul>{result.uncertainties.map((item, i) => <li key={i}>{item}</li>)}</ul></>}
            {result.suggested_next_step && <p><strong>可选下一步：</strong>{result.suggested_next_step}</p>}{result.follow_up_offer && <p>{result.follow_up_offer}</p>}
            <details><summary>查看本轮来源记录 <ChevronDown size={14} /></summary><p className="sj-small">{result.evidence_refs.join(" · ")}</p><p className="sj-small">来源只说明本轮使用了哪些输入，不代表回应已得到事实或专业验证。</p></details>
          </div>}
          <form className="sj-card sj-composer" onSubmit={submit}>
            <label className="sj-label" htmlFor="sj-input">和拾光继续聊聊</label>
            {correction && <div className="sj-correction"><span>纠正理解：{correction.understanding.interpretation}</span><button type="button" className="sj-icon" aria-label="取消纠正目标" onClick={() => { ++inputVersion.current; setCorrection(null); }}><X size={15} /></button></div>}
            {!correctionCurrent && <p className="sj-notice">这份理解已更新或失效。请取消当前目标，或从画布重新选择需要纠正的版本。</p>}
            <textarea id="sj-input" placeholder={correction ? "请写出你希望替换成的理解，例如：其实最近是旅行，不是备考。" : "此刻，有什么想说的吗？请仅输入虚构内容。"} maxLength={2000} value={input} onChange={(event) => { ++inputVersion.current; setInput(event.target.value); }} disabled={!active || journey.pending || disabled} rows={4} />
            <label className="sj-check"><input type="checkbox" checked={sendConsent} disabled={!active || journey.pending || disabled} onChange={(e) => setSendConsent(e.target.checked)} />{fixture ? "使用本次虚构输入与本会话上下文，运行离线支持流程" : "我同意将本次虚构输入、本会话历史和已确认理解用于后端模型支持。设备用途另行选择。"}</label>
            <div className="sj-actions"><button className="sj-button" disabled={!active || !ready || !input.trim() || !sendConsent || !correctionCurrent || journey.pending || disabled} type="submit">{correction ? "提交纠正并继续" : "发送给拾光"}<ArrowRight size={16} /></button>
              {journey.pending && <button className="sj-button sj-secondary" type="button" disabled={disabled} onClick={() => void journey.stop()}><X size={15} />停止这次生成</button>}
              <button type="button" className="sj-link" disabled={!active || journey.pending || disabled} onClick={() => { ++inputVersion.current; setInput(initialInput); }}><Sparkles size={14} />使用虚构示例</button>
            </div>
            {journey.failedAttempt && !journey.pending && <div className="sj-retry"><p>请重新勾选本次用途同意。响应可能丢失时，可先重取原请求结果；明确重新生成会使用新请求编号。</p><button className="sj-link" type="button" disabled={disabled || !active || !sendConsent} onClick={() => void retry(true)}>重取本次结果</button><button className="sj-link" type="button" disabled={disabled || !active || !sendConsent} onClick={() => void retry(false)}>重新尝试一次</button></div>}
          </form>
        </div>
        <aside className="sj-card sj-world-state"><div className="sj-orbit" aria-hidden="true"><i /><i /><i /></div><p className="sj-eyebrow">静水湾 / CURRENT UNDERSTANDING</p><h2>{safety ? "先陪你照顾此刻。" : confirmed ? "你确认的理解，已被听见。" : worldState === "proposed" ? "水面起了涟漪。" : "留一点空间给自己。"}</h2><p>{safety ? "现实支持始终在这里。你不需要先完成画布。" : confirmed ? "场景跟随后端确认状态变化。新的纠正会撤销旧确认，拾光将等待你重新核对。" : "把你认同的理解放进画布，再看看下一轮对话是否贴近你。"}</p><button className="sj-button sj-secondary" onClick={() => onNavigate("canvas")}><Layers3 size={15} />查看理解画布</button></aside>
      </div>}

      {view === "canvas" && <div className="sj-stack">
        <div className="sj-callout"><Layers3 size={20} /><div><strong>当前接入：理解条目、来源与确认版本</strong><p>四栏心理结构尚未定稿。这里展示后端实际保存的理解，不把普通回复拆成事实、情绪或需要。</p></div></div>
        <div className="sj-understanding-grid">{journey.state?.understandings.map((item) => <UnderstandingCard key={item.understanding.understanding_id} view={item} journey={journey} onCorrect={correct} />)}</div>
        {!journey.state?.understandings.length && <p className="sj-empty">还没有当前有效的理解。可以用自己的话记录一条，再确认它是否贴近你。</p>}
        <form className="sj-card" onSubmit={propose}><label className="sj-label" htmlFor="sj-understanding">用自己的话补充理解</label><textarea id="sj-understanding" maxLength={2000} rows={3} value={understandingText} onChange={(e) => { ++understandingVersion.current; setUnderstandingText(e.target.value); }} placeholder="例如：最近睡得少，是因为旅行安排，不是备考压力。" disabled={!active || disabled || journey.pending} /><div className="sj-actions"><button className="sj-button" type="submit" disabled={!active || disabled || journey.pending || !understandingText.trim()}>记录为待确认理解</button><button className="sj-link" type="button" onClick={() => onNavigate("agent")}>回到对话</button></div><p className="sj-small">这条文字会作为你的自述保留在本次旅程中；提出理解不会自动确认或写入磁盘。</p></form>
      </div>}

      {view === "signals" && <div className="sj-stack">
        <section className="sj-card"><div className="sj-row"><div><p className="sj-eyebrow">SIMULATED WEARABLES</p><h2>从一段模拟记录开始</h2></div><Activity size={24} /></div><p>生成 21 个完整日的多指标记录。场景答案不会送给模型；每个用途需要你单独允许。</p><div className="sj-actions"><label className="sj-inline-label">模拟编号<select value={scenario} onChange={(e) => setScenario(e.target.value)} disabled={!active || journey.pending || disabled}>{(journey.catalog?.scenarios ?? [{ scenario_id: "S02", label: "模拟场景 S02" }]).map((item) => <option key={item.scenario_id} value={item.scenario_id}>{item.label}</option>)}</select></label><button className="sj-button" disabled={!active || journey.pending || disabled || !!journey.state?.wearables.record_count} onClick={() => void journey.simulate(scenario)}>导入模拟设备记录</button></div>{!!journey.state?.wearables.record_count && <p className="sj-small">当前已有 {journey.state.wearables.record_count} 条记录。更换模拟场景请先清空或开启新旅程，避免混合来源。</p>}</section>
        {journey.state?.wearables.providers.map((provider) => <div className="sj-card" key={`${provider.provider}:${journey.state?.wearables.revision}`}><ProviderConsent provider={provider} disabled={disabled || !active} onChange={(metrics, scopes) => void journey.consent(provider, metrics, scopes)} /></div>)}
        <section className="sj-card"><div className="sj-row"><div><p className="sj-eyebrow">YOUR AUTHORIZED OBSERVATIONS</p><h2>个人趋势</h2></div><div className="sj-segment" aria-label="趋势时间范围">{([7, 14, 30] as const).map((days) => <button type="button" key={days} aria-pressed={journey.range === days} onClick={() => journey.setRange(days)}>{days} 天</button>)}</div></div><label className="sj-inline-label">指标<select value={metric} onChange={(e) => setMetric(e.target.value)}>{(journey.catalog?.metrics.filter((item) => item.value_type !== "timestamp") ?? [{ metric_id: "sleep_duration_minutes" }]).map((item) => <option key={item.metric_id} value={item.metric_id}>{metricName(item.metric_id)}</option>)}</select></label>{selectedSeries ? <TrendChart series={selectedSeries} /> : <p className="sj-empty">导入记录并允许本地分析后，这里会展示可用趋势。</p>}</section>
        <section className="sj-card"><p className="sj-eyebrow">PERSONAL BASELINE</p><h2>与自己的基线比较</h2><p className="sj-small">基线 14 日与近期 3 日分开计算；记录不足、质量不足和来源冲突都会明确说明。</p>{journey.analysis?.observations.length ? <div className="sj-observations">{journey.analysis.observations.map((item) => { const base = metricText(item.metric_id, item.baseline_median, item.unit), recent = metricText(item.metric_id, item.recent_median, item.unit); return <article key={item.metric_id}><h3>{metricName(item.metric_id)}</h3><p><span>基线 {base.text}</span><ArrowRight size={15} /><strong>{recent.text} {recent.unit}</strong></p><small>{item.baseline_valid_days} 个基线有效日 / {item.recent_valid_days} 个近期有效日</small></article>; })}</div> : <p className="sj-empty">{availabilityNames[journey.analysis?.status ?? "no_data"] ?? "尚无可用分析"}</p>}{journey.analysis?.limitations.map((item) => <p className="sj-small" key={item}>{limitationText(item)}</p>)}{!!journey.analysis?.metric_statuses.some((item) => item.status !== "ready") && <details><summary>查看未生成变化结论的指标 <ChevronDown size={14} /></summary><ul>{journey.analysis.metric_statuses.filter((item) => item.status !== "ready").map((item) => <li key={item.metric_id}>{metricName(item.metric_id)}：{availabilityNames[item.status] ?? item.status}</li>)}</ul></details>}</section>
      </div>}

      {view === "archive" && <div className="sj-stack"><section className="sj-card"><p className="sj-eyebrow">ONLY WHEN YOU CHOOSE</p><h2>保存当前旅程到本机</h2><p>保存对话、理解、自述和设备记录，供之后打开。文件以明文保存在运行后端的本机；不会因此进入案例库或训练数据。</p><label className="sj-check"><input type="checkbox" checked={saveConsent} disabled={!session || journey.pending || disabled} onChange={(e) => setSaveConsent(e.target.checked)} />我选择把当前旅程及业务数据保存到本机磁盘</label><button className="sj-button" disabled={!session || journey.pending || disabled || !saveConsent} onClick={() => { setSaveConsent(false); void journey.save(); }}>保存当前旅程</button><p className="sj-small">重新从磁盘恢复时，设备用途授权需重新选择。刷新网页只会重置网页状态，不会自动删除后端内存或磁盘存档。</p></section>
        <section className="sj-card"><div className="sj-row"><h2>本机存档</h2><button className="sj-icon" aria-label="刷新存档" disabled={disabled} onClick={() => void journey.refreshAll()}><RefreshCw size={17} /></button></div>{journey.archives.length ? <div className="sj-archives">{journey.archives.map((entry) => <article key={entry.archive_id}><div><strong>{dateTime.format(new Date(entry.saved_at))}</strong><p>{entry.message_count / 2} 轮已完成对话 · {entry.snapshot_version === 2 ? "含业务数据" : "普通会话"} · {entry.status === "ENDED" ? "已结束" : "可继续"}</p></div><div className="sj-actions"><button className="sj-button sj-secondary" disabled={journey.pending || disabled} onClick={() => void journey.load(entry)}>打开</button>{deleteId === entry.archive_id ? <><button className="sj-link" disabled={disabled} onClick={() => { setDeleteId(null); void journey.removeArchive(entry); }}>确认删除磁盘存档</button><button className="sj-link" onClick={() => setDeleteId(null)}>保留</button></> : <button className="sj-link" disabled={disabled} onClick={() => setDeleteId(entry.archive_id)}>删除</button>}</div></article>)}</div> : <p className="sj-empty">还没有保存到本机的旅程。保存始终由你主动选择。</p>}</section>
      </div>}
      <p className="sj-footer-note"><LockKeyhole size={13} />本机单用户联调 · 请仅使用虚构内容 · 当前流程未经专业审核</p>
    </div>
  </section>;
}
