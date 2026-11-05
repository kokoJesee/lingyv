import type {
  ArchiveInfo, ConsentRequest, Operation, SendSupportMessage, SimulationRequest, StateAnalysis,
  SupportHealth, SupportNotice, SupportResponse, SupportSession, SupportState, UnderstandingProposal,
  UnderstandingView, UserReport, WearableCatalog, WearableReceipt, WearableTrends,
} from "./support-types";

const messages: Record<string, string> = {
  not_configured: "本地支持接口未启用，请先启动后端并配置前端中转地址。",
  invalid_input: "提交内容不符合接口要求，请检查后重试。",
  session_not_found: "这段旅程已不存在，请重新开始。",
  session_ended: "这段旅程已经结束，请开始新的旅程。",
  session_busy: "当前操作仍在进行，请稍后重试。",
  stale_revision: "内容或授权已更新，请刷新当前版本后再操作。",
  source_withdrawn: "这次回复依赖的来源已撤回，不能再次使用。",
  request_id_conflict: "请求编号与内容不一致，请刷新后重试。",
  request_restored: "这个请求来自恢复的记录；如需继续，请发送新消息。",
  request_cleared: "这次请求的内容已被清空。",
  run_cancelled: "这次生成已停止，没有提交未完成的回复。",
  model_timeout: "本次支持响应超时。你可以重试或使用现实支持入口。",
  model_not_configured: "模型未配置。可由服务端显式启用离线工程演示。",
  model_unavailable: "本次自动支持未能完成，已保留之前的内容。",
  context_limit_exceeded: "这段旅程已超过上下文预算，请保存后开始新旅程。",
  archive_business_consent_required: "请明确同意保存本次旅程的业务数据。",
  archive_version_conflict: "存档版本已变化，请刷新列表后再操作。",
  session_already_loaded: "该存档已在当前后端打开，请使用已打开的旅程。",
  upstream_error: "本地支持接口未能完成请求，请检查服务后重试。",
};

export class SupportError extends Error {
  readonly code: string;
  readonly status: number;
  readonly supportNotice?: SupportNotice;
  constructor(code: string, status: number, supportNotice?: SupportNotice) {
    super(messages[code] ?? (status === 409 ? "当前版本或运行状态有变化，请刷新后重试。" : messages.upstream_error));
    this.name = "SupportError";
    this.code = code;
    this.status = status;
    this.supportNotice = supportNotice;
  }
}

function notice(value: unknown): SupportNotice | undefined {
  if (!value || typeof value !== "object") return undefined;
  const entry = value as Partial<SupportNotice>;
  return entry.review_status === "unreviewed" && typeof entry.message === "string"
    && Array.isArray(entry.actions) && entry.actions.every((action) => typeof action === "string")
    && entry.automatic_contact === false ? entry as SupportNotice : undefined;
}

function identity(value: string): string {
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(value)) throw new Error("无效的对象编号。");
  return value;
}

