import assert from "node:assert/strict";
import { test } from "node:test";
import {
  chooseSupport,
  createFastApiAgent,
  demoAgent,
  demoSample,
  maxInputLength,
  reviseReflection,
  saveExperience,
} from "../src/lib/agent";

test("correcting a draft changes the character response, saved card and next local turn", async () => {
  const draft = await demoAgent.reflect({ text: demoSample });
  const confirmed = reviseReflection(draft, {
    facts: "我在会议结束后把想法写下来了。",
    interpretation: "我还可以找一个适合的时机表达。",
    emotion: "遗憾和期待",
    needs: "先把想法写完整",
  });

  const outcome = chooseSupport(confirmed, "next");
  const card = saveExperience(confirmed, outcome);
  assert.equal(draft.status, "proposed");
  assert.equal(card.reflectionStatus, "corrected");
  assert.equal(card.title, "我在会议结束后把想法写下来了。");
  assert.equal(card.hypotheses.needs, "先把想法写完整");
  assert.match(outcome.response, /遗憾和期待.*先把想法写完整/);
  assert.equal(saveExperience(confirmed, outcome).id, card.id);

  const nextTurn = await demoAgent.reflect({ text: "我又想到那次会议了。", previous: card });
  assert.match(nextTurn.reply, /上一次.*先把想法写完整.*小动作/);
  assert.equal(nextTurn.hypotheses.needs, "先把想法写完整");
  assert.equal(nextTurn.status, "proposed");
});

test("an outcome is usable only with the confirmed revision it was made for", async () => {
  const draft = await demoAgent.reflect({ text: "我今天有点紧张。" });
  assert.throws(() => chooseSupport(draft, "keep"), /先确认/);
  const first = reviseReflection(draft, draft.hypotheses);
  const keep = chooseSupport(first, "keep");
  const revised = reviseReflection(first, { ...first.hypotheses, emotion: "" });
  assert.throws(() => saveExperience(revised, keep), /当前版本/);

  const release = chooseSupport(revised, "release");
  const saved = saveExperience(revised, release);
  assert.match(release.response, /留白.*先停一停/);
  assert.equal(saved.hypotheses.emotion, "");
  assert.equal(saved.choice, "release");
  assert.notEqual(keep.action, release.action);
});

test("invalid or cancelled inputs do not produce a draft", async () => {
  const cancelled = AbortSignal.abort();
  await assert.rejects(demoAgent.reflect({ text: demoSample }, cancelled), { name: "AbortError" });
  await assert.rejects(demoAgent.reflect({ text: "  " }), /写下/);
  await assert.rejects(demoAgent.reflect({ text: "长".repeat(maxInputLength + 1) }), /1200/);
});

test("a FastAPI turn requires explicit consent and sends no prior card", async (context) => {
  const adapter = createFastApiAgent();
  const fetchMock = context.mock.method(globalThis, "fetch", async (_path: RequestInfo | URL, _options?: RequestInit) => {
    return Response.json({ reply: "收到这句演示输入。" });
  });
  await assert.rejects(adapter.reflect({ text: demoSample }), /确认/);
  assert.equal(fetchMock.mock.callCount(), 0);

  const previous = { choice: "release", hypotheses: { facts: "旧资料", interpretation: "", emotion: "", needs: "" } } as const;
  const reflection = await adapter.reflect({ text: demoSample, consent: true, previous });
  const [path, options] = fetchMock.mock.calls[0].arguments;
  assert.equal(path, "/api/agent");
  assert.deepEqual(JSON.parse(String(options?.body)), { text: demoSample, consent: true });
  assert.equal(reflection.reply, "收到这句演示输入。");
  assert.equal(reflection.source, "fastapi");
  assert.equal(reflection.hypothesisSource, "demo");
});

test("a relay failure does not turn into a simulated success", async (context) => {
  context.mock.method(globalThis, "fetch", async () => Response.json({ code: "model_unavailable" }, { status: 503 }));
  await assert.rejects(createFastApiAgent().reflect({ text: demoSample, consent: true }), /模型当前不可用/);
});
