"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createSupportClient, SupportError, type SupportClient } from "../lib/support-client";
import type { ArchiveInfo, Scope, SendSupportMessage, StateAnalysis, SupportHealth, SupportNotice, SupportResponse, SupportState, UnderstandingView, WearableCatalog, WearableProvider, WearableTrends } from "../lib/support-types";
import { cancellableRun } from "./support-journey-state";

const api = createSupportClient();
const fallbackSupport: SupportNotice = {
  review_status: "unreviewed", message: "你可以随时联系信任的人，或查找所在地的现实支持。", actions: ["联系信任的人", "查找所在地现实支持"], automatic_contact: false,
};
const uuid = () => crypto.randomUUID();

/** Ended journeys remain viewable but no longer request active-session analysis. */
export async function readActiveSignals(
  client: Pick<SupportClient, "trends" | "analysis">,
  state: SupportState | null,
  at: string,
  days: 7 | 14 | 30,
) {
  if (!state || state.session.status !== "ACTIVE") return null;
  const id = state.session.session_id;
  const [trends, analysis] = await Promise.all([
    client.trends(id, { as_of: at, days }),
    client.analysis(id, { as_of: at, scope: "local_analysis", timezone: "Asia/Shanghai" }),
  ]);
  if (trends.session_id !== id || analysis.session_id !== id
    || trends.revision !== state.wearables.revision || analysis.evidence_revision !== state.wearables.revision) return null;
  return { trends, analysis };
}

/** One explicit UI confirmation, with server-owned object versions and credentials. */
export async function commitUnderstandingVersion(
  client: Pick<SupportClient, "addReport" | "proposeUnderstanding" | "edit" | "confirm">,
  sessionId: string,
  text: string,
  existing?: UnderstandingView,
): Promise<UnderstandingView> {
  const interpretation = text.trim();
  if (!interpretation || interpretation.length > 2000) throw new Error("理解文字需为 1–2000 个字符。");
  if (existing && !["proposed", "confirmed"].includes(existing.understanding.status)) throw new Error("旧理解已失效，请刷新后重新核对。");
  const report = await client.addReport(sessionId, { operation_id: uuid(), content: interpretation });
  const proposal = {
    operation_id: uuid(), interpretation, origin: "user_report" as const,
    evidence_refs: [{ kind: "user_report", source_id: report.report_id, revision: report.revision }],
  };
  const current = existing
    ? await client.edit(sessionId, existing.understanding.understanding_id, { ...proposal, expected_revision: existing.understanding.revision })
    : await client.proposeUnderstanding(sessionId, proposal);
  const value = current.understanding;
  if (value.status !== "proposed" || !current.confirmation_token || value.interpretation !== interpretation
    || (existing && value.understanding_id !== existing.understanding.understanding_id)
    || (existing && value.revision <= existing.understanding.revision)) {
    throw new Error("后端返回的待确认理解与本次内容不一致，请刷新后核对。");
  }
  const confirmed = await client.confirm(sessionId, value.understanding_id, {
    operation_id: uuid(), expected_revision: value.revision, confirmation_token: current.confirmation_token,
  });
  const result = confirmed.understanding;
  if (result.understanding_id !== value.understanding_id || result.interpretation !== interpretation
    || result.status !== "confirmed" || result.confirmed_revision !== result.revision || result.revision !== value.revision + 1) {
    throw new Error("未收到这份理解的有效确认结果，请刷新当前版本。");
  }
  return confirmed;
}

