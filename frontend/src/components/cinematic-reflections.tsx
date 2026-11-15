"use client";

import { ArrowDown, ArrowUpRight, ChevronDown, Fingerprint, Heart, Layers3, ScanLine, Sparkles, Wind, Footprints } from "lucide-react";
import { motion, useInView } from "motion/react";
import { useRef, useState } from "react";
import { hypothesisKeys, hypothesisLabels, supportChoices, type ExperienceCard, type HypothesisKey, type Reflection, type SupportChoice } from "../lib/agent";
import { WorldMedia } from "./world-media";
import { CinematicArchiveControls } from "./cinematic-archive-controls";
import type { JourneyController } from "./use-support-journey";
import "./cinematic-reflections.css";

const canvasVideo = "https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260317_100335_dc625816-c3c1-4b00-b93e-4cb301cf5ea5.mp4";
const archiveVideo = "https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260330_145725_08886141-ed95-4a8e-8d6d-b75eaadce638.mp4";

const canvasLayers = {
  facts: { index: "01", icon: ScanLine, hint: "先看看发生了什么。试着把你看见、听见的，和当时的猜测分开。" },
  interpretation: { index: "02", icon: Layers3, hint: "你当时是怎么理解这件事的？同一件事也可能有别的读法。" },
  emotion: { index: "03", icon: Heart, hint: "哪些词贴近你的感受？你也可以同时有几种感受。" },
  needs: { index: "04", icon: Fingerprint, hint: "此刻你希望得到什么？如果还说不清，这一层可以留白。" },
} satisfies Record<HypothesisKey, { index: string; icon: typeof Heart; hint: string }>;

const reflectionStatus = { proposed: "待你确认的理解草稿", confirmed: "由你确认的理解", corrected: "由你修正的理解" };
const archiveChoices = ["keep", "release", "next"] as const;
const archiveIcons = { keep: Heart, release: Wind, next: Footprints };
const archiveDate = new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "long", day: "numeric" });

