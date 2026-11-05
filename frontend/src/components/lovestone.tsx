"use client";

import Image from "next/image";
import {
  Activity, Aperture, ArrowDownRight, ArrowLeft, ArrowRight, BookOpen,
  Check, ChevronRight, CircleHelp, Compass, Feather, FlaskConical, Focus, HeartHandshake,
  Layers3, LockKeyhole, Menu, MessageCircle, Orbit, Pencil, Sparkles, Star, X,
} from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";
import {
  type ExperienceCard, type HypothesisFields, type Reflection, type SupportChoice, type SupportOutcome,
  chooseSupport, createFastApiAgent, demoAgent, demoSample, hypothesisKeys, hypothesisLabels,
  maxHypothesisLength, maxInputLength, reviseReflection, saveExperience, supportChoices,
} from "@/lib/agent";
import { CinematicHome } from "./cinematic-home";
import { CaseForgeView } from "./support-views";
import { CinematicCanvas, CinematicArchive } from "./cinematic-reflections";
import { CinematicSignals } from "./cinematic-signals";
import { WorldMedia } from "./world-media";
import { SupportJourney } from "./support-journey";
import { useSupportJourney } from "./use-support-journey";
import "./world-pages.css";

const nav = [
  { id: "universe", label: "心象宇宙", icon: Orbit },
  { id: "agent", label: "拾光对话", icon: MessageCircle },
  { id: "canvas", label: "心象画布", icon: Layers3 },
  { id: "signals", label: "身心状态", icon: Activity },
  { id: "archive", label: "成长档案", icon: BookOpen },
] as const;
type MainView = (typeof nav)[number]["id"];
type View = MainView | "caseforge";
type Mode = "demo" | "fastapi" | "support";
const choices = ["keep", "release", "next"] as const;
const choiceIcons = { keep: Star, release: Feather, next: Compass };
const scene = {
  idle: { title: "拾光 · 静水湾", text: "一位在静水湾收集微光的旅伴。", tag: "静水湾", index: "01" },
  proposed: { title: "拾光 · 水面起了涟漪", text: "它把听见的片段摆在水面，等你重新命名。", tag: "等你确认", index: "02" },
  confirmed: { title: "拾光 · 水面清澈了", text: "你给出的理解，让拾光知道该怎样陪你。", tag: "理解已确认", index: "03" },
  keep: { title: "拾光 · 把光留在这里", text: "拾光在湾里留下一盏灯，照着你想带走的部分。", tag: "留下一盏灯", index: "04" },
  release: { title: "拾光 · 让水带走一点", text: "拾光把那片涟漪送向远处，陪你腾出一点空间。", tag: "水面渐静", index: "04" },
  next: { title: "拾光 · 岸边亮了一步", text: "拾光只照亮身前的一小步，什么时候走由你决定。", tag: "照亮下一步", index: "04" },
};

function readView(): View {
  const key = window.location.hash.slice(1);
  return key === "caseforge" || nav.some((item) => item.id === key) ? key as View : "universe";
}

function SceneWave({ state }: { state: keyof typeof scene }) {
  if (state === "idle") return null;
  return <div className="wave-layer" aria-hidden="true" key={state}>
    {[0, 1, 2].map((ring) => <i key={ring} className="scene-wave" data-choice={state} />)}
  </div>;
}