export function useSupportJourney(active: boolean) {
  const [health, setHealth] = useState<SupportHealth | null>(null);
  const [state, setState] = useState<SupportState | null>(null);
  const [catalog, setCatalog] = useState<WearableCatalog | null>(null);
  const [support, setSupport] = useState<SupportNotice>(fallbackSupport);
  const [response, setResponse] = useState<SupportResponse | null>(null);
  const [trends, setTrends] = useState<WearableTrends | null>(null);
  const [analysis, setAnalysis] = useState<StateAnalysis | null>(null);
  const [archives, setArchives] = useState<ArchiveInfo[]>([]);
  const [pending, setPending] = useState(false);
  const [action, setAction] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [lastAttempt, setLastAttempt] = useState<SendSupportMessage | null>(null);
  const [failedAttempt, setFailedAttempt] = useState(false);
  const [asOf, setAsOf] = useState(() => new Date().toISOString());
  const [range, updateRange] = useState<7 | 14 | 30>(14);
  const businessEpoch = useRef(0);
  const stateReadTicket = useRef(0);
  const signalReadTicket = useRef(0);
  const sid = useRef<string | null>(null);
  const mounted = useRef(true);
  const running = useRef<{ id: string; controller: AbortController } | null>(null);
  const acting = useRef(false);
  const stateRef = useRef<SupportState | null>(null);
  const configured = useRef(false);
  const opening = useRef<Promise<string | undefined> | null>(null);

  const fail = useCallback((issue: unknown) => {
    if (!mounted.current) return;
    setError(issue instanceof Error ? issue.message : "操作没有完成，请刷新状态后重试。");
    if (issue instanceof SupportError && issue.supportNotice) setSupport(issue.supportNotice);
  }, []);

  const refresh = useCallback(async (id = sid.current) => {
    if (!id) return null;
    const ticket = ++stateReadTicket.current;
    const next = await api.getSupportState(id);
    if (!mounted.current || sid.current !== id || ticket !== stateReadTicket.current) return null;
    const current = stateRef.current;
    if (current?.session.session_id === id && (next.session.revision < current.session.revision || next.wearables.revision < current.wearables.revision)) return null;
    stateRef.current = next;
    setState(next);
    return next;
  }, []);

  const refreshSignals = useCallback(async (id = sid.current, at = asOf, days = range) => {
    if (!id || acting.current || stateRef.current?.session.session_id !== id || stateRef.current.session.status !== "ACTIVE") return;
    const ticket = ++signalReadTicket.current;
    const expectedRevision = stateRef.current?.wearables.revision;
    try {
      const result = await readActiveSignals(api, stateRef.current, at, days);
      if (result && mounted.current && sid.current === id && ticket === signalReadTicket.current
        && stateRef.current?.session.status === "ACTIVE" && expectedRevision === stateRef.current.wearables.revision) {
        setTrends(result.trends); setAnalysis(result.analysis);
      }
    } catch (issue) {
      // A stale read rejected during end/revocation must not overwrite the
      // successful lifecycle notice with an irrelevant analysis conflict.
      if (mounted.current && sid.current === id && ticket === signalReadTicket.current
        && stateRef.current?.session.status === "ACTIVE") throw issue;
    }
  }, [asOf, range]);

  const actionRun = useCallback(async <T,>(label: string, task: () => Promise<T>, invalidates = true): Promise<T | undefined> => {
    if (acting.current) return;
    acting.current = true;
    ++stateReadTicket.current; ++signalReadTicket.current;
    if (invalidates) ++businessEpoch.current;
    setAction(label); setError(""); setNotice("");
    try { return await task(); }
    catch (issue) { fail(issue); if (sid.current) await refresh().catch(() => undefined); }
    finally { acting.current = false; if (mounted.current) setAction(""); }
  }, [fail, refresh]);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    if (!active || configured.current) return;
    configured.current = true;
    Promise.all([api.health(), api.catalog()]).then(([h, c]) => {
      if (mounted.current) { setHealth(h); setCatalog(c); }
    }).catch((issue) => { configured.current = false; fail(issue); });
    api.support().then((value) => { if (mounted.current) setSupport(value); }).catch(() => undefined);
    api.listArchives().then((value) => { if (mounted.current) setArchives(value); }).catch(() => undefined);
  }, [active, fail]);

  useEffect(() => {
    if (!pending) return;
    let alive = true, polling = false;
    const poll = async () => {
      if (!alive || polling || acting.current) return;
      polling = true;
      try { await refresh(); } catch { /* The request itself owns error reporting. */ }
      finally { polling = false; }
    };
    void poll();
    const timer = setInterval(() => void poll(), 450);
    return () => { alive = false; clearInterval(timer); };
  }, [pending, refresh]);

  // Clearing, revocation and edits can invalidate previously fetched views.
  useEffect(() => {
    if (!active || !state || action) return;
    setTrends(null); setAnalysis(null);
    if (state.session.status !== "ACTIVE") { ++signalReadTicket.current; return; }
    void refreshSignals().catch(fail);
  }, [active, action, state?.wearables.revision, state?.session.session_id, state?.session.status, asOf, range, refreshSignals, fail]);

  const ensureSession = (): Promise<string | undefined> => {
    if (opening.current) return opening.current;
    if (!mounted.current || acting.current || running.current) return Promise.resolve(undefined);
    const task = actionRun("准备本次对话", async () => {
      const h = await api.health();
      if (!mounted.current) return;
      setHealth(h);
      if (!h.runtime.psychology_enabled) throw new Error("后端尚未开启支持流程，请设置 PSYCHOLOGY_ENABLED=true 后重启服务。");
      let previousMissing = false;
      if (sid.current) {
        try {
          const current = await refresh(sid.current);
          if (!mounted.current) return;
          if (current?.session.status === "ACTIVE") return current.session.session_id;
          if (!current) return;
        } catch (issue) {
          if (!(issue instanceof SupportError) || issue.status !== 404 || issue.code !== "session_not_found") throw issue;
          sid.current = null; stateRef.current = null; setState(null);
          previousMissing = true;
        }
      }
      const session = await api.createSession();
      if (!mounted.current) return;
      sid.current = session.session_id; setResponse(null); setTrends(null); setAnalysis(null); setLastAttempt(null); setFailedAttempt(false);
      const next = await refresh(session.session_id);
      if (!next) return;
      setNotice(previousMissing
        ? "上次未保存的后端会话已不存在。本次已开启新的对话；旧内容没有自动恢复。"
        : "对话已准备好。默认只在后端内存中保留；保存到本机需要单独同意。");
      return session.session_id;
    });
    opening.current = task;
    void task.finally(() => { if (opening.current === task) opening.current = null; });
    return task;
  };
  const start = ensureSession;

  async function send(body: SendSupportMessage) {
    const id = sid.current;
    if (!id || running.current || acting.current) return;
    const controller = new AbortController();
    const epoch = businessEpoch.current;
    running.current = { id: body.request_id, controller };
    setPending(true); setLastAttempt(body); setFailedAttempt(false); setError(""); setNotice("");
    try {
      const result = await api.sendMessage(id, body, controller.signal);
      if (sid.current !== id || running.current?.controller !== controller || !mounted.current) return;
      if (epoch !== businessEpoch.current || result.revision < (stateRef.current?.session.revision ?? 0)) { await refresh(id); return; }
      if (!result.psychology) throw new Error("本次没有返回结构化支持结果，请确认后端已启用心理流程。页面没有生成替代结果。");
      setResponse(result);
      // A validated response is a completed operation even if the following read fails.
      await refresh(id).catch(fail);
      if (epoch === businessEpoch.current && sid.current === id && running.current?.controller === controller) return result;
    } catch (issue) {
      if (!controller.signal.aborted && running.current?.controller === controller && epoch === businessEpoch.current) {
        setFailedAttempt(true); fail(issue); await refresh(id).catch(() => undefined);
      }
    } finally {
      if (running.current?.controller === controller) { running.current = null; if (mounted.current) setPending(false); }
    }
  }

  const stop = () => actionRun("停止生成", async () => {
    const id = sid.current, current = running.current;
    if (!id || !current) return;
    const latest = await refresh(id);
    const run = cancellableRun(latest, current.id);
    if (!run) {
      setNotice("后台尚未返回本次运行编号，或本次运行已完成。请先刷新状态；没有取消其他运行。");
      return;
    }
    await api.cancel(id, run);
    if (running.current === current) { current.controller.abort(); running.current = null; setPending(false); }
    setFailedAttempt(false); await refresh(id);
    setNotice("已停止这次生成。之前完成的内容仍然保留。");
  });

  const clear = () => actionRun("清空当前旅程", async () => {
    const id = sid.current; if (!id) return;
    setTrends(null); setAnalysis(null); setResponse(null);
    await api.clear(id);
    running.current?.controller.abort(); running.current = null; setPending(false);
    setResponse(null); setLastAttempt(null); setFailedAttempt(false); setTrends(null); setAnalysis(null);
    // The mutation already committed. A failed follow-up read must neither keep
    // old content on screen nor turn a successful clear into a reported failure.
    stateRef.current = null; setState(null);
    const cleared = await refresh(id).catch((issue) => { fail(issue); return null; });
    setNotice(cleared
      ? "后端内存中的对话、理解、设备和授权已清空。磁盘存档仍保留，可在成长档案中单独删除。"
      : "后端内容已清空，但当前状态暂时无法读取，请刷新后继续。磁盘存档仍保留。");
    return true;
  });

  const end = () => actionRun("结束旅程", async () => {
    const id = sid.current; if (!id) return;
    await api.end(id); running.current?.controller.abort(); running.current = null; setPending(false); setFailedAttempt(false);
    await refresh(id); setNotice("旅程已结束，之前完成的内容仍可查看。结束不等于删除；需要移除内容时请选择清空。");
  });

  const simulate = (scenario: string) => actionRun("导入模拟记录", async () => {
    const id = sid.current, latest = stateRef.current; if (!id || !latest) return;
    const at = new Date().toISOString();
    await api.simulate(id, { operation_id: uuid(), expected_revision: latest.wearables.revision, as_of: at, scenario_id: scenario, seed: 42, days: 21 });
    setAsOf(at); await refresh(id); setNotice("已导入 21 天虚构设备记录。导入不会自动允许本地分析或模型使用，请在下方选择用途。");
  });

  const consent = (provider: WearableProvider, metrics: string[], scopes: Scope[]) => actionRun("更新设备用途", async () => {
    const id = sid.current, latest = stateRef.current; if (!id || !latest) return;
    setTrends(null); setAnalysis(null); setResponse(null);
    await api.setConsent(id, { operation_id: uuid(), expected_revision: latest.wearables.revision, provider: provider.provider, allowed_metrics: metrics, scopes });
    setResponse(null); setTrends(null); setAnalysis(null); await refresh(id); await refreshSignals(id);
    setNotice(scopes.length ? "用途选择已更新。只有允许进入对话的设备观察才会被模型使用。" : "设备用途已撤回。依赖旧设备来源的回复将不再进入后续模型上下文。");
  });

  const propose = (text: string) => actionRun("记录理解", async () => {
    const id = sid.current; if (!id || !text.trim()) return;
    const report = await api.addReport(id, { operation_id: uuid(), content: text.trim() });
    const created = await api.proposeUnderstanding(id, { operation_id: uuid(), interpretation: text.trim(), origin: "user_report", evidence_refs: [{ kind: "user_report", source_id: report.report_id, revision: report.revision }] });
    await refresh(id).catch(fail); setNotice("理解已记录为待确认。确认当前版本后，才会进入后续支持上下文。");
    return created;
  });

  const edit = (view: UnderstandingView, text: string) => actionRun("修改理解", async () => {
    const id = sid.current; if (!id || !text.trim()) return;
    const u = view.understanding;
    await api.edit(id, u.understanding_id, { operation_id: uuid(), expected_revision: u.revision, interpretation: text.trim(), origin: u.origin, evidence_refs: u.evidence_refs });
    setResponse(null); await refresh(id); setNotice("理解已更新。旧版本确认失效，请核对并确认新版。");
  });

  const decide = (view: UnderstandingView, decision: "confirm" | "reject") => actionRun(decision === "confirm" ? "确认理解" : "拒绝理解", async () => {
    const id = sid.current; if (!id) return;
    const u = view.understanding, operation = { operation_id: uuid(), expected_revision: u.revision };
    if (decision === "confirm") {
      if (!view.confirmation_token) throw new Error("当前版本没有确认凭据，请刷新后重试。");
      await api.confirm(id, u.understanding_id, { ...operation, confirmation_token: view.confirmation_token });
    } else await api.reject(id, u.understanding_id, operation);
    setResponse(null); await refresh(id);
    setNotice(decision === "confirm" ? "已确认当前版本。拾光将在后续对话中采用这份理解。" : "这份理解已拒绝，将不再用于后续支持。");
  });

  const commitUnderstanding = (text: string, existing?: UnderstandingView) => actionRun("确认当前理解", async () => {
    const id = sid.current;
    if (!id) throw new Error("请先开始一次对话，再确认理解。");
    const confirmed = await commitUnderstandingVersion(api, id, text, existing);
    setResponse(null);
    await refresh(id).catch(fail);
    setNotice("已确认当前四栏内容。后续支持将采用这个版本。");
    return confirmed;
  });

  const save = () => actionRun("保存旅程", async () => {
    const id = sid.current; if (!id) return;
    const latest = await refresh(id); if (!latest) return;
    const currentArchives = await api.listArchives();
    const current = currentArchives.find((entry) => entry.archive_id === id);
    await api.saveArchive(id, { expected_revision: latest.session.revision, expected_archive_version: current?.archive_version ?? null, save_business_data: true });
    setArchives(await api.listArchives()); setNotice("已按你的选择保存到本机磁盘。删除磁盘存档与清空当前旅程是独立操作。");
  });

  const load = (archive: ArchiveInfo) => actionRun("打开存档", async () => {
    if (running.current) throw new Error("请先停止当前生成，再打开另一段旅程。");
    if (sid.current && sid.current !== archive.archive_id && stateRef.current?.session.status === "ACTIVE") throw new Error("请先结束当前旅程，再打开另一份存档。");
    let restored = true;
    try { await api.restoreArchive(archive.archive_id); }
    catch (issue) {
      if (!(issue instanceof SupportError) || issue.code !== "session_already_loaded") throw issue;
      restored = false;
    }
    sid.current = archive.archive_id; setResponse(null); setTrends(null); setAnalysis(null); setLastAttempt(null); setFailedAttempt(false);
    await refresh(archive.archive_id);
    setNotice(restored ? "已从本机存档恢复。设备用途授权已清零，请重新选择；相关理解需重新核对。" : "已打开后端内存中的这段旅程，没有用磁盘存档覆盖当前内容。");
  });

  const removeArchive = (archive: ArchiveInfo) => actionRun("删除磁盘存档", async () => {
    await api.deleteArchive(archive.archive_id, archive.archive_version);
    setArchives(await api.listArchives()); setNotice("磁盘存档已删除。已打开的内存旅程仍保留，可单独清空。");
  });

  const refreshAll = () => actionRun("刷新状态", async () => {
    const [h, c, a] = await Promise.all([api.health(), api.catalog(), api.listArchives()]);
    setHealth(h); setCatalog(c); setArchives(a); await refresh(); await refreshSignals();
  }, false);

  return { health, state, catalog, support, response, trends, analysis, archives, pending, action, error, notice, lastAttempt, failedAttempt, asOf, range,
    setRange: (days: 7 | 14 | 30) => { ++signalReadTicket.current; setTrends(null); updateRange(days); }, start, ensureSession, refreshAll, send, stop, clear, end, simulate, consent, propose, edit, decide, commitUnderstanding, save, load, removeArchive,
    retry: (same: boolean) => lastAttempt ? send(same ? lastAttempt : { ...lastAttempt, request_id: uuid() }) : Promise.resolve<SupportResponse | undefined>(undefined),
  };
}
export type JourneyController = ReturnType<typeof useSupportJourney>;