function BlurTitle({ lines, id, language, lineDelay = .42 }: {
  lines: readonly (readonly string[])[]; id: string; language?: string; lineDelay?: number;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  const visible = useInView(heading, { once: true, amount: .24 });
  return (
    <h1 ref={heading} id={id} className="cr-title" lang={language} aria-label={lines.map((line) => line.join(language === "en" ? " " : "")).join(" ")}>
      {lines.map((line, row) => (
        <span key={line.join("")} className="cr-title-line" aria-hidden="true">
          {line.map((word, column) => (
            <motion.span key={word} className="cr-word"
              initial={{ filter: "blur(10px)", opacity: 0, y: 36 }}
              animate={visible ? { filter: "blur(0px)", opacity: 1, y: 0 } : undefined}
              transition={{ duration: .35, delay: .1 + row * lineDelay + column * .1, ease: "easeOut" }}
            >{word}{language === "en" && column + 1 < line.length ? "\u00a0" : ""}</motion.span>
          ))}
        </span>
      ))}
    </h1>
  );
}

export function CinematicCanvas({ draft, onOpenAgent }: { draft: Reflection | null; onOpenAgent: () => void }) {
  const [active, setActive] = useState<HypothesisKey>("facts");
  const layer = canvasLayers[active];
  const Icon = layer.icon;

  return (
    <section className="cr-world cr-canvas" aria-labelledby="cr-canvas-title">
      <div className="cr-hero cr-canvas-hero">
        <WorldMedia src={canvasVideo} poster="/images/canvas-bg.jpg" />
        <div className="cr-canvas-wash" aria-hidden="true" />
        <div className="cr-canvas-content">
          <p className="cr-badge cr-canvas-badge cr-enter"><Layers3 size={14} /> 心象画布 <span>MIND CANVAS</span></p>
          <BlurTitle lines={[["给感受", "一个形状。"]]} id="cr-canvas-title" />
          <p className="cr-description cr-enter cr-delay-copy">把一段经历分开来看。事实、解释、情绪与需要，可以由你重新命名。</p>
          <div className="cr-canvas-workspace cr-enter cr-delay-actions">
            <div className="cr-glass cr-layer-control" role="group" aria-label="选择心象画布的理解层">
              {hypothesisKeys.map((key) => {
                const LayerIcon = canvasLayers[key].icon;
                return <button type="button" key={key} aria-pressed={key === active} aria-controls="cr-layer-details" onClick={() => setActive(key)}><LayerIcon size={15} />{hypothesisLabels[key]}</button>;
              })}
            </div>
            <article id="cr-layer-details" className="cr-layer-details" aria-live="polite">
              <div key={active} className="cr-layer-body">
                <span className="cr-layer-icon" aria-hidden="true"><Icon size={18} /></span>
                <div>
                  <div className="cr-layer-meta">
                    <span>{layer.index} / 04</span>
                    <span>{draft ? reflectionStatus[draft.status] : "画布指引"}</span>
                    {draft?.hypothesisSource === "demo" && <span>· 演示来源</span>}
                    {draft?.hypothesisSource === "fastapi" && <span>· 你的记录</span>}
                  </div>
                  <h2>{hypothesisLabels[active]}</h2>
                  <p tabIndex={draft ? 0 : undefined} aria-label={draft ? `${hypothesisLabels[active]}理解内容` : undefined}>{draft ? draft.hypotheses[active] || "你选择了留白。" : layer.hint}</p>
                </div>
              </div>
              <button type="button" className="cr-button cr-canvas-button" onClick={onOpenAgent}>{draft ? "去对话确认或修正" : "和拾光聊聊"}<ArrowUpRight size={17} /></button>
            </article>
            <p className="cr-canvas-note">理解可以改变，也可以留白。这不是诊断。</p>
          </div>
        </div>
      </div>
    </section>
  );
}

export function CinematicArchive({ cards, onOpenAgent, journey }: { cards: readonly ExperienceCard[]; onOpenAgent: () => void; journey?: JourneyController }) {
  const [filter, setFilter] = useState<"all" | SupportChoice>("all");
  const cardHeading = useRef<HTMLHeadingElement>(null);
  const visibleCards = [...cards].filter((card) => filter === "all" || filter === card.choice)
    .sort((left, right) => right.createdAt.localeCompare(left.createdAt));

  function showCards(choice: "all" | SupportChoice = "all") {
    setFilter(choice);
    cardHeading.current?.focus({ preventScroll: true });
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    cardHeading.current?.scrollIntoView({ behavior: reduced ? "instant" : "smooth", block: "start" });
  }

  return (
    <section className="cr-world cr-archive" aria-labelledby="cr-archive-title">
      <div className="cr-hero cr-archive-hero">
        <WorldMedia src={archiveVideo} poster="/images/archive-bg.jpg" position="right" />
        <div className="cr-archive-wash" aria-hidden="true" />
        <div className="cr-archive-content">
          <p className="cr-badge cr-glass cr-enter"><span className="cr-badge-chip">{cards.length} 张</span> 成长档案 <span lang="en">GROWTH ARCHIVE</span></p>
          <BlurTitle lines={[["What", "You", "Keep"], ["Becomes", "Light."]]} id="cr-archive-title" language="en" />
          <p className="cr-description cr-enter cr-delay-copy">把想带走的理解存成一张经历卡。再回头时，你可以看见当时如何照顾自己、为自己选了哪一步。</p>
          <div className="cr-archive-actions cr-enter cr-delay-actions">
            <button type="button" className="cr-button cr-glass cr-archive-primary" onClick={() => showCards()}>查看我的经历卡<ArrowDown size={17} /></button>
            <button type="button" className="cr-button cr-archive-secondary" onClick={onOpenAgent}>和拾光开启一段对话<ArrowUpRight size={17} /></button>
          </div>
        </div>
        <nav className="cr-archive-beacons cr-enter cr-delay-actions" aria-label="按经历选择浏览卡片">
          <span className="cr-badge cr-glass">每一种选择，都可以留下自己的位置</span>
          <div>
            {archiveChoices.map((choice) => (
              <button type="button" key={choice} onClick={() => showCards(choice)}><span lang="en">{supportChoices[choice].label.split(" · ")[0]}</span><small>{supportChoices[choice].label.split(" · ")[1]}</small></button>
            ))}
          </div>
        </nav>
      </div>

      <div className="cr-archive-feed">
        <header className="cr-feed-header">
          <div><p>YOUR EXPERIENCES</p><h2 ref={cardHeading} tabIndex={-1}>我的经历卡</h2></div>
          <div className="cr-glass cr-feed-filters" role="group" aria-label="筛选经历卡">
            {(["all", ...archiveChoices] as const).map((key) => (
              <button type="button" key={key} aria-pressed={filter === key} onClick={() => setFilter(key)}>{key === "all" ? "全部" : supportChoices[key].label}</button>
            ))}
          </div>
        </header>
        <p className="cr-feed-note" aria-live="polite">{visibleCards.length} 张经历卡 · 仅在当前页面暂存，刷新后清空。{journey ? "本机旅程存档需在下方单独保存。" : ""}</p>
        {visibleCards.length ? (
          <div className="cr-archive-cards" key={filter}>
            {visibleCards.map((card) => {
              const CardIcon = archiveIcons[card.choice];
              return (
                <details key={card.id} className="cr-glass cr-experience">
                  <summary>
                    <span className="cr-card-icon"><CardIcon size={19} /></span>
                    <span className="cr-card-title"><strong>{card.title}</strong><small>{archiveDate.format(new Date(card.createdAt))} · {supportChoices[card.choice].label} · {reflectionStatus[card.reflectionStatus]}</small></span>
                    <ChevronDown size={19} className="cr-card-chevron" />
                  </summary>
                  <div className="cr-card-details">
                    <span className="cr-card-provenance">{card.source === "fastapi" ? "对话记录 · 用户确认。" : "演示来源。"}这张经历卡保留了你确认或修正的内容，以及当时的支持选择。</span>
                    <p>{card.action}</p>
                    <dl>{hypothesisKeys.map((key) => <div key={key}><dt>{hypothesisLabels[key]}</dt><dd>{card.hypotheses[key] || "留白"}</dd></div>)}</dl>
                  </div>
                </details>
              );
            })}
          </div>
        ) : (
          <div className="cr-glass cr-empty-cards">
            <Sparkles size={25} />
            <h3>{cards.length ? "这个筛选下暂时没有卡片" : "第一束光，等你留下"}</h3>
            <p>{cards.length ? "可以查看其他卡片，或回到对话留下一张新的经历卡。" : "和拾光说说近况，确认你自己的理解，选择留下、放下或向前，再保存这段经历。"}</p>
            <button type="button" className="cr-button cr-glass" onClick={cards.length ? () => setFilter("all") : onOpenAgent}>{cards.length ? "查看全部" : "前往拾光对话"}<ArrowUpRight size={16} /></button>
          </div>
        )}
        {journey && <CinematicArchiveControls journey={journey} />}
      </div>
    </section>
  );
}
