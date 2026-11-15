import assert from "node:assert/strict";
import { test } from "node:test";
import { createSupportClient, SupportError } from "../src/lib/support-client";

const sessionId = "11111111-1111-4111-8111-111111111111";
const requestId = "22222222-2222-4222-8222-222222222222";

test("network retries preserve caller-owned request IDs and the client does not manufacture cleanup calls", async (context) => {
  const fetchMock = context.mock.method(globalThis, "fetch", async () => Response.json({ reply: "虚构回复", request_id: requestId }));
  const api = createSupportClient();
  const body = { content: "虚构输入", request_id: requestId };
  await api.sendMessage(sessionId, body);
  await api.sendMessage(sessionId, body);
  assert.equal(fetchMock.mock.callCount(), 2);
  for (const { arguments: [url, options] } of fetchMock.mock.calls) {
    assert.equal(url, `/api/support/sessions/${sessionId}/messages`);
    assert.deepEqual(JSON.parse(String(options?.body)), body);
    assert.equal(options?.credentials, "same-origin");
  }
});

test("business failures retain code, HTTP status and the independent support notice", async (context) => {
  const notice = { review_status: "unreviewed", message: "仍可联系信任的人。", actions: ["联系信任的人"], automatic_contact: false };
  context.mock.method(globalThis, "fetch", async () => Response.json({ code: "source_withdrawn", support_notice: notice }, { status: 409 }));
  await assert.rejects(createSupportClient().sendMessage(sessionId, { content: "虚构输入", request_id: requestId }), (error) => {
    assert.ok(error instanceof SupportError);
    assert.equal(error.status, 409);
    assert.equal(error.code, "source_withdrawn");
    assert.deepEqual(error.supportNotice, notice);
    return true;
  });
});

test("explicit run cancellation does not clear history; aborting a disconnected request does not target a different run", async (context) => {
  const fetchMock = context.mock.method(globalThis, "fetch", async () => Response.json({ status: "ACTIVE" }));
  const api = createSupportClient();
  await api.cancel(sessionId, requestId);
  const [url, options] = fetchMock.mock.calls[0].arguments;
  assert.equal(url, `/api/support/sessions/${sessionId}/workflow/cancel`);
  assert.deepEqual(JSON.parse(String(options?.body)), { run_id: requestId });
  await assert.rejects(api.sendMessage(sessionId, { content: "虚构输入", request_id: requestId }, AbortSignal.abort()), { name: "AbortError" });
  assert.equal(fetchMock.mock.callCount(), 1);
});

test("archive save uses explicit business-data choice and delete handles an empty successful response", async (context) => {
  const fetchMock = context.mock.method(globalThis, "fetch", async (_url: RequestInfo | URL, options?: RequestInit) => options?.method === "DELETE"
    ? new Response(null, { status: 204 }) : Response.json({ archive_id: sessionId }));
  const api = createSupportClient();
  await api.saveArchive(sessionId, { expected_revision: 4, save_business_data: true });
  assert.deepEqual(JSON.parse(String(fetchMock.mock.calls[0].arguments[1]?.body)), { expected_revision: 4, save_business_data: true });
  assert.equal(await api.deleteArchive(sessionId, 2), undefined);
  assert.equal(fetchMock.mock.calls[1].arguments[0], `/api/support/archives/${sessionId}?expected_version=2`);
});

test("client addresses cannot switch origin and object IDs cannot inject a path", () => {
  assert.throws(() => createSupportClient("https://example.com"), /本站/);
  assert.throws(() => createSupportClient().getSupportState("../../health"), /编号/);
});
