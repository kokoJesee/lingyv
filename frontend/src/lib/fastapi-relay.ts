import { cleanInput } from "./agent";

const loopbackHosts = new Set(["localhost", "127.0.0.1", "[::1]"]);
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const maximumRequestSize = 8192;
const relayTimeoutMs = 60_000;
const cleanupTimeoutMs = 2_000;

function respond(payload: { reply: string } | { code: string }, status: number): Response {
  return Response.json(payload, { status, headers: { "Cache-Control": "no-store" } });
}

function configuredOrigin(value: string | undefined): string | undefined {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    if (!loopbackHosts.has(url.hostname) || !["http:", "https:"].includes(url.protocol)) return undefined;
    if (url.username || url.password || url.pathname !== "/" || url.search || url.hash) return undefined;
    return url.origin;
  } catch {
    return undefined;
  }
}

function upstreamError(status: number): Response {
  if (status === 503) return respond({ code: "model_unavailable" }, 503);
  if (status === 504) return respond({ code: "model_timeout" }, 504);
  return respond({ code: "upstream_error" }, 502);
}

async function clearSession(sessionUrl: string): Promise<void> {
  const requestOptions = { cache: "no-store", redirect: "error" } as const;
  await fetch(`${sessionUrl}/messages`, {
    ...requestOptions,
    method: "DELETE",
    signal: AbortSignal.timeout(cleanupTimeoutMs),
  }).catch(() => undefined);
  await fetch(`${sessionUrl}/end`, {
    ...requestOptions,
    method: "POST",
    signal: AbortSignal.timeout(cleanupTimeoutMs),
  }).catch(() => undefined);
}

export async function handleFastApiReflection(request: Request): Promise<Response> {
  const origin = configuredOrigin(process.env.LOVESTONE_AGENT_API_URL);
  if (!origin) return respond({ code: "not_configured" }, 503);

  const incoming = new URL(request.url);
  // Next may normalize the request URL to localhost even when the browser used 127.0.0.1.
  const requestedHost = request.headers.get("host") ?? incoming.host;
  const localOrigin = configuredOrigin(`${incoming.protocol}//${requestedHost}`);
  const browserOrigin = request.headers.get("origin");
  if (!localOrigin || (browserOrigin && browserOrigin !== localOrigin)) {
    return respond({ code: "not_configured" }, 403);
  }

  const contentType = request.headers.get("content-type")?.split(";")[0].trim().toLowerCase();
  if (contentType !== "application/json") return respond({ code: "invalid_input" }, 415);
  if (Number(request.headers.get("content-length")) > maximumRequestSize) {
    return respond({ code: "invalid_input" }, 413);
  }

  let content: string;
  try {
    const raw = await request.text();
    if (raw.length > maximumRequestSize) return respond({ code: "invalid_input" }, 413);
    const body: { text?: unknown; consent?: unknown } | null = JSON.parse(raw);
    if (body?.consent !== true) return respond({ code: "consent_required" }, 400);
    if (typeof body.text !== "string") return respond({ code: "invalid_input" }, 400);
    content = cleanInput(body.text);
  } catch {
    return respond({ code: "invalid_input" }, 400);
  }

  const timeout = AbortSignal.timeout(relayTimeoutMs);
  const signal = AbortSignal.any([request.signal, timeout]);
  const requestOptions = { cache: "no-store", redirect: "error", signal } as const;
  let sessionUrl: string | undefined;

  try {
    const opened = await fetch(`${origin}/sessions`, { ...requestOptions, method: "POST" });
    if (!opened.ok) return upstreamError(opened.status);
    const session: { session_id?: unknown } | null = await opened.json();
    if (typeof session?.session_id !== "string" || !uuidPattern.test(session.session_id)) return upstreamError(502);
    sessionUrl = `${origin}/sessions/${session.session_id}`;

    const requestId = globalThis.crypto.randomUUID();
    const message = await fetch(`${sessionUrl}/messages`, {
      ...requestOptions,
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content, request_id: requestId }),
    });
    if (!message.ok) return upstreamError(message.status);
    const result: { reply?: unknown; session_id?: unknown; request_id?: unknown } | null = await message.json();
    if (result?.session_id !== session.session_id || result.request_id !== requestId || typeof result.reply !== "string") {
      return upstreamError(502);
    }
    const reply = result.reply.trim();
    if (!reply || reply.length > 32_000) return upstreamError(502);
    return respond({ reply }, 200);
  } catch {
    if (request.signal.aborted) return respond({ code: "upstream_error" }, 499);
    return upstreamError(timeout.aborted ? 504 : 502);
  } finally {
    if (sessionUrl) await clearSession(sessionUrl);
  }
}
