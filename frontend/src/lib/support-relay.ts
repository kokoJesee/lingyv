/** Explicit local-only BFF for the persistent support journey; never an open proxy. */
const loopbackHosts = new Set(["localhost", "127.0.0.1", "[::1]"]);
const uuid = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
const objectId = "[A-Za-z0-9_-]{1,128}";
const maxSmallBody = 8192;
const maxImportBody = 4 * 1024 * 1024;
const maxResponseBody = 4 * 1024 * 1024;
const routes = new Set(["routine_support", "clarify_safety", "safety_support", "urgent_real_world_help"]);

type Projection = true | { [key: string]: Projection } | [Projection];
const scalars = (...keys: string[]): Record<string, Projection> => Object.fromEntries(keys.map((key) => [key, true]));
const workflow: Projection = scalars("run_id", "kind", "phase", "step", "step_count", "stop_reason");
const session: Projection = { ...scalars("session_id", "state", "status", "revision", "created_at"), workflow, messages: [{ role: true, content: true }] };
const reference: Projection = scalars("kind", "source_id", "revision");
const draftReference: Projection = scalars("draft_id", "revision");
const understanding: Projection = {
  ...scalars("schema_version", "understanding_id", "revision", "status", "interpretation", "origin", "created_at", "updated_at", "confirmed_revision", "confirmed_at", "invalidation_reason"),
  evidence_refs: [reference],
};
const understandingView: Projection = { understanding, confirmation_token: true };
const notice: Projection = { ...scalars("review_status", "message", "automatic_contact"), actions: [true] };
const provider: Projection = { ...scalars("provider", "record_count", "is_simulated"), available_metrics: [true], allowed_metrics: [true], scopes: [true] };
const health: Projection = {
  status: true, service: true,
  llm: scalars("provider", "model", "key_configured"),
  runtime: scalars("storage", "prompt_version", "psychology_enabled", "psychology_demo_mode", "psychology_review_status", "max_steps", "max_context_chars"),
};
const psychology: Projection = {
  ...scalars("schema_version", "session_id", "request_id", "session_revision"),
  result: {
    ...scalars("route", "reply", "suggested_next_step", "memory_update", "follow_up_offer"),
    evidence_refs: [true], uncertainties: [true],
  },
};
const contextManifest: Projection = {
  ...scalars("builder_version", "prompt_version", "session_id", "session_revision", "request_id", "model_message_count", "character_count"),
  sources: [scalars("kind", "model_message_index", "source_id", "evidence_type", "history_index", "role", "draft_id", "draft_revision")],
  excluded_history_indices: [true],
  business_dependencies: { wearable_revision: true, understanding_refs: [reference], draft_refs: [draftReference] },
};
const message: Projection = {
  ...scalars("session_id", "state", "reply", "request_id", "run_id", "revision", "review_status"), workflow,
  psychology, context_manifest: contextManifest,
};
const state: Projection = {
  session, ...scalars("active_request_id", "support_mode", "support_route", "review_status"), understandings: [understandingView],
  wearables: { revision: true, record_count: true, providers: [provider] },
};
const catalog: Projection = {
  metrics: [scalars("metric_id", "unit", "value_type", "usage", "required_method")],
  scenarios: [scalars("scenario_id", "label")], is_simulated: true,
};
const trends: Projection = {
  ...scalars("session_id", "revision", "scope", "as_of", "timezone", "days"),
  series: [{
    ...scalars("metric_id", "unit", "value_type", "status"),
    points: [scalars("date", "value", "quality", "record_id")],
    source: {
      ...scalars("provider", "device_id", "measurement_method", "source_algorithm_version", "is_simulated", "aggregation_method", "window_seconds", "baseline_ref", "measurement_site", "namespace"),
      score_scale: scalars("minimum", "maximum", "direction"),
    },
  }],
};
const analysis: Projection = {
  ...scalars("schema_version", "analysis_id", "session_id", "evidence_revision", "algorithm_version", "is_simulated", "as_of", "status", "scope", "timezone"),
  metric_statuses: [scalars("metric_id", "status", "baseline_valid_days", "recent_valid_days", "excluded_days")],
  observations: [{
    ...scalars("metric_id", "baseline_valid_days", "recent_valid_days", "baseline_median", "recent_median", "delta", "relative_delta", "unit", "comparison_method", "provider", "device_id", "measurement_method", "source_algorithm_version", "baseline_ref", "timezone", "aggregation_method", "window_seconds", "measurement_site", "evidence_group", "inquiry_candidate"),
    baseline_window: scalars("start", "end"), recent_window: scalars("start", "end"), record_refs: [reference],
  }], limitations: [true],
};
const archive: Projection = scalars("archive_id", "archive_version", "snapshot_version", "session_revision", "message_count", "draft_count", "status", "saved_at");
const receipt: Projection = scalars("revision", "accepted", "unchanged", "invalid_metrics", "changed", "deleted");
type Route = { expression: RegExp; methods: string[]; output: Projection; query?: string[]; bodyKeys?: string[]; maxBody?: number };
const sessionPrefix = `/sessions/${uuid}`;
const understandingPrefix = `${sessionPrefix}/understandings/${objectId}`;
const definitions: Route[] = [
  { expression: /^\/health$/, methods: ["GET"], output: health },
  { expression: /^\/support$/, methods: ["GET"], output: notice },
  { expression: /^\/wearables\/catalog$/, methods: ["GET"], output: catalog },
  { expression: /^\/sessions$/, methods: ["POST"], output: session, bodyKeys: [] },
  { expression: new RegExp(`^${sessionPrefix}$`), methods: ["GET"], output: session },
  { expression: new RegExp(`^${sessionPrefix}/support-state$`), methods: ["GET"], output: state },
  { expression: new RegExp(`^${sessionPrefix}/messages$`), methods: ["POST"], output: message, bodyKeys: ["content", "request_id", "correction_of"] },
  { expression: new RegExp(`^${sessionPrefix}/messages$`), methods: ["DELETE"], output: session },
  { expression: new RegExp(`^${sessionPrefix}/end$`), methods: ["POST"], output: session, bodyKeys: [] },
  { expression: new RegExp(`^${sessionPrefix}/workflow/cancel$`), methods: ["POST"], output: session, bodyKeys: ["run_id"] },
  { expression: new RegExp(`^${sessionPrefix}/wearables/import$`), methods: ["POST"], output: receipt, bodyKeys: ["operation_id", "expected_revision", "batch"], maxBody: maxImportBody },
  { expression: new RegExp(`^${sessionPrefix}/wearables/simulate$`), methods: ["POST"], output: receipt, bodyKeys: ["operation_id", "expected_revision", "as_of", "scenario_id", "seed", "days", "timezone"] },
  { expression: new RegExp(`^${sessionPrefix}/wearables/consent$`), methods: ["POST"], output: receipt, bodyKeys: ["operation_id", "expected_revision", "provider", "allowed_metrics", "scopes"] },
  { expression: new RegExp(`^${sessionPrefix}/wearables/delete$`), methods: ["POST"], output: receipt, bodyKeys: ["operation_id", "expected_revision", "record_id"] },
  { expression: new RegExp(`^${sessionPrefix}/wearables/trends$`), methods: ["GET"], output: trends, query: ["as_of", "days", "timezone"] },
  { expression: new RegExp(`^${sessionPrefix}/analysis$`), methods: ["POST"], output: analysis, bodyKeys: ["as_of", "timezone", "scope"] },
  { expression: new RegExp(`^${sessionPrefix}/reports$`), methods: ["POST"], output: scalars("report_id", "content", "revision"), bodyKeys: ["operation_id", "content"] },
  { expression: new RegExp(`^${sessionPrefix}/reports/${objectId}/delete$`), methods: ["POST"], output: receipt, bodyKeys: ["operation_id", "expected_revision"] },
  { expression: new RegExp(`^${sessionPrefix}/understandings$`), methods: ["POST"], output: understandingView, bodyKeys: ["operation_id", "interpretation", "origin", "evidence_refs"] },
  { expression: new RegExp(`^${sessionPrefix}/understandings$`), methods: ["GET"], output: [understanding], query: ["scope"] },
  { expression: new RegExp(`^${sessionPrefix}/understandings/control$`), methods: ["GET"], output: [understandingView] },
  { expression: new RegExp(`^${understandingPrefix}$`), methods: ["GET"], output: understandingView },
  { expression: new RegExp(`^${understandingPrefix}$`), methods: ["PUT"], output: understandingView, bodyKeys: ["operation_id", "expected_revision", "interpretation", "origin", "evidence_refs"] },
  { expression: new RegExp(`^${understandingPrefix}/confirm$`), methods: ["POST"], output: understandingView, bodyKeys: ["operation_id", "expected_revision", "confirmation_token"] },
  { expression: new RegExp(`^${understandingPrefix}/reject$`), methods: ["POST"], output: understandingView, bodyKeys: ["operation_id", "expected_revision"] },
  { expression: new RegExp(`^${sessionPrefix}/archive$`), methods: ["POST"], output: archive, bodyKeys: ["expected_revision", "expected_archive_version", "save_business_data"] },
  { expression: /^\/archives$/, methods: ["GET"], output: [archive] },
  { expression: new RegExp(`^/archives/${uuid}/restore$`), methods: ["POST"], output: session, bodyKeys: [] },
  { expression: new RegExp(`^/archives/${uuid}$`), methods: ["DELETE"], output: true, query: ["expected_version"] },
];

