import assert from "node:assert/strict";
import { test, type TestContext } from "node:test";
import { handleSupportRequest } from "../src/lib/support-relay";

const sessionId = "11111111-1111-4111-8111-111111111111";
const requestId = "22222222-2222-4222-8222-222222222222";
const runId = "33333333-3333-4333-8333-333333333333";
const origin = "http://127.0.0.1:8000";
const workflow = { run_id: runId, phase: "COMPLETED", step_count: 4, stop_reason: "completed" };
const supportNotice = { review_status: "unreviewed", message: "本次支持未完成，仍可联系信任的人。", actions: ["联系信任的人"], automatic_contact: false };

function configure(context: TestContext) {
  const previous = process.env.LOVESTONE_AGENT_API_URL;
  process.env.LOVESTONE_AGENT_API_URL = origin;
  context.after(() => {
    if (previous === undefined) delete process.env.LOVESTONE_AGENT_API_URL;
    else process.env.LOVESTONE_AGENT_API_URL = previous;
  });
}
function request(path: string, method = "GET", body?: unknown, headers: Record<string, string> = {}) {
  return new Request(`http://localhost:3000/api/support${path}`, {
    method,
    headers: { host: "127.0.0.1:3000", origin: "http://127.0.0.1:3000", ...(body === undefined ? {} : { "content-type": "application/json" }), ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}
function messageReply(route = "routine_support") {
  return {
    session_id: sessionId, request_id: requestId, run_id: runId, revision: 4, reply: "已采用当前理解。", workflow,
    review_status: "unreviewed", diagnostic_secret: "never-forward",
    psychology: {
      schema_version: "0.1.0", session_id: sessionId, request_id: requestId, session_revision: 3,
      result: { route, evidence_refs: ["current_message"], uncertainties: [], reply: "已采用当前理解。", suggested_next_step: null, memory_update: null, follow_up_offer: null, debug: "hidden" },
    },
  };
}

test("persistent messages preserve business results and request IDs without creating or clearing sessions", async (context) => {
  configure(context);
  const fetchMock = context.mock.method(globalThis, "fetch", async (url: RequestInfo | URL, options?: RequestInit) => {
    assert.equal(String(url), `${origin}/sessions/${sessionId}/messages`);
    assert.deepEqual(JSON.parse(String(options?.body)), {
      content: "其实最近是旅行。", request_id: requestId,
      correction_of: { understanding_id: "u_123", expected_revision: 2 },
    });
    return Response.json(messageReply("clarify_safety"));
  });
  const result = await handleSupportRequest(request(`/sessions/${sessionId}/messages`, "POST", {
    content: "其实最近是旅行。", request_id: requestId,
    correction_of: { understanding_id: "u_123", expected_revision: 2 },
  }));
  assert.equal(result.status, 200);
  const body = await result.json();
  assert.equal(body.psychology.result.route, "clarify_safety");
  assert.equal(body.review_status, "unreviewed");
  assert.equal(body.workflow.run_id, runId);
  assert.equal(body.diagnostic_secret, undefined);
  assert.equal(body.psychology.result.debug, undefined);
  assert.equal(fetchMock.mock.callCount(), 1);
  assert.equal(result.headers.get("cache-control"), "no-store");
});

test("the BFF rejects remote origins, arbitrary routes, unsupported verbs and query injection", async (context) => {
  configure(context);
  const fetchMock = context.mock.method(globalThis, "fetch", async () => Response.error());
  const cases = [
    request("/health", "GET", undefined, { origin: "https://example.com" }),
    request("/health", "GET", undefined, { host: "example.com" }),
    request("/health", "GET", undefined, { "sec-fetch-site": "cross-site" }),
    request("/runtime/calls"),
    request(`/sessions/${sessionId}/workflow/demo`, "POST"),
    request(`/sessions/${sessionId}/messages?target=http://example.com`, "DELETE"),
    request(`/sessions/${sessionId}/wearables/trends?days=7&days=14`),
    request(`/sessions/${sessionId}/understandings/%2Fsecret`),
  ];
  for (const input of cases) assert.ok((await handleSupportRequest(input)).status >= 400);
  process.env.LOVESTONE_AGENT_API_URL = "https://example.com";
  assert.equal((await handleSupportRequest(request("/health"))).status, 503);
  assert.equal(fetchMock.mock.callCount(), 0);
});

test("controlled errors retain status, code and support notice while debug and validation inputs are stripped", async (context) => {
  configure(context);
  context.mock.method(globalThis, "fetch", async () => Response.json({
    detail: { code: "source_withdrawn", message: "private diagnostic", traceback: "secret" },
    support_notice: { ...supportNotice, provider_key: "secret" },
  }, { status: 409 }));
  const result = await handleSupportRequest(request(`/sessions/${sessionId}/messages`, "POST", { content: "虚构输入", request_id: requestId }));
  assert.equal(result.status, 409);
  assert.deepEqual(await result.json(), { code: "source_withdrawn", support_notice: supportNotice });
});

test("large UTF-8 or streamed bodies are bounded before forwarding, including without Content-Length", async (context) => {
  configure(context);
  const fetchMock = context.mock.method(globalThis, "fetch", async () => Response.error());
  const oversized = request(`/sessions/${sessionId}/messages`, "POST", { content: "字".repeat(3000), request_id: requestId });
  assert.equal((await handleSupportRequest(oversized)).status, 413);
  const encoder = new TextEncoder();
  let cancelled = false;
  const stream = new ReadableStream<Uint8Array>({
    pull(controller) { controller.enqueue(encoder.encode(" ".repeat(9000))); },
    cancel() { cancelled = true; },
  });
  const streamed = new Request(`http://127.0.0.1:3000/api/support/sessions/${sessionId}/wearables/consent`, {
    method: "POST", headers: { "content-type": "application/json", host: "127.0.0.1:3000" },
    body: stream, duplex: "half",
  } as RequestInit & { duplex: "half" });
  assert.equal((await handleSupportRequest(streamed)).status, 413);
  assert.equal(cancelled, true);
  assert.equal(fetchMock.mock.callCount(), 0);
});

test("wearable import gets the separate 4 MiB budget but arbitrary extra request keys are rejected", async (context) => {
  configure(context);
  const fetchMock = context.mock.method(globalThis, "fetch", async () => Response.json({ accepted: 1, revision: 1 }));
  const batch = { description: "x".repeat(20_000) };
  assert.equal((await handleSupportRequest(request(`/sessions/${sessionId}/wearables/import`, "POST", {
    expected_revision: 0, operation_id: requestId, batch,
  }))).status, 200);
  assert.equal((await handleSupportRequest(request(`/sessions/${sessionId}/wearables/consent`, "POST", {
    expected_revision: 1, operation_id: requestId, provider: "demo", allowed_metrics: [], scopes: [], api_key: "injected",
  }))).status, 400);
  assert.equal(fetchMock.mock.callCount(), 1);
});

test("clear and cancel remain separate explicit requests and successful archive deletion accepts 204", async (context) => {
  configure(context);
  const fetchMock = context.mock.method(globalThis, "fetch", async (url: RequestInfo | URL) => {
    return String(url).includes("/archives/") ? new Response(null, { status: 204 })
      : Response.json({ session_id: sessionId, messages: [], workflow });
  });
  await handleSupportRequest(request(`/sessions/${sessionId}/workflow/cancel`, "POST", { run_id: runId }));
  await handleSupportRequest(request(`/sessions/${sessionId}/messages`, "DELETE"));
  const deleted = await handleSupportRequest(request(`/archives/${sessionId}?expected_version=3`, "DELETE"));
  assert.equal(deleted.status, 204);
  assert.deepEqual(fetchMock.mock.calls.map(({ arguments: [url, options] }) => [String(url), options?.method]), [
    [`${origin}/sessions/${sessionId}/workflow/cancel`, "POST"],
    [`${origin}/sessions/${sessionId}/messages`, "DELETE"],
    [`${origin}/archives/${sessionId}?expected_version=3`, "DELETE"],
  ]);
});

test("cross-session responses, unsupported psychology routes and invalid payloads cannot become successes", async (context) => {
  configure(context);
  const payloads = [
    { ...messageReply(), session_id: requestId },
    messageReply("pretend_safe"),
    { ...messageReply(), request_id: runId },
    { reply: "missing identity" },
  ];
  context.mock.method(globalThis, "fetch", async () => Response.json(payloads.shift()));
  for (let index = 0; index < 4; index += 1) {
    const result = await handleSupportRequest(request(`/sessions/${sessionId}/messages`, "POST", { content: "虚构输入", request_id: requestId }));
    assert.equal(result.status, 502);
  }
});

test("trend responses retain sampling and scale metadata instead of flattening different measurements", async (context) => {
  configure(context);
  const body = {
    session_id: sessionId, revision: 2, scope: "local_analysis", as_of: "2026-11-05T00:00:00Z", timezone: "Asia/Shanghai", days: 7,
    series: [{
      metric_id: "vendor.readiness_score", unit: "score", value_type: "number", status: "ready",
      points: [{ date: "2026-11-04", value: 63, quality: "valid", record_id: "daily_1" }],
      source: {
        provider: "mock", device_id: "mock_device", measurement_method: "vendor", source_algorithm_version: "v1", is_simulated: true,
        aggregation_method: "daily", window_seconds: null, baseline_ref: null, measurement_site: null, namespace: "mock.readiness",
        score_scale: { minimum: 0, maximum: 100, direction: "higher_is_more" },
      },
    }],
  };
  context.mock.method(globalThis, "fetch", async () => Response.json(body));
  const response = await handleSupportRequest(request(`/sessions/${sessionId}/wearables/trends?as_of=2026-11-05T00%3A00%3A00Z&days=7`));
  assert.deepEqual(await response.json(), body);
});

test("Next-style empty request streams permit bodyless operations but do not discard nonempty content", async (context) => {
  configure(context);
  const fetchMock = context.mock.method(globalThis, "fetch", async () => Response.json({ session_id: sessionId, messages: [], workflow }));
  function streamed(path: string, method: string, content = "", contentType?: string) {
    const stream = new ReadableStream<Uint8Array>({ start(controller) {
      if (content) controller.enqueue(new TextEncoder().encode(content));
      controller.close();
    } });
    return new Request(`http://127.0.0.1:3000/api/support${path}`, {
      method, headers: { host: "127.0.0.1:3000", ...(contentType ? { "content-type": contentType } : {}) },
      body: stream, duplex: "half",
    } as RequestInit & { duplex: "half" });
  }
  for (const [path, method] of [["/sessions", "POST"], [`/sessions/${sessionId}/end`, "POST"], [`/sessions/${sessionId}/messages`, "DELETE"], [`/archives/${sessionId}/restore`, "POST"]]) {
    const input = streamed(path, method);
    assert.notEqual(input.body, null);
    assert.equal((await handleSupportRequest(input)).status, 200);
  }
  assert.equal(fetchMock.mock.callCount(), 4);
  assert.ok(fetchMock.mock.calls.every(({ arguments: [, options] }) => options?.body === undefined));
  assert.equal((await handleSupportRequest(streamed("/sessions", "POST", "not JSON", "text/plain"))).status, 415);
  assert.equal((await handleSupportRequest(streamed("/sessions", "POST", '{"unexpected":"body"}', "application/json"))).status, 400);
  assert.equal((await handleSupportRequest(streamed(`/sessions/${sessionId}/messages`, "DELETE", "discard me"))).status, 400);
  assert.equal((await handleSupportRequest(streamed(`/sessions/${sessionId}/messages`, "POST"))).status, 400);
  assert.equal(fetchMock.mock.callCount(), 4);
});
