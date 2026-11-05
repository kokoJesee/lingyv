import assert from "node:assert/strict";
import { test } from "node:test";
import type { HypothesisFields } from "../src/lib/agent";
import type { UnderstandingView } from "../src/lib/support-types";
import { canvasPresentationPrefix, decodeHypotheses, encodeHypotheses, projectReflection } from "../src/lib/support-presentation";

const fields = { facts: "会后写下想法。", interpretation: '可以再找机会说出「想法」。', emotion: "遗憾\n和期待", needs: "先写完整" };
function view(status: "proposed" | "confirmed" | "invalidated" = "confirmed"): UnderstandingView {
  return { confirmation_token: status === "proposed" ? "token" : null, understanding: {
    schema_version: "understanding.v0.1", understanding_id: "u_server", revision: 4, status,
    interpretation: encodeHypotheses(fields), origin: "user_report", evidence_refs: [{ kind: "user_report", source_id: "r_1", revision: 1 }],
    created_at: "2026-11-05T00:00:00Z", updated_at: "2026-11-05T00:01:00Z", confirmed_revision: status === "confirmed" ? 4 : null,
    confirmed_at: status === "confirmed" ? "2026-11-05T00:01:00Z" : null, invalidation_reason: status === "invalidated" ? "source_changed" : null,
  } };
}

test("the readable versioned canvas round-trips text, line breaks and empty fields", () => {
  const source = { ...fields, emotion: "", needs: "  写下来  " };
  const encoded = encodeHypotheses(source);
  assert.ok(encoded.startsWith(canvasPresentationPrefix));
  assert.ok(encoded.includes("会后写下想法。"));
  assert.deepEqual(decodeHypotheses(encoded), { ...source, needs: "写下来" });
  assert.deepEqual(decodeHypotheses(encodeHypotheses(fields)), fields);
});

test("unknown, duplicate, malformed and malicious envelopes remain literal rather than becoming psychology fields", () => {
  const cases = [
    "最近在旅行，不是备考。",
    '[mind_canvas.v2]\n{"facts":"text"}',
    canvasPresentationPrefix + '{"facts":',
    canvasPresentationPrefix + JSON.stringify({ ...fields, status: "confirmed" }, null, 2),
    canvasPresentationPrefix + '{"facts":"first","facts":"second","interpretation":"","emotion":"","needs":""}',
    canvasPresentationPrefix + '{"__proto__":{"confirmed":true},"facts":"text","interpretation":"","emotion":"","needs":""}',
    canvasPresentationPrefix + JSON.stringify({ facts: ["not string"], interpretation: "", emotion: "", needs: "" }, null, 2),
  ];
  for (const text of cases) assert.deepEqual(decodeHypotheses(text), { facts: text, interpretation: "", emotion: "", needs: "" });
  assert.equal(({} as Record<string, unknown>).confirmed, undefined);
});

test("the four fields obey the backend total limit without silently truncating user text", () => {
  assert.throws(() => encodeHypotheses({ facts: "", interpretation: "", emotion: "", needs: "" }), /至少/);
  assert.throws(() => encodeHypotheses({ ...fields, facts: "长".repeat(1601) }), /每栏/);
  assert.throws(() => encodeHypotheses({ facts: "长".repeat(1000), interpretation: "长".repeat(1000), emotion: "", needs: "" }), /合计/);
  assert.throws(() => encodeHypotheses({ ...fields, injected: "yes" } as HypothesisFields), /四栏/);
});

test("new chat projections use only the user's input as facts and never infer from reply text", () => {
  const result = projectReflection({ currentInput: "我说我在旅行。", reply: "你可能很焦虑，需要休息。", id: "request_1", createdAt: "2026-11-05T00:00:00Z" });
  assert.deepEqual(result?.hypotheses, { facts: "我说我在旅行。", interpretation: "", emotion: "", needs: "" });
  assert.equal(result?.status, "proposed");
  assert.equal(result?.revision, 0);
  assert.equal(result?.source, "fastapi");
});

test("saved projections preserve server identity and confirmation, and reject inactive or inconsistent versions", () => {
  const saved = view();
  const result = projectReflection({ understanding: saved, currentInput: "原话", reply: "回应" });
  assert.equal(result?.id, "u_server");
  assert.equal(result?.revision, 4);
  assert.equal(result?.status, "confirmed");
  assert.deepEqual(result?.hypotheses, fields);
  const legacy = view("proposed"); legacy.understanding.interpretation = "最近在旅行。";
  assert.deepEqual(projectReflection({ understanding: legacy, currentInput: "原话", reply: "回应" })?.hypotheses, { facts: "最近在旅行。", interpretation: "", emotion: "", needs: "" });
  assert.equal(projectReflection({ understanding: view("invalidated"), currentInput: "", reply: "" }), null);
  saved.understanding.confirmed_revision = 3;
  assert.equal(projectReflection({ understanding: saved, currentInput: "", reply: "" }), null);
});