export function LoveStone({ liveEnabled }: { liveEnabled: boolean }) {
  const [view, setView] = useState<View>("universe");
  const [mode, setMode] = useState<Mode>("demo");
  const journey = useSupportJourney(mode === "support");
  const [input, setInput] = useState("");
  const [consent, setConsent] = useState(false);
  const [draft, setDraft] = useState<Reflection | null>(null);
  const [fields, setFields] = useState<HypothesisFields | null>(null);
  const [outcome, setOutcome] = useState<SupportOutcome | null>(null);
  const [cards, setCards] = useState<ExperienceCard[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [privacy, setPrivacy] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const [backgroundPaused, setBackgroundPaused] = useState(false);
  const focusDestination = useRef(false);
  const request = useRef<AbortController | null>(null);
  const main = useRef<HTMLElement>(null);
  const confirmRef = useRef<HTMLHeadingElement>(null);
  const inputField = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const field = inputField.current;
    if (!field || view !== "agent") return;
    field.style.height = "46px";
    field.style.height = `${Math.min(field.scrollHeight, 180)}px`;
  }, [input, view]);

  useEffect(() => {
    if (focusDestination.current) {
      main.current?.focus({ preventScroll: true });
      focusDestination.current = false;
    }
  }, [view]);

  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (menuOpen) {
        setMenuOpen(false);
        document.getElementById("world-menu-toggle")?.focus();
      } else if (privacy) {
        setPrivacy(false);
        document.getElementById("world-privacy-toggle")?.focus();
      }
    };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [menuOpen, privacy]);

  useEffect(() => {
    const sync = () => setView(readView());
    sync();
    window.addEventListener("hashchange", sync);
    return () => {
      window.removeEventListener("hashchange", sync);
      request.current?.abort();
    };
  }, []);

  useEffect(() => {
    const update = () => setScrolled(window.scrollY > 60);
    update();
    window.addEventListener("scroll", update, { passive: true });
    return () => window.removeEventListener("scroll", update);
  }, []);

  useEffect(() => {
    if (!("IntersectionObserver" in window) || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const observer = new IntersectionObserver((entries) => {
      entries.forEach(({ target, isIntersecting }) => {
        if (!isIntersecting) return;
        target.classList.remove("waiting");
        observer.unobserve(target);
      });
    }, { threshold: .08 });
    main.current?.querySelectorAll("[data-reveal]").forEach((element) => {
      if (element.getBoundingClientRect().top < window.innerHeight) return;
      element.classList.add("waiting");
      observer.observe(element);
    });
    return () => observer.disconnect();
  }, [view]);

  function go(next: View) {
    setMenuOpen(false);
    if (next === view) return;
    focusDestination.current = true;
    setPrivacy(false);
    window.location.hash = next;
    setView(next);
    window.scrollTo({ top: 0 });
  }

  function changeMode(next: Mode) {
    if (next === mode || pending || journey.pending || journey.action) return;
    setMode(next);
    setConsent(false);
    setError("");
  }

  async function generate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (mode === "support" || pending || !input.trim() || (mode === "fastapi" && !consent)) return;
    const controller = new AbortController();
    request.current = controller;
    setError("");
    setPending(true);
    const usedMode = mode;
    if (usedMode === "fastapi") setConsent(false);
    try {
      const adapter = usedMode === "fastapi" ? createFastApiAgent("/api/agent") : demoAgent;
      const result = await adapter.reflect({
        text: input,
        consent: usedMode === "fastapi",
        previous: usedMode === "demo" ? cards[0] : undefined,
      }, controller.signal);
      if (request.current !== controller) return;
      setDraft(result);
      setFields(result.hypotheses);
      setOutcome(null);
      setSaved(false);
      requestAnimationFrame(scrollToDraft);
    } catch (issue) {
      if (controller.signal.aborted || request.current !== controller) return;
      setError(issue instanceof Error ? issue.message : "暂时没有收到回复，请重试。");
    } finally {
      if (request.current === controller) {
        setPending(false);
        request.current = null;
      }
    }
  }

  function cancel() {
    request.current?.abort();
    request.current = null;
    setPending(false);
    setError("已停止这次请求。之前完成的内容仍然保留。");
  }

  function scrollToDraft() {
    confirmRef.current?.focus({ preventScroll: true });
    confirmRef.current?.scrollIntoView({ block: "start", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
  }

  const dirty = !!draft && !!fields && hypothesisKeys.some((key) => draft.hypotheses[key] !== fields[key]);
  const confirmed = !!draft && draft.status !== "proposed" && !dirty;
  const sceneKey = confirmed && outcome ? outcome.choice : confirmed ? "confirmed" : draft ? "proposed" : "idle";
  const currentScene = scene[sceneKey];

  function confirm() {
    if (!draft || !fields) return;
    const result = reviseReflection(draft, fields);
    setDraft(result);
    setFields(result.hypotheses);
    setOutcome(null);
    setSaved(false);
  }

  function selectSupport(choice: SupportChoice) {
    if (!draft || !confirmed) return;
    setOutcome(chooseSupport(draft, choice));
    setSaved(false);
  }

  function save() {
    if (!draft || !outcome || !confirmed) return;
    const card = saveExperience(draft, outcome);
    setCards((current) => [card, ...current.filter((item) => item.id !== card.id)]);
    setSaved(true);
  }

  function clearSession() {
    request.current?.abort();
    request.current = null;
    setPending(false);
    setMode("demo");
    setInput("");
    setConsent(false);
    setDraft(null);
    setFields(null);
    setOutcome(null);
    setCards([]);
    setError("");
    setSaved(false);
    setPrivacy(false);
    go("universe");
  }

  if (view === "universe") return <CinematicHome onNavigate={go} mainRef={main}
    pausedByUser={backgroundPaused} onPauseChange={setBackgroundPaused} />;

  return (
    <div className={`shell ${view === "caseforge" ? "" : "world-shell"} world-${view}`}>
      <a className="skip-link" href="#main-content" onClick={(event) => { event.preventDefault(); main.current?.focus(); }}>跳到主要内容</a>
      <header className="site-header" data-scrolled={scrolled}>
        <div className="header-inner">
          <button type="button" className="brand" aria-label="LoveStone 首页" onClick={() => go("universe")}>
            <span className="brand-mark"><Image src="/mark.svg" width={33} height={33} alt="" priority /></span>
            <span>Love<span>Stone</span></span>
          </button>
          <nav id="world-navigation" aria-label="主导航" className="nav-glass" data-open={menuOpen}>
            {nav.map(({ id, label, icon: Icon }) => (
              <button type="button" key={id} aria-current={view === id ? "page" : undefined} onClick={() => go(id)}>
                <Icon size={15} aria-hidden="true" /><span>{label}</span>
              </button>
            ))}
          </nav>
          <div className="world-header-actions">
            <button id="world-privacy-toggle" type="button" className="mode-badge" onClick={() => setPrivacy(!privacy)} aria-expanded={privacy} aria-controls="privacy-info">
              <i /> 演示说明 <CircleHelp size={14} aria-hidden="true" />
            </button>
            <button id="world-menu-toggle" type="button" className="world-menu-toggle" onClick={() => setMenuOpen(!menuOpen)} aria-expanded={menuOpen} aria-controls="world-navigation" aria-label={menuOpen ? "关闭导航" : "打开导航"}>
              {menuOpen ? <X size={20} /> : <Menu size={20} />}
            </button>
          </div>
        </div>
      </header>

      <main id="main-content" ref={main} tabIndex={-1} className="content">
        {privacy && <section id="privacy-info" className="privacy-drawer" aria-label="演示与隐私说明">
          <LockKeyhole size={20} aria-hidden="true" />
          <div><strong>这是演示空间</strong><p>{mode === "support" ? "后端支持流程会在本机服务内存中保留旅程。刷新网页不会删除后端内容；请使用旅程中的清空按钮。保存到磁盘需要单独同意。请仅使用虚构资料。" : "请只填写虚构内容。默认模式在本页内存运行；刷新后会清除草稿和经历卡。联网通用聊天须逐次同意发送文本，不发送画布或经历卡。"}</p></div>
          {mode !== "support" && <button type="button" className="btn btn-secondary" onClick={clearSession}>清空本页演示记录</button>}
          <button type="button" className="icon-button" aria-label="关闭演示说明" onClick={() => setPrivacy(false)}><X size={18} /></button>
        </section>}

        {liveEnabled && view !== "caseforge" && <div className="journey-mode-bar" role="group" aria-label="选择体验模式">
          <button type="button" aria-pressed={mode === "demo"} disabled={pending || journey.pending || !!journey.action} onClick={() => changeMode("demo")}>本地演示</button>
          <button type="button" aria-pressed={mode === "fastapi"} disabled={pending || journey.pending || !!journey.action} onClick={() => changeMode("fastapi")}>联网通用聊天</button>
          <button type="button" aria-pressed={mode === "support"} disabled={pending || journey.pending || !!journey.action} onClick={() => changeMode("support")}>后端支持流程</button>
        </div>}
        <div key={mode === "support" ? "support" : view} className="view-enter">
          {mode === "support" && view !== "caseforge" && <SupportJourney view={view} journey={journey} onNavigate={go} />}
          {mode !== "support" && view === "agent" && <section className="agent-view" aria-labelledby="agent-title">
            <div className="agent-intro-screen">
              <WorldMedia src="https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260328_115001_bcdaa3b4-03de-47e7-ad63-ae3e392c32d4.mp4" poster="/images/agent-bg.jpg" fadeLoop shift />
              <div className="agent-intro-shade" aria-hidden="true" />
              <div className="agent-intro-content">
                <span className="agent-intro-kicker">LOVESTONE / PERSONAL AGENT</span>
                <h1 id="agent-title" lang="en">A space to be heard.</h1>
                <p className="agent-intro-subtitle">和拾光说说话。<span>不必先整理好自己。</span></p>
                <form className="agent-composer-form" onSubmit={generate}>

                  <label htmlFor="story-input" className="sr-only">说说发生了什么</label>
                  <div className="agent-composer">
                    <textarea ref={inputField} id="story-input" rows={1} placeholder="此刻，有什么想说的吗？" maxLength={maxInputLength} value={input} disabled={pending} onChange={(e) => setInput(e.target.value)} />
                    {pending ? <button type="button" className="agent-send" onClick={cancel} aria-label="停止这次请求"><X size={20} /></button>
                      : <button type="submit" className="agent-send" disabled={!input.trim() || (mode === "fastapi" && !consent)} aria-label="生成理解草稿"><ArrowRight size={22} /></button>}
                  </div>
                  <div className="input-meta">
                    <button type="button" className="text-button" disabled={pending} onClick={() => setInput(demoSample)}><Sparkles size={14} /> 使用虚构示例</button>
                    <span>{input.length} / {maxInputLength}</span>
                  </div>
                  {mode === "fastapi" && <label className="consent-box"><input type="checkbox" checked={consent} disabled={pending} onChange={(e) => setConsent(e.target.checked)} /><span>我同意将这次的虚构文本发送给本机 FastAPI 及其配置的 DeepSeek 模型。本次仅回复文本，下面的理解草稿仍来自模拟。</span></label>}
                  <p className="agent-data-note"><LockKeyhole size={12} /> {mode === "demo" ? "本地模拟理解 · 请仅输入虚构内容 · 刷新即清除" : "仅供本机联调 · 理解结构仍为模拟 · 请勿输入真实隐私"}</p>
                  {pending && <p className="inline-status" role="status">拾光正在准备回应…</p>}
                  {error && <p className="inline-status" role="alert">{error}</p>}
                </form>
                <p className="agent-intro-promise">拾光会把听见的轻轻放在你面前。<br />哪些理解贴近你，怎样继续，都由你决定。</p>
                <button type="button" className="agent-manifesto" onClick={draft ? scrollToDraft : () => setPrivacy(true)}>{draft ? "继续整理这段经历" : "关于这个空间"} <ArrowRight size={14} /></button>
              </div>
              <div className="agent-orbit-actions" aria-label="继续探索">
                <button type="button" aria-label="回到心象宇宙" onClick={() => go("universe")}><Orbit size={20} /><span>心象宇宙</span></button>
                <button type="button" aria-label="打开心象画布" onClick={() => go("canvas")}><Layers3 size={20} /><span>心象画布</span></button>
                <button type="button" aria-label="打开成长档案" onClick={() => go("archive")}><BookOpen size={20} /><span>成长档案</span></button>
              </div>
            </div>
            {draft && fields && <div className="agent-layout">
              <div className="agent-main">
                {draft && fields && <>
                  <section className="flow-panel reflection-panel" aria-labelledby="draft-title">
                    <div className="panel-head"><span className="step-index">02</span><div><h2 id="draft-title" tabIndex={-1} ref={confirmRef}>看看拾光听见了什么</h2><p>包括「事实」在内，都可以重写、留空或确认。</p></div></div>
                    <div className="reply"><span><Aperture size={17} /> 拾光 · {draft.source === "fastapi" ? "联网通用回复，未经你确认" : "演示回复，未经你确认"}</span><p>{draft.reply}</p></div>
                    <div className="draft-note"><Pencil size={14} /> 模拟理解 · 可修改的假设 · 非诊断</div>
                    <div className="field-grid">
                      {hypothesisKeys.map((key) => <label key={key}>
                        <span>{hypothesisLabels[key]}</span>
                        <textarea maxLength={maxHypothesisLength} disabled={pending} value={fields[key]} onChange={(e) => setFields({ ...fields, [key]: e.target.value })} />
                      </label>)}
                    </div>
                    <div className="flow-actions">
                      <button type="button" className="btn btn-secondary" disabled={pending || confirmed} onClick={confirm}><Check size={16} /> {confirmed ? "理解已确认" : "确认当前理解"}</button>
                      <small aria-live="polite">{dirty ? "有未确认的修改" : confirmed ? "拾光会用你确认的版本来回应" : "确认后可选择怎样被支持"}</small>
                    </div>
                  </section>

                  {confirmed && <section className="flow-panel support-panel" aria-labelledby="support-title">
                    <div className="panel-head"><span className="step-index">03</span><div><h2 id="support-title">此刻，希望怎么继续？</h2><p>你选的方式会改变拾光的回应，以及静水湾接下来的样子。</p></div></div>
                    <div className="support-grid" aria-label="支持选择">
                      {choices.map((choice) => { const Icon = choiceIcons[choice]; return <button key={choice} type="button" aria-pressed={outcome?.choice === choice} onClick={() => selectSupport(choice)} disabled={pending}>
                        <Icon size={18} aria-hidden="true" /><strong>{supportChoices[choice].label}</strong><small>{supportChoices[choice].description}</small>
                      </button>; })}
                    </div>
                    {outcome && <div key={outcome.choice} className="outcome" aria-live="polite">
                      <p>{outcome.response}</p><p className="outcome-action"><ArrowDownRight size={16} /> {outcome.action}</p>
                    </div>}
                    {outcome && <div className="flow-actions">
                      <button type="button" className="btn btn-primary" onClick={save} disabled={pending || saved}>{saved ? <Check size={16} /> : <BookOpen size={16} />}{saved ? "已保存经历卡" : "保存经历卡"}</button>
                      {saved ? <button type="button" className="text-button" onClick={() => go("archive")}>查看成长档案 <ArrowRight size={15} /></button> : <small>仅留在本页内存，刷新后清除</small>}
                    </div>}
                  </section>}
                </>}
              </div>
              <aside className="agent-side" aria-label="拾光与静水湾">
                <div className="side-art" data-scene={sceneKey}>
                  <Image src="/quiet-cove.webp" alt="" fill sizes="(max-width: 830px) 100vw, 335px" />
                  <SceneWave state={sceneKey} />
                  <div className="side-art-content"><span><span className="live-dot" /> {currentScene.tag}</span><h2>{currentScene.title}</h2><p aria-live="polite">{currentScene.text}</p></div>
                </div>
                <div className="side-note"><HeartHandshake size={19} /><div><strong>这一步，由你决定。</strong><p>拾光的回应仅供自我反思。你可以修改任何理解，也可以停在这里。</p></div></div>
                <button type="button" className="text-button back-to-cove" onClick={() => go("universe")}><ArrowLeft size={15} /> 回到静水湾</button>
              </aside>
            </div>}
          </section>}

          {mode !== "support" && view === "canvas" && <CinematicCanvas draft={draft} onOpenAgent={() => go("agent")} />}
          {mode !== "support" && view === "signals" && <CinematicSignals onOpenAgent={() => go("agent")} />}
          {mode !== "support" && view === "archive" && <CinematicArchive cards={cards} onOpenAgent={() => go("agent")} />}
          {view === "caseforge" && <CaseForgeView />}
        </div>
      </main>
      <footer className="site-footer"><div className="footer-inner">
        <span><Focus size={15} /> LoveStone <span className="footer-divider" /> 感受由你命名</span>
        <button type="button" className="text-button" onClick={() => go(view === "caseforge" ? "universe" : "caseforge")}>
          <FlaskConical size={14} /> {view === "caseforge" ? "返回心象宇宙" : "CaseForge · 内部研发"} <ChevronRight size={14} />
        </button>
      </div></footer>
    </div>
  );
}