const knownErrors = new Set(`session_not_found session_ended session_busy session_mode_conflict request_id_conflict
request_limit_exceeded request_cleared request_restored source_withdrawn run_cancelled operation_id_conflict
stale_revision stale_record_revision stale_wearable_snapshot stale_understanding_revision understanding_not_found
understanding_not_confirmable understanding_not_editable understanding_source_invalid stale_confirmation
invalid_confirmation confirmation_conflict invalid_evidence_reference source_changed source_not_found
invalid_scope record_deleted record_identity_conflict wearable_too_large wearable_storage_limit
model_not_configured model_unavailable model_timeout model_http_error model_empty_response model_invalid_response
model_output_invalid output_validation_failed context_limit_exceeded step_limit_exceeded
unsupported_safety_route route_transition_unreviewed workflow_not_running workflow_run_mismatch
workflow_not_waiting workflow_confirmation_mismatch run_id_conflict stale_run stale_run_id
archive_business_version_required archive_business_consent_required archive_save_consent_required
archive_session_busy archive_version_conflict archive_invalid archive_storage_error archive_too_large
archive_not_found archive_version_unsupported archive_limit_exceeded session_already_loaded
external_call_queue_full external_call_timeout external_call_queue_timeout external_call_cancelled
understanding_stale_revision invalid_understanding_reference report_not_found invalid_input
source_conflict source_invalid session_not_editable stale_understanding operation_superseded
understanding_inactive understanding_too_large report_too_large psychology_mode_required internal_error`.split(/\s+/));

