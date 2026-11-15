"use client";

import { Check, ChevronDown, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import type { JourneyController } from "./use-support-journey";

const savedDate = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric", month: "long", day: "numeric", hour: "2-digit", minute: "2-digit",
});

/** A quiet, optional control within the original archive page, not a card store. */
export function CinematicArchiveControls({ journey }: { journey: JourneyController }) {
  const [saveConsent, setSaveConsent] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; version: number } | null>(null);
  const session = journey.state?.session;
  const busy = journey.pending || !!journey.action || session?.workflow.phase === "RUNNING";

  useEffect(() => { setSaveConsent(false); }, [session?.session_id, session?.revision]);

  return <details className="cr-glass cr-journey-archive">
    <summary><span><strong>本机旅程存档</strong><small>单独保存对话、理解与设备记录</small></span><ChevronDown size={18} className="cr-card-chevron" /></summary>
    <div className="cr-journey-archive-body">
      <p>上方的经历卡只在本页暂存。这里可另存旅程中的对话、理解、自述与设备记录，之后重新打开；不会把经历卡当作已保存到磁盘的旅程。</p>
      <p className="cr-journey-archive-note">文件以明文保存在运行服务的本机。恢复时需重新选择设备用途；清空旅程与删除磁盘存档是两项独立操作。</p>
      <label className="cr-journey-save-choice"><input type="checkbox" checked={saveConsent} disabled={!session || busy} onChange={(event) => setSaveConsent(event.target.checked)} /><span>我选择将当前旅程的对话、理解、自述和设备记录保存到本机</span></label>
      <div className="cr-journey-archive-actions">
        <button type="button" className="cr-button cr-glass" disabled={!session || busy || !saveConsent} onClick={() => { setSaveConsent(false); void journey.save(); }}><Check size={16} />保存当前旅程</button>
        <button type="button" className="cr-button" disabled={busy} onClick={() => void journey.refreshAll()}><RefreshCw size={15} />刷新本机存档</button>
      </div>
      {!session && <p className="cr-journey-archive-note">开启一段对话后，可以在这里选择保存；已有的本机存档也可直接打开。</p>}
      {journey.error && <p className="cr-journey-archive-error" role="alert">{journey.error}</p>}
      {journey.notice && <p className="cr-journey-archive-note" role="status">{journey.notice}</p>}
      {journey.archives.length ? <ul className="cr-journey-archive-list">{journey.archives.map((entry) => {
        const confirming = deleteTarget?.id === entry.archive_id && deleteTarget.version === entry.archive_version;
        return <li key={entry.archive_id}>
          <div><strong>{savedDate.format(new Date(entry.saved_at))}</strong><small>{entry.message_count / 2} 轮对话 · {entry.snapshot_version === 2 ? "含旅程业务数据" : "普通会话"} · {entry.status === "ENDED" ? "已结束" : "可继续"}</small></div>
          <div className="cr-journey-archive-actions">
            <button type="button" className="cr-button cr-glass" disabled={busy} onClick={() => { setSaveConsent(false); setDeleteTarget(null); void journey.load(entry); }}>打开</button>
            {confirming ? <>
              <button type="button" className="cr-button" disabled={busy} onClick={() => { setDeleteTarget(null); void journey.removeArchive(entry); }}>确认删除这份磁盘存档</button>
              <button type="button" className="cr-button" disabled={busy} onClick={() => setDeleteTarget(null)}>保留</button>
            </> : <button type="button" className="cr-button" disabled={busy} onClick={() => setDeleteTarget({ id: entry.archive_id, version: entry.archive_version })}>删除</button>}
          </div>
        </li>;
      })}</ul> : <p className="cr-journey-archive-note">还没有本机旅程存档。保存由你主动选择。</p>}
    </div>
  </details>;
}
