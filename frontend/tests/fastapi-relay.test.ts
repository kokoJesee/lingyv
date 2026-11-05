import assert from "node:assert/strict";
import { test, type TestContext } from "node:test";
import { handleFastApiReflection } from "../src/lib/fastapi-relay";

const sessionId = "11111111-1111-4111-8111-111111111111";
const localUpstream = "http://127.0.0.1:8000";

function configure(context: TestContext, origin: string) {
  const previous = process.env.LOVESTONE_AGENT_API_URL;
  process.env.LOVESTONE_AGENT_API_URL = origin;
  context.after(() => {
    if (previous === undefined) delete process.env.LOVESTONE_AGENT_API_URL;
    else process.env.LOVESTONE_AGENT_API_URL = previous;
  });
}

function request(consent = true, origin = "http://127.0.0.1:3000") {
  return new Request("http://localhost:3000/api/agent", {
    method: "POST",
    headers: { Host: "127.0.0.1:3000", Origin: origin, "Content-Type": "application/json" },
    body: JSON.stringify({ text: "  一次虚构的经历。  ", consent }),
  });
}

test("the relay keeps real calls behind local origin configuration and explicit consent", async (context) => {
  configure(context, localUpstream);
  const fetchMock = context.mock.method(globalThis, "fetch", async () => Response.error());
  const rejected = await handleFastApiReflection(request(false));
  assert.equal(rejected.status, 400);
  assert.deepEqual(await rejected.json(), { code: "consent_required" });

  const crossOrigin = await handleFastApiReflection(request(true, "https://example.com"));
  assert.equal(crossOrigin.status, 403);

  process.env.LOVESTONE_AGENT_API_URL = "https://example.com";
  const notConfigured = await handleFastApiReflection(request());
  assert.equal(notConfigured.status, 503);
  assert.equal(fetchMock.mock.callCount(), 0);
});

test("the relay sends the supported contract and clears ephemeral content on success", async (context) => {
  configure(context, localUpstream);
  const fetchMock = context.mock.method(globalThis, "fetch", async (url: RequestInfo | URL, options?: RequestInit) => {
    const target = String(url);
    if (target === `${localUpstream}/sessions`) return Response.json({ session_id: sessionId }, { status: 201 });
    if (options?.method === "POST" && target.endsWith("/messages")) {
      const body = JSON.parse(String(options.body));
      assert.equal(body.content, "一次虚构的经历。");
      assert.match(body.request_id, /^[0-9a-f-]{36}$/);
      return Response.json({ session_id: sessionId, request_id: body.request_id, reply: "  已收到。  " });
    }
    return Response.json({ status: "ok" });
  });

  const result = await handleFastApiReflection(request());
  assert.equal(result.status, 200);
  assert.deepEqual(await result.json(), { reply: "已收到。" });
  assert.deepEqual(fetchMock.mock.calls.map(({ arguments: [url, options] }) => [url, options?.method]), [
    [`${localUpstream}/sessions`, "POST"],
    [`${localUpstream}/sessions/${sessionId}/messages`, "POST"],
    [`${localUpstream}/sessions/${sessionId}/messages`, "DELETE"],
    [`${localUpstream}/sessions/${sessionId}/end`, "POST"],
  ]);
});

test("the relay redacts upstream errors and still attempts cleanup", async (context) => {
  configure(context, localUpstream);
  const fetchMock = context.mock.method(globalThis, "fetch", async (url: RequestInfo | URL, options?: RequestInit) => {
    if (String(url).endsWith("/sessions")) return Response.json({ session_id: sessionId }, { status: 201 });
    if (options?.method === "POST" && String(url).endsWith("/messages")) {
      return Response.json({ detail: "upstream diagnostic body" }, { status: 503 });
    }
    return Response.json({ status: "ok" });
  });

  const result = await handleFastApiReflection(request());
  assert.equal(result.status, 503);
  assert.deepEqual(await result.json(), { code: "model_unavailable" });
  assert.equal(fetchMock.mock.callCount(), 4);
});