function respond(payload: unknown, status: number): Response {
  if (status === 204) return new Response(null, { status, headers: { "Cache-Control": "no-store" } });
  return Response.json(payload, { status, headers: { "Cache-Control": "no-store" } });
}

function configuredOrigin(value: string | undefined): string | undefined {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    if (!loopbackHosts.has(url.hostname) || !["http:", "https:"].includes(url.protocol)) return undefined;
    if (url.username || url.password || url.pathname !== "/" || url.search || url.hash) return undefined;
    return url.origin;
  } catch { return undefined; }
}

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function project(value: unknown, shape: Projection): unknown {
  if (value === null) return null;
  if (shape === true) {
    if (!["string", "number", "boolean"].includes(typeof value)) throw new Error("Invalid scalar");
    return value;
  }
  if (Array.isArray(shape)) {
    if (!Array.isArray(value)) throw new Error("Invalid list");
    return value.map((item) => project(item, shape[0]));
  }
  if (!record(value)) throw new Error("Invalid object");
  return Object.fromEntries(Object.entries(shape).filter(([key]) => Object.hasOwn(value, key)).map(([key, child]) => [key, project(value[key], child)]));
}

class BodyLimit extends Error {}
async function boundedText(body: ReadableStream<Uint8Array> | null, limit: number, signal: AbortSignal): Promise<string> {
  if (!body) return "";
  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  let rejectAbort!: (reason: unknown) => void;
  const aborted = new Promise<never>((_, reject) => { rejectAbort = reject; });
  const abort = () => { rejectAbort(signal.reason); void reader.cancel().catch(() => undefined); };
  signal.addEventListener("abort", abort, { once: true });
  try {
    signal.throwIfAborted();
    while (true) {
      const { value, done } = await Promise.race([reader.read(), aborted]);
      if (done) break;
      size += value.byteLength;
      if (size > limit) { void reader.cancel().catch(() => undefined); throw new BodyLimit(); }
      chunks.push(value);
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } finally {
    signal.removeEventListener("abort", abort);
    reader.releaseLock();
  }
}

function safeNotice(value: unknown): unknown {
  if (!record(value) || value.review_status !== "unreviewed" || value.automatic_contact !== false
    || typeof value.message !== "string" || value.message.length > 2000 || !Array.isArray(value.actions)
    || value.actions.length > 10 || value.actions.some((item) => typeof item !== "string" || item.length > 200)) return undefined;
  return project(value, notice);
}

function failure(value: unknown, status: number): Response {
  const detail = record(value) ? value.detail : undefined;
  const rawCode = record(detail) ? detail.code : detail === "Session not found" ? "session_not_found" : undefined;
  const code = typeof rawCode === "string" && knownErrors.has(rawCode) ? rawCode
    : status === 422 || status === 400 ? "invalid_input"
      : status === 503 ? "model_unavailable" : status === 504 ? "model_timeout" : "upstream_error";
  const supportNotice = record(value) ? safeNotice(value.support_notice) : undefined;
  return respond({ code, ...(supportNotice ? { support_notice: supportNotice } : {}) }, status >= 400 && status < 600 ? status : 502);
}

function checkResponse(value: unknown, path: string, method: string, input: Record<string, unknown> | undefined) {
  if (Array.isArray(value)) return;
  if (!record(value)) throw new Error("Invalid response");
  const sid = path.match(new RegExp(`^/sessions/(${uuid})(?:/|$)`))?.[1];
  if (sid && typeof value.session_id === "string" && value.session_id !== sid) throw new Error("Session mismatch");
  if (path === "/sessions" || path.endsWith("/restore")) {
    if (typeof value.session_id !== "string" || !new RegExp(`^${uuid}$`).test(value.session_id)
      || !record(value.workflow) || !Array.isArray(value.messages)) throw new Error("Invalid session");
  }
  if (path.endsWith("/support-state")) {
    if (!record(value.session) || value.session.session_id !== sid || !Array.isArray(value.understandings)
      || !record(value.wearables) || (value.support_route !== null && !routes.has(String(value.support_route)))) throw new Error("Invalid state");
  }
  if (path.endsWith("/messages") && method === "POST") {
    if (value.session_id !== sid || value.request_id !== input?.request_id || typeof value.reply !== "string"
      || !value.reply.trim() || value.reply.length > 32_000 || !record(value.workflow)) throw new Error("Invalid reply");
    if (value.psychology !== null && value.psychology !== undefined) {
      if (!record(value.psychology)) throw new Error("Invalid psychology response");
      const result = value.psychology.result;
      if (!record(result) || !routes.has(String(result.route)) || result.memory_update !== null
        || value.psychology.session_id !== sid || value.psychology.request_id !== input?.request_id) throw new Error("Invalid psychology result");
    }
  }
}

export async function handleSupportRequest(request: Request): Promise<Response> {
  const upstream = configuredOrigin(process.env.LOVESTONE_AGENT_API_URL);
  if (!upstream) return respond({ code: "not_configured" }, 503);
  const incoming = new URL(request.url);
  const localOrigin = configuredOrigin(`${incoming.protocol}//${request.headers.get("host") ?? incoming.host}`);
  const browserOrigin = request.headers.get("origin");
  if (!localOrigin || (browserOrigin && browserOrigin !== localOrigin)
    || request.headers.get("sec-fetch-site") === "cross-site") return respond({ code: "not_configured" }, 403);
  if (!incoming.pathname.startsWith("/api/support/") || incoming.pathname.includes("%")) return respond({ code: "invalid_input" }, 404);
  const path = incoming.pathname.slice("/api/support".length);
  const definition = definitions.find((item) => item.expression.test(path) && item.methods.includes(request.method));
  if (!definition) return respond({ code: "invalid_input" }, 404);
  for (const key of incoming.searchParams.keys()) {
    if (!definition.query?.includes(key) || incoming.searchParams.getAll(key).length !== 1) return respond({ code: "invalid_input" }, 400);
  }
  const timeout = AbortSignal.timeout(60_000);
  const signal = AbortSignal.any([request.signal, timeout]);
  let body: string | undefined;
  let input: Record<string, unknown> | undefined;
  try {
    if (request.body) {
      const limit = definition.maxBody ?? maxSmallBody;
      const length = request.headers.get("content-length");
      if (length && (!/^\d+$/.test(length) || Number(length) > limit)) return respond({ code: "invalid_input" }, Number(length) > limit ? 413 : 400);
      const raw = await boundedText(request.body, limit, signal);
      // Next can represent an absent POST/DELETE body as an empty stream.
      // Only zero bytes mean absent; nonempty bodies still require their contract.
      if (raw.length) {
        if (definition.bodyKeys === undefined) return respond({ code: "invalid_input" }, 400);
        if (request.headers.get("content-type")?.split(";")[0].trim().toLowerCase() !== "application/json") return respond({ code: "invalid_input" }, 415);
        const parsed: unknown = JSON.parse(raw);
        if (!record(parsed) || Object.keys(parsed).some((key) => !definition.bodyKeys!.includes(key))) return respond({ code: "invalid_input" }, 400);
        body = raw;
        input = parsed;
        if (path.endsWith("/messages") && request.method === "POST") {
          if (typeof input.content !== "string" || !input.content.trim() || typeof input.request_id !== "string"
            || !new RegExp(`^${uuid}$`).test(input.request_id)) return respond({ code: "invalid_input" }, 400);
        }
      } else if (definition.bodyKeys?.length) {
        return respond({ code: "invalid_input" }, 400);
      }
    } else if (definition.bodyKeys?.length) {
      return respond({ code: "invalid_input" }, 400);
    }
  } catch (error) {
    return respond({ code: "invalid_input" }, error instanceof BodyLimit ? 413 : request.signal.aborted ? 499 : timeout.aborted ? 408 : 400);
  }
  try {
    const response = await fetch(`${upstream}${path}${incoming.search}`, {
      method: request.method, body, cache: "no-store", redirect: "error", signal,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    });
    if (response.status === 204 && request.method === "DELETE") return respond(null, 204);
    const text = await boundedText(response.body, maxResponseBody, signal);
    const payload: unknown = JSON.parse(text);
    if (!response.ok) return failure(payload, response.status);
    checkResponse(payload, path, request.method, input);
    return respond(project(payload, definition.output), response.status);
  } catch {
    return respond({ code: timeout.aborted ? "model_timeout" : "upstream_error" }, request.signal.aborted ? 499 : timeout.aborted ? 504 : 502);
  }
}
