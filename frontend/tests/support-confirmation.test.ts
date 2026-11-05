import assert from "node:assert/strict";
import { test } from "node:test";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { commitUnderstandingVersion, readActiveSignals, useSupportJourney, type JourneyController } from "../src/components/use-support-journey";
import type { SupportClient } from "../src/lib/support-client";
import type { StateAnalysis, SupportState, UnderstandingView, WearableTrends } from "../src/lib/support-types";

const sid = "11111111-1111-4111-8111-111111111111";
function understanding(revision: number, status: "proposed" | "confirmed", text = "当前四栏文字"): UnderstandingView {
  return { confirmation_token: status === "proposed" ? "22222222-2222-4222-8222-222222222222" : null, understanding: {
    schema_version: "understanding.v0.1", understanding_id: "u_1", revision, status, interpretation: text,
    origin: "user_report", evidence_refs: [{ kind: "user_report", source_id: "r_new", revision: 1 }],
    created_at: "2026-11-05T00:00:00Z", updated_at: "2026-11-05T00:01:00Z",
    confirmed_revision: status === "confirmed" ? revision : null, confirmed_at: status === "confirmed" ? "2026-11-05T00:01:00Z" : null, invalidation_reason: null,
  } };
}

test("one explicit confirmation creates the report then confirms exactly the returned proposal version", async () => {
  const calls: string[] = [];
  const operations = new Set<string>();
  const client: Pick<SupportClient, "addReport" | "proposeUnderstanding" | "edit" | "confirm"> = {
    async addReport(session, body) { calls.push("report"); operations.add(body.operation_id); assert.equal(session, sid); return { report_id: "r_new", content: body.content, revision: 1 }; },
    async proposeUnderstanding(_session, body) { calls.push("propose"); operations.add(body.operation_id); assert.equal(body.origin, "user_report"); assert.deepEqual(body.evidence_refs, [{ kind: "user_report", source_id: "r_new", revision: 1 }]); return understanding(1, "proposed"); },
    async edit() { throw new Error("unexpected edit"); },
    async confirm(_session, id, body) { calls.push("confirm"); operations.add(body.operation_id); assert.equal(id, "u_1"); assert.equal(body.expected_revision, 1); assert.equal(body.confirmation_token, understanding(1, "proposed").confirmation_token); return understanding(2, "confirmed"); },
  };
  assert.equal((await commitUnderstandingVersion(client, sid, "当前四栏文字")).understanding.status, "confirmed");
  assert.deepEqual(calls, ["report", "propose", "confirm"]);
  assert.equal(operations.size, 3);
});

test("editing preserves the expected old version but uses fresh report evidence; conflicts never confirm", async () => {
  const existing = understanding(4, "confirmed", "旧文字");
  let confirmed = 0;
  const client: Pick<SupportClient, "addReport" | "proposeUnderstanding" | "edit" | "confirm"> = {
    async addReport(_session, body) { return { report_id: "r_new", content: body.content, revision: 1 }; },
    async proposeUnderstanding() { throw new Error("unexpected proposal"); },
    async edit(_session, id, body) { assert.equal(id, "u_1"); assert.equal(body.expected_revision, 4); assert.deepEqual(body.evidence_refs, [{ kind: "user_report", source_id: "r_new", revision: 1 }]); throw new Error("stale_revision"); },
    async confirm() { confirmed++; return understanding(6, "confirmed"); },
  };
  await assert.rejects(commitUnderstandingVersion(client, sid, "当前四栏文字", existing), /stale_revision/);
  assert.equal(confirmed, 0);
  client.edit = async () => understanding(5, "proposed");
  assert.equal((await commitUnderstandingVersion(client, sid, "当前四栏文字", existing)).understanding.revision, 6);
});

test("missing credentials, mismatched content and invalid confirmation responses cannot produce confirmed success", async () => {
  let confirms = 0;
  const proposed = understanding(1, "proposed");
  const client: Pick<SupportClient, "addReport" | "proposeUnderstanding" | "edit" | "confirm"> = {
    async addReport(_session, body) { return { report_id: "r_new", content: body.content, revision: 1 }; },
    async proposeUnderstanding() { return proposed; },
    async edit() { throw new Error("unexpected edit"); },
    async confirm() { confirms++; return understanding(2, "proposed"); },
  };
  proposed.confirmation_token = null;
  await assert.rejects(commitUnderstandingVersion(client, sid, "当前四栏文字"), /不一致/);
  assert.equal(confirms, 0);
  proposed.confirmation_token = "token";
  proposed.understanding.interpretation = "另一份内容";
  await assert.rejects(commitUnderstandingVersion(client, sid, "当前四栏文字"), /不一致/);
  assert.equal(confirms, 0);
  proposed.understanding.interpretation = "当前四栏文字";
  await assert.rejects(commitUnderstandingVersion(client, sid, "当前四栏文字"), /有效确认/);
  assert.equal(confirms, 1);
});

