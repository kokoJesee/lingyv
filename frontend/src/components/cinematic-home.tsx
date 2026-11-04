"use client";

import Image from "next/image";
import { ArrowUpRight, Menu, Pause, Play, Sparkles, X } from "lucide-react";
import { motion, useInView, useReducedMotion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import type { RefObject } from "react";
import "./cinematic-home.css";

export const cinematicVideo = "https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260306_115329_5e00c9c5-4d69-49b7-94c3-9c31c60bb644.mp4";

type Destination = "universe" | "agent" | "canvas" | "signals" | "archive" | "caseforge";
const links = [
  { id: "universe", label: "心象宇宙" },
  { id: "agent", label: "拾光对话" },
  { id: "canvas", label: "心象画布" },
  { id: "signals", label: "身心状态" },
  { id: "archive", label: "成长档案" },
] as const;
const journeys = [
  { id: "agent", title: "Listen", label: "表达" },
  { id: "canvas", title: "Reflect", label: "理解" },
  { id: "signals", title: "Feel", label: "觉察 · 模拟" },
  { id: "archive", title: "Grow", label: "成长" },
  { id: "agent", title: "Begin", label: "出发" },
] as const;

function BlurHeading({ reduced }: { reduced: boolean }) {
  const ref = useRef<HTMLHeadingElement>(null);
  const seen = useInView(ref, { once: true, amount: .3 });
  const lines = ["A Little Space to Feel.", "A Universe Within."];
  let wordIndex = 0;
  return <h1 ref={ref} id="cinema-title" className="cinema-title" aria-label={lines.join(" ")} lang="en">
    {lines.map((line) => <span className="cinema-title-line" key={line} aria-hidden="true">
      {line.split(" ").map((word) => {
        const index = wordIndex++;
        return <motion.span className="cinema-word" key={word + index}
          initial={reduced ? false : { filter: "blur(10px)", opacity: 0, y: 50 }}
          animate={seen && !reduced ? {
            filter: ["blur(10px)", "blur(5px)", "blur(0px)"],
            opacity: [0, .5, 1], y: [50, -5, 0],
          } : reduced ? { filter: "blur(0px)", opacity: 1, y: 0 } : { filter: "blur(10px)", opacity: 0, y: 50 }}
          transition={reduced ? { duration: 0 } : { duration: .7, delay: index * .1, times: [0, .5, 1] }}
        >{word}{index < 8 ? "\u00a0" : ""}</motion.span>;
      })}
    </span>)}
  </h1>;
}

export function CinematicHome({ onNavigate, mainRef, pausedByUser, onPauseChange }: {
  onNavigate: (destination: Destination) => void;
  mainRef: RefObject<HTMLElement | null>;
  pausedByUser: boolean;
  onPauseChange: (paused: boolean) => void;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const intro = useRef<HTMLDialogElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);
  const initialReduced = useReducedMotion();
  const [reduced, setReduced] = useState(initialReduced);
  const [enabled, setEnabled] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [failed, setFailed] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [introOpen, setIntroOpen] = useState(false);

  useEffect(() => {
    const preference = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(preference.matches);
    update();
    preference.addEventListener("change", update);
    return () => preference.removeEventListener("change", update);
  }, []);
  useEffect(() => { setEnabled(reduced === false); }, [reduced]);
  useEffect(() => {
    const media = video.current;
    if (!media) return;
    let cancelled = false;
    function synchronize() {
      if (!media) return;
      if (!enabled || pausedByUser || introOpen || document.hidden) {
        media.pause();
      } else {
        void media.play().catch(() => {
          if (!cancelled) setPlaying(false);
        });
      }
    }
    synchronize();
    document.addEventListener("visibilitychange", synchronize);
    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", synchronize);
      media.pause();
    };
  }, [enabled, pausedByUser, introOpen]);

  function navigate(destination: Destination) {
    setMenuOpen(false);
    intro.current?.close();
    onNavigate(destination);
  }

  function toggleVideo() {
    if (playing) {
      onPauseChange(true);
    } else {
      setEnabled(true);
      onPauseChange(false);
      if (failed) { setFailed(false); video.current?.load(); }
      void video.current?.play().catch(() => setPlaying(false));
    }
  }

  function showIntro() {
    setMenuOpen(false);
    setIntroOpen(true);
    intro.current?.showModal();
  }

  const reveal = (delay: number) => ({
    initial: reduced ? false as const : { filter: "blur(10px)", opacity: 0, y: 20 },
    animate: { filter: "blur(0px)", opacity: 1, y: 0 },
    transition: { duration: reduced ? 0 : .6, delay: reduced ? 0 : delay },
  });

  return <div className="cinema-home">
    <link rel="preload" as="image" href="/images/hero_bg.jpeg" type="image/jpeg" />
    <a className="skip-link" href="#main-content" onClick={(event) => {
      event.preventDefault(); mainRef.current?.focus();
    }}>跳到主要内容</a>
    <div className="cinema-media" aria-hidden="true">
      <video ref={video} className="cinema-video" src={enabled ? cinematicVideo : undefined}
        autoPlay={enabled && !pausedByUser && !introOpen} loop muted playsInline
        preload={enabled ? "auto" : "none"} poster="/images/hero_bg.jpeg"
        onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)}
        onError={() => { setFailed(true); setPlaying(false); }} />
      <div className="cinema-overlay" />
      <div className="cinema-readability" />
    </div>

    <header className="cinema-nav">
      <button type="button" className="cinema-brand" aria-label="LoveStone 首页" onClick={() => navigate("universe")}>
        <Image src="/mark.svg" alt="" width={48} height={48} priority />
        <span>LoveStone</span>
      </button>
      <nav className="liquid-glass cinema-desktop-nav" aria-label="主导航">
        {links.map(({ id, label }) => <button type="button" key={id} aria-current={id === "universe" ? "page" : undefined}
          onClick={() => navigate(id)}>{label}</button>)}
        <button type="button" className="cinema-nav-cta" onClick={() => navigate("agent")}>开始旅程 <ArrowUpRight size={16} /></button>
      </nav>
      <button ref={toggle} type="button" className="liquid-glass cinema-menu-toggle" aria-expanded={menuOpen}
        aria-controls="cinema-mobile-nav" aria-label={menuOpen ? "关闭导航" : "打开导航"}
        onClick={() => setMenuOpen(!menuOpen)}>{menuOpen ? <X size={21} /> : <Menu size={21} />}</button>
      {menuOpen && <nav id="cinema-mobile-nav" className="cinema-mobile-nav liquid-glass-strong" aria-label="移动导航"
        onKeyDown={(event) => { if (event.key === "Escape") { setMenuOpen(false); toggle.current?.focus(); } }}>
        {links.map(({ id, label }) => <button key={id} type="button" onClick={() => navigate(id)}>{label}<ArrowUpRight size={15} /></button>)}
        <button type="button" className="cinema-nav-cta" onClick={() => navigate("agent")}>开始旅程 <ArrowUpRight size={16} /></button>
      </nav>}
    </header>

    <div className="cinema-foreground">
      <main id="main-content" ref={mainRef} tabIndex={-1} className="cinema-center" aria-labelledby="cinema-title">
        <motion.div className="liquid-glass cinema-badge" {...reveal(.05)}>
          <span>New</span><span>LoveStone · 你的心象宇宙，从此刻开启</span>
        </motion.div>
        <BlurHeading reduced={!!reduced} />
        <motion.p className="cinema-description" {...reveal(.8)}>
          给感受一点空间，向内探索一整个宇宙。<br />
          与拾光一起，理解此刻的自己，找到属于你的下一步。
        </motion.p>
        <motion.div className="cinema-actions" {...reveal(1.1)}>
          <motion.button type="button" className="liquid-glass-strong cinema-primary"
            whileHover={reduced ? undefined : { y: -3, scale: 1.025 }}
            whileTap={reduced ? undefined : { scale: .97 }}
            onClick={() => navigate("agent")}>开启心象旅程 <ArrowUpRight size={20} /></motion.button>
          <button type="button" className="cinema-secondary" onClick={showIntro}>认识拾光 <Play size={14} fill="currentColor" /></button>
        </motion.div>
      </main>

      <footer className="cinema-bottom">
        <motion.div className="cinema-journey" {...reveal(1.35)}>
          <span className="liquid-glass cinema-journey-label">每一种感受，都有值得被看见的宇宙</span>
          <nav className="cinema-journey-links" aria-label="探索 LoveStone">
            {journeys.map(({ id, title, label }) => <button type="button" key={title} onClick={() => navigate(id)} aria-label={title + " · " + label}>
              <span lang="en">{title}</span><small>{label}</small>
            </button>)}
          </nav>
        </motion.div>
        <div className="cinema-edge">
          <button type="button" className="cinema-demo-note" onClick={showIntro}>本地演示 · 感受由你命名</button>
          <button type="button" className="liquid-glass cinema-playback" onClick={toggleVideo} aria-label={playing ? "暂停背景视频" : "播放背景视频"}>
            {playing ? <Pause size={12} /> : <Play size={12} />}
            <span>{playing ? "暂停动态" : failed ? "重试视频" : "播放动态"}</span>
          </button>
        </div>
      </footer>
    </div>

    <dialog ref={intro} className="cinema-intro" onClose={() => setIntroOpen(false)}
      aria-labelledby="intro-title" onClick={(event) => { if (event.target === event.currentTarget) intro.current?.close(); }}>
      <div className="cinema-intro-inner">
        <button type="button" className="cinema-close" aria-label="关闭拾光介绍" onClick={() => intro.current?.close()}><X size={20} /></button>
        <Image src="/quiet-cove.webp" alt="静水湾中，一颗透着暖光的晶石悬在水面上。" width={600} height={400} />
        <div className="cinema-intro-copy">
          <div className="cinema-intro-eyebrow"><Sparkles size={14} /> MEET YOUR COMPANION</div>
          <h2 id="intro-title">你好，我是拾光。</h2>
          <p>我在静水湾收集微光，也陪你整理那些还没说清的感受。先从一件小事开始；你可以改写我的理解，再选择留住什么、放下什么，或者走出一小步。</p>
          <button type="button" className="btn btn-primary" onClick={() => navigate("agent")}>和拾光聊聊 <ArrowUpRight size={17} /></button>
          <small>当前为演示体验，请使用虚构内容。理解草稿可修改、待确认；记录仅留在本页内存，刷新后清除。</small>
        </div>
      </div>
    </dialog>
  </div>;
}
