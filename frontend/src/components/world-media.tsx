"use client";

import { Pause, Play } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type Hls from "hls.js";
import "./world-media.css";

type WorldMediaProps = {
  src: string;
  poster: string;
  fadeLoop?: boolean;
  shift?: boolean;
  position?: "center" | "right";
  className?: string;
};

type Playback = "poster" | "playing" | "paused" | "failed";

const fadeDuration = 500;
const fadeOutThreshold = 0.55;
const loopRestartDelay = 100;

export function WorldMedia({ src, poster, fadeLoop = false, shift = false, position = "center", className }: WorldMediaProps) {
  const media = useRef<HTMLDivElement>(null);
  const player = useRef<HTMLVideoElement>(null);
  const toggle = useRef<() => void>(undefined);
  const [playback, setPlayback] = useState<Playback>("poster");

  useEffect(() => {
    const wrapper = media.current;
    if (!wrapper || !player.current) return;
    const video = player.current;

    const preference = window.matchMedia("(prefers-reduced-motion: reduce)");
    const streaming = /\.m3u8(?:[?#]|$)/i.test(src);
    let visible = typeof IntersectionObserver === "undefined";
    let disposed = false;
    let failed = false;
    let userChoice: "play" | "pause" | null = null;
    let sourceAttached = false;
    let ready = false;
    let hls: Hls | undefined;
    let hlsLoading = false;
    let animation: number | undefined;
    let restartTimer: number | undefined;
    let fadingOut = false;

    video.muted = true;
    video.style.opacity = fadeLoop ? "0" : "1";
    setPlayback("poster");

    function cancelAnimation() {
      if (animation !== undefined) window.cancelAnimationFrame(animation);
      animation = undefined;
    }

    function fadeTo(target: number) {
      cancelAnimation();
      const from = Number(video.style.opacity);
      const start = performance.now();
      function frame(time: number) {
        const progress = Math.min((time - start) / fadeDuration, 1);
        video.style.opacity = String(from + (target - from) * progress);
        animation = progress < 1 ? window.requestAnimationFrame(frame) : undefined;
      }
      animation = window.requestAnimationFrame(frame);
    }

    function cancelRestart() {
      if (restartTimer !== undefined) window.clearTimeout(restartTimer);
      restartTimer = undefined;
    }

    function shouldPlay() {
      return !disposed && !failed && visible && !document.hidden &&
        (userChoice === "play" || (userChoice === null && !preference.matches));
    }

    function fallBackToPoster() {
      if (disposed || failed) return;
      failed = true;
      cancelAnimation();
      cancelRestart();
      video.pause();
      hls?.destroy();
      hls = undefined;
      setPlayback("failed");
    }

    function attachSource() {
      sourceAttached = true;
      if (!streaming || video.canPlayType("application/vnd.apple.mpegurl")) {
        video.src = src;
        ready = true;
        return;
      }
      void import("hls.js").then(({ default: HlsClient }) => {
        if (disposed || failed) return;
        if (!HlsClient.isSupported()) {
          fallBackToPoster();
          return;
        }
        hls = new HlsClient({ autoStartLoad: false });
        hls.on(HlsClient.Events.MEDIA_ATTACHED, () => hls?.loadSource(src));
        hls.on(HlsClient.Events.MANIFEST_PARSED, () => {
          ready = true;
          syncPlayback();
        });
        hls.on(HlsClient.Events.ERROR, (_event, data) => {
          if (data.fatal) fallBackToPoster();
        });
        hls.attachMedia(video);
      }).catch(fallBackToPoster);
    }

    function syncPlayback() {
      if (!shouldPlay()) {
        video.pause();
        cancelAnimation();
        cancelRestart();
        fadingOut = false;
        if (hlsLoading) {
          hls?.stopLoad();
          hlsLoading = false;
        }
        return;
      }
      if (!sourceAttached) attachSource();
      if (!ready || restartTimer !== undefined || !video.paused) return;
      if (hls && !hlsLoading) {
        hls.startLoad();
        hlsLoading = true;
      }
      if (video.ended) video.currentTime = 0;
      void video.play().catch(() => {
        // A browser may require the next play attempt to come from a user gesture.
      });
    }

    function onTimeUpdate() {
      if (!fadeLoop || fadingOut || video.paused || !shouldPlay()) return;
      const remaining = video.duration - video.currentTime;
      if (remaining > 0 && remaining <= fadeOutThreshold) {
        fadingOut = true;
        fadeTo(0);
      }
    }

    function onPlaying() {
      if (!shouldPlay()) {
        video.pause();
        return;
      }
      setPlayback("playing");
      if (fadeLoop) {
        onTimeUpdate();
        if (!fadingOut) fadeTo(1);
      }
    }

    function onPause() {
      setPlayback((current) => current === "playing" ? "paused" : current);
    }

    function onEnded() {
      if (!fadeLoop) return;
      cancelAnimation();
      cancelRestart();
      fadingOut = false;
      video.style.opacity = "0";
      if (shouldPlay()) {
        restartTimer = window.setTimeout(() => {
          restartTimer = undefined;
          if (!shouldPlay()) return;
          video.currentTime = 0;
          syncPlayback();
        }, loopRestartDelay);
      }
    }

    function onPreferenceChange() {
      if (preference.matches && userChoice === "play") userChoice = null;
      syncPlayback();
    }

    toggle.current = () => {
      userChoice = shouldPlay() && (!video.paused || restartTimer !== undefined) ? "pause" : "play";
      syncPlayback();
    };
    video.addEventListener("playing", onPlaying);
    video.addEventListener("pause", onPause);
    video.addEventListener("timeupdate", onTimeUpdate);
    video.addEventListener("ended", onEnded);
    video.addEventListener("error", fallBackToPoster);
    document.addEventListener("visibilitychange", syncPlayback);
    preference.addEventListener("change", onPreferenceChange);
    const observer = typeof IntersectionObserver === "undefined" ? undefined : new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting && entry.intersectionRatio >= 0.05;
      syncPlayback();
    }, { threshold: 0.05 });
    observer?.observe(wrapper);
    syncPlayback();

    return () => {
      disposed = true;
      toggle.current = undefined;
      observer?.disconnect();
      cancelAnimation();
      cancelRestart();
      video.removeEventListener("playing", onPlaying);
      video.removeEventListener("pause", onPause);
      video.removeEventListener("timeupdate", onTimeUpdate);
      video.removeEventListener("ended", onEnded);
      video.removeEventListener("error", fallBackToPoster);
      document.removeEventListener("visibilitychange", syncPlayback);
      preference.removeEventListener("change", onPreferenceChange);
      video.pause();
      hls?.destroy();
      video.removeAttribute("src");
      video.load();
    };
  }, [src, fadeLoop]);

  const playing = playback === "playing";
  const Icon = playing ? Pause : Play;
  return (
    <div ref={media} className={["world-media", className].filter(Boolean).join(" ")} data-position={position} data-shift={shift}>
      <div className="world-media-poster" style={{ backgroundImage: `url(${poster})` }} data-visible={playback === "poster" || playback === "failed"} aria-hidden="true" />
      <video ref={player} className="world-media-video" poster={poster} muted loop={!fadeLoop} playsInline preload="none" aria-hidden="true" />
      {playback !== "failed" && <button type="button" className="world-media-toggle" aria-label={playing ? "暂停背景视频" : "播放背景视频"} title={playing ? "暂停背景视频" : "播放背景视频"} onClick={() => toggle.current?.()}>
        <Icon size={16} aria-hidden="true" />
      </button>}
    </div>
  );
}

export default WorldMedia;
