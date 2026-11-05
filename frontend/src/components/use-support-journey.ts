"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createSupportClient, SupportError } from "../lib/support-client";
import type { ArchiveInfo, Scope, SendSupportMessage, StateAnalysis, SupportHealth, SupportNotice, SupportResponse, SupportState, UnderstandingView, WearableCatalog, WearableProvider, WearableTrends } from "../lib/support-types";
import { cancellableRun } from "./support-journey-state";

const api = createSupportClient();
const fallbackSupport: SupportNotice = {
  review_status: "unreviewed", message: "你可以随时联系信任的人，或查找所在地的现实支持。", actions: ["联系信任的人", "查找所在地现实支持"], automatic_contact: false,
};
const uuid = () => crypto.randomUUID();

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
    if (!id || acting.current) return;
    const ticket = ++signalReadTicket.current;
    const expectedRevision = stateRef.current?.wearables.revision;
    const [nextTrends, nextAnalysis] = await Promise.all([
      api.trends(id, { as_of: at, days }), api.analysis(id, { as_of: at, scope: "local_analysis", timezone: "Asia/Shanghai" }),
    ]);
    if (mounted.current && sid.current === id && ticket === signalReadTicket.current
      && expectedRevision === stateRef.current?.wearables.revision
      && nextTrends.revision === expectedRevision && nextAnalysis.evidence_revision === expectedRevision) {
      setTrends(nextTrends); setAnalysis(nextAnalysis);
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
    void refreshSignals().catch(fail);
  }, [active, action, state?.wearables.revision, state?.session.session_id, asOf, range, refreshSignals, fail]);

  const start = () => actionRun("开启旅程", async () => {
    const h = await api.health(); setHealth(h);
    if (!h.runtime.psychology_enabled) throw new Error("后端尚未开启支持流程，请设置 PSYCHOLOGY_ENABLED=true 后重启服务。");
    if (sid.current && stateRef.current?.session.status === "ACTIVE") throw new Error("请先结束当前旅程，再开启新的旅程。");
    const session = await api.createSession();
    sid.current = session.session_id; setResponse(null); setTrends(null); setAnalysis(null); setLastAttempt(null); setFailedAttempt(false);
    await refresh(session.session_id);
    setNotice("旅程已开启。默认只在后端内存中保留；保存到本机需要单独同意。");
  });

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
    await refresh(id); setNotice("后端内存中的对话、理解、设备和授权已清空。磁盘存档仍保留，可在成长档案中单独删除。");
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
    setRange: (days: 7 | 14 | 30) => { ++signalReadTicket.current; setTrends(null); updateRange(days); }, start, refreshAll, send, stop, clear, end, simulate, consent, propose, edit, decide, save, load, removeArchive,
    retry: (same: boolean) => lastAttempt ? send(same ? lastAttempt : { ...lastAttempt, request_id: uuid() }) : Promise.resolve<SupportResponse | undefined>(undefined),
  };
}
export type JourneyController = ReturnType<typeof useSupportJourney>;