export function createSupportClient(base = "/api/support") {
  if (!/^\/[a-z0-9/_-]+$/i.test(base) || base.startsWith("//") || base.endsWith("/")) {
    throw new Error("支持接口必须为本站的相对路径。");
  }

  async function request<T>(path: string, method = "GET", body?: unknown, signal?: AbortSignal): Promise<T> {
    signal?.throwIfAborted();
    let response: Response;
    try {
      response = await fetch(`${base}${path}`, {
        method, signal, credentials: "same-origin", cache: "no-store", redirect: "error",
        headers: body === undefined ? undefined : { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch {
      signal?.throwIfAborted();
      throw new SupportError("upstream_error", 502);
    }
    if (response.status === 204 && response.ok) return undefined as T;
    const result: unknown = await response.json().catch(() => null);
    signal?.throwIfAborted();
    if (!response.ok) {
      const failure = result && typeof result === "object" ? result as Record<string, unknown> : {};
      throw new SupportError(typeof failure.code === "string" ? failure.code : "upstream_error", response.status, notice(failure.support_notice));
    }
    if (result === null || typeof result !== "object") throw new SupportError("upstream_error", 502);
    return result as T;
  }

  const sessionPath = (session: string) => `/sessions/${identity(session)}`;
  const understandingPath = (session: string, understanding: string) => `${sessionPath(session)}/understandings/${identity(understanding)}`;
  return {
    health: (signal?: AbortSignal) => request<SupportHealth>("/health", "GET", undefined, signal),
    support: (signal?: AbortSignal) => request<SupportNotice>("/support", "GET", undefined, signal),
    catalog: (signal?: AbortSignal) => request<WearableCatalog>("/wearables/catalog", "GET", undefined, signal),
    createSession: () => request<SupportSession>("/sessions", "POST"),
    getSupportState: (session: string, signal?: AbortSignal) => request<SupportState>(`${sessionPath(session)}/support-state`, "GET", undefined, signal),
    // A request ID belongs to the caller. Network retries reuse it; new attempts use a new ID.
    sendMessage: (session: string, body: SendSupportMessage, signal?: AbortSignal) => request<SupportResponse>(`${sessionPath(session)}/messages`, "POST", body, signal),
    // Cancellation targets a known run. Aborting fetch alone never clears the conversation.
    cancel: (session: string, runId: string) => request<SupportSession>(`${sessionPath(session)}/workflow/cancel`, "POST", { run_id: runId }),
    clear: (session: string) => request<SupportSession>(`${sessionPath(session)}/messages`, "DELETE"),
    end: (session: string) => request<SupportSession>(`${sessionPath(session)}/end`, "POST"),
    simulate: (session: string, body: SimulationRequest) => request<WearableReceipt>(`${sessionPath(session)}/wearables/simulate`, "POST", body),
    setConsent: (session: string, body: ConsentRequest) => request<WearableReceipt>(`${sessionPath(session)}/wearables/consent`, "POST", body),
    trends: (session: string, params: { as_of: string; days: 7 | 14 | 30; timezone?: string }, signal?: AbortSignal) => {
      const query = new URLSearchParams({ as_of: params.as_of, days: String(params.days), timezone: params.timezone ?? "Asia/Shanghai" });
      return request<WearableTrends>(`${sessionPath(session)}/wearables/trends?${query}`, "GET", undefined, signal);
    },
    analysis: (session: string, body: { as_of: string; timezone?: string; scope?: "local_analysis" | "model_context" }) => request<StateAnalysis>(`${sessionPath(session)}/analysis`, "POST", body),
    addReport: (session: string, body: Operation & { content: string }) => request<UserReport>(`${sessionPath(session)}/reports`, "POST", body),
    proposeUnderstanding: (session: string, body: UnderstandingProposal) => request<UnderstandingView>(`${sessionPath(session)}/understandings`, "POST", body),
    edit: (session: string, understanding: string, body: UnderstandingProposal & { expected_revision: number }) => request<UnderstandingView>(understandingPath(session, understanding), "PUT", body),
    confirm: (session: string, understanding: string, body: Operation & { expected_revision: number; confirmation_token: string }) => request<UnderstandingView>(`${understandingPath(session, understanding)}/confirm`, "POST", body),
    reject: (session: string, understanding: string, body: Operation & { expected_revision: number }) => request<UnderstandingView>(`${understandingPath(session, understanding)}/reject`, "POST", body),
    listArchives: () => request<ArchiveInfo[]>("/archives"),
    saveArchive: (session: string, body: { expected_revision: number; expected_archive_version?: number | null; save_business_data: boolean }) => request<ArchiveInfo>(`${sessionPath(session)}/archive`, "POST", body),
    restoreArchive: (archive: string) => request<SupportSession>(`/archives/${identity(archive)}/restore`, "POST"),
    deleteArchive: (archive: string, expectedVersion: number) => request<void>(`/archives/${identity(archive)}?${new URLSearchParams({ expected_version: String(expectedVersion) })}`, "DELETE"),
  };
}

export type SupportClient = ReturnType<typeof createSupportClient>;