test("ensureSession shares concurrent creation, checks the support switch, and reuses its active session", async (context) => {
  let controller!: JourneyController;
  renderToString(createElement(function Harness() { controller = useSupportJourney(false); return null; }));
  let creates = 0, healthCalls = 0;
  const session = { session_id: sid, revision: 0, status: "ACTIVE", messages: [], workflow: { phase: "IDLE" } };
  let missing = false, unavailable = false;
  context.mock.method(globalThis, "fetch", async (url: RequestInfo | URL) => {
    const path = String(url);
    if (path.endsWith("/health")) { healthCalls++; return Response.json({ runtime: { psychology_enabled: true } }); }
    if (path.endsWith("/sessions")) { creates++; missing = false; return Response.json(session); }
    if (unavailable) return Response.json({ code: "upstream_error" }, { status: 502 });
    if (missing) return Response.json({ code: "session_not_found" }, { status: 404 });
    return Response.json({ session, support_mode: false, support_route: null, understandings: [], wearables: { revision: 0, record_count: 0, providers: [] } });
  });
  const first = controller.ensureSession(), second = controller.ensureSession();
  assert.equal(first, second);
  assert.equal(await first, sid);
  assert.equal(await controller.ensureSession(), sid);
  assert.equal(creates, 1);
  assert.equal(healthCalls, 2);
  unavailable = true;
  assert.equal(await controller.ensureSession(), undefined);
  assert.equal(creates, 1);
  unavailable = false; missing = true;
  assert.equal(await controller.ensureSession(), sid);
  assert.equal(creates, 2);
});

test("a committed clear remains successful when its follow-up read fails, while the session identity is retained", async (context) => {
  let controller!: JourneyController;
  renderToString(createElement(function Harness() { controller = useSupportJourney(false); return null; }));
  let cleared = false, creates = 0;
  const session = { session_id: sid, revision: 0, status: "ACTIVE", messages: [], workflow: { phase: "IDLE" } };
  context.mock.method(globalThis, "fetch", async (url: RequestInfo | URL, options?: RequestInit) => {
    const path = String(url);
    if (path.endsWith("/health")) return Response.json({ runtime: { psychology_enabled: true } });
    if (path.endsWith("/sessions")) { creates++; return Response.json(session); }
    if (path.endsWith("/messages") && options?.method === "DELETE") { cleared = true; return Response.json(session); }
    if (cleared) return Response.json({ code: "upstream_error" }, { status: 502 });
    return Response.json({ session, support_mode: false, support_route: null, understandings: [], wearables: { revision: 0, record_count: 0, providers: [] } });
  });
  assert.equal(await controller.ensureSession(), sid);
  assert.equal(await controller.clear(), true);
  assert.equal(await controller.ensureSession(), undefined);
  assert.equal(creates, 1);
});

test("ended journeys stop signal requests and active reads require matching session and data revisions", async () => {
  let calls = 0;
  const state = { session: { session_id: sid, status: "ENDED" }, wearables: { revision: 3 } } as SupportState;
  const trends = { session_id: sid, revision: 3 } as WearableTrends;
  const analysis = { session_id: sid, evidence_revision: 3 } as StateAnalysis;
  const client = {
    async trends() { calls++; return trends; },
    async analysis() { calls++; return analysis; },
  };
  assert.equal(await readActiveSignals(client, state, "2026-11-05T00:00:00Z", 14), null);
  assert.equal(calls, 0);
  state.session.status = "ACTIVE";
  assert.deepEqual(await readActiveSignals(client, state, "2026-11-05T00:00:00Z", 14), { trends, analysis });
  analysis.evidence_revision = 2;
  assert.equal(await readActiveSignals(client, state, "2026-11-05T00:00:00Z", 14), null);
});
