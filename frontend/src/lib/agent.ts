export const hypothesisKeys = ["facts", "interpretation", "emotion", "needs"] as const;
export type HypothesisKey = (typeof hypothesisKeys)[number];
export type HypothesisFields = Record<HypothesisKey, string>;

export const hypothesisLabels: Record<HypothesisKey, string> = {
  facts: "事实",
  interpretation: "解释",
  emotion: "情绪",
  needs: "需要",
};

export const maxInputLength = 1200;
export const maxHypothesisLength = 1600;
export const demoSample = "今天开会时，我准备好的想法没来得及说。会议结束后，我有点闷，也一直在想是不是自己不够好。";

export type SupportChoice = "keep" | "release" | "next";
export const supportChoices: Record<SupportChoice, { label: string; description: string }> = {
  keep: { label: "Keep · 留下", description: "把对自己有帮助的理解留在这里。" },
  release: { label: "Release · 放下", description: "给暂时没有答案的部分留一点空间。" },
  next: { label: "Next · 向前", description: "从自己认同的需要出发，试想一个小动作。" },
};

export type ReflectionSource = "demo" | "fastapi";

export type Reflection = {
  id: string;
  revision: number;
  input: string;
  reply: string;
  hypotheses: HypothesisFields;
  originalHypotheses: HypothesisFields;
  status: "proposed" | "confirmed" | "corrected";
  source: ReflectionSource;
  hypothesisSource: ReflectionSource;
  createdAt: string;
};

export type SupportOutcome = {
  choice: SupportChoice;
  reflectionId: string;
  reflectionRevision: number;
  response: string;
  action: string;
};

export type ExperienceCard = {
  id: string;
  title: string;
  hypotheses: HypothesisFields;
  choice: SupportChoice;
  action: string;
  createdAt: string;
  reflectionStatus: "confirmed" | "corrected";
  source: ReflectionSource;
};

export type ReflectInput = {
  text: string;
  consent?: boolean;
  previous?: Pick<ExperienceCard, "choice" | "hypotheses">;
};

export interface AgentAdapter {
  reflect(input: ReflectInput, signal?: AbortSignal): Promise<Reflection>;
}

export function cleanInput(input: string): string {
  const value = input.trim();
  if (!value) throw new Error("先写下一点想说的话。");
  if (value.length > maxInputLength) throw new Error(`请将文字缩短到 ${maxInputLength} 个字符以内。`);
  return value;
}

function demoHypotheses(input: string): HypothesisFields {
  if (input === demoSample) {
    return {
      facts: "开会时，你准备好的想法没有来得及说。",
      interpretation: "你可能把没来得及表达理解成了自己做得不够好。",
      emotion: "也许有点失落，还有一点自我怀疑。",
      needs: "也许你想被听见，或者希望有机会把想法说完整。",
    };
  }

  const possibleFeelings = ["紧张", "难过", "开心", "失落", "生气", "委屈", "疲惫", "焦虑", "轻松"];
  const mentionedFeeling = possibleFeelings.find((feeling) => input.includes(feeling));
  return {
    facts: input,
    interpretation: "我还不确定你怎么看这段经历。可以写下你当时想到的一种解释。",
    emotion: mentionedFeeling
      ? `文字里出现了「${mentionedFeeling}」。它能代表你此刻的感受吗？`
      : "我还不知道这段经历让你有什么感受。可以用自己的词描述，也可以留空。",
    needs: "也许你想先把感受说清楚。此刻你更希望得到什么？",
  };
}

function preview(value: string, length: number): string {
  const characters = Array.from(value.replace(/\s+/g, " "));
  if (characters.length <= length) return characters.join("");
  return `${characters.slice(0, length).join("")}…`;
}

function newReflection(input: string, reply: string, source: ReflectionSource, priorNeed?: string): Reflection {
  const hypotheses = demoHypotheses(input);
  if (priorNeed) hypotheses.needs = priorNeed;
  return {
    id: globalThis.crypto.randomUUID?.() ?? globalThis.crypto.getRandomValues(new Uint32Array(4)).join("-"),
    revision: 0,
    input,
    reply,
    hypotheses,
    originalHypotheses: { ...hypotheses },
    status: "proposed",
    source,
    hypothesisSource: "demo",
    createdAt: new Date().toISOString(),
  };
}

const followUp: Record<SupportChoice, string> = {
  keep: "我们可以先看看，这次有什么值得留在心里。",
  release: "这次也可以先聊聊，不急着找到答案。",
  next: "如果你还想试一个小动作，我们可以等你说完再一起想。",
};

export const demoAgent: AgentAdapter = {
  async reflect({ text, previous }, signal) {
    signal?.throwIfAborted();
    const input = cleanInput(text);
    const opening = input === demoSample
      ? "想法没能说出口，那份闷可能还留在身上。我们可以一起看看，哪些是发生的事，哪些是你后来对自己的解释。"
      : "我听见了。我们可以慢一点，把发生的事和心里浮起来的感受分别放在画布上。";
    if (!previous) return newReflection(input, opening, "demo");

    const priorNeed = previous.hypotheses.needs;
    const previousContext = priorNeed ? `上一次，你确认的需要是「${preview(priorNeed, 120)}」。那份需要今天还适用吗？` : "";
    return newReflection(input, `${opening}${previousContext}${followUp[previous.choice]}`, "demo", priorNeed);
  },
};

const relayErrors: Record<string, string> = {
  not_configured: "本地 Agent 接口未启用。可以切回离线演示。",
  consent_required: "请先确认仅发送虚构示例，再尝试连接本地 Agent。",
  model_unavailable: "本地 Agent 未配置模型或模型当前不可用。可以切回离线演示。",
  model_timeout: "本地 Agent 响应超时。请稍后重试或切回离线演示。",
  upstream_error: "本地 Agent 未能完成回复。请检查后端状态或切回离线演示。",
};

export function createFastApiAgent(baseUrl = "/api/agent"): AgentAdapter {
  if (!/^\/[a-z0-9/_-]*$/i.test(baseUrl) || baseUrl.startsWith("//")) {
    throw new Error("Agent 中转地址必须为本站的相对路径。");
  }

  return {
    async reflect({ text, consent }, signal) {
      signal?.throwIfAborted();
      const input = cleanInput(text);
      if (consent !== true) throw new Error(relayErrors.consent_required);

      let response: Response;
      try {
        response = await fetch(baseUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: input, consent: true }),
          cache: "no-store",
          credentials: "same-origin",
          redirect: "error",
          signal,
        });
      } catch {
        signal?.throwIfAborted();
        throw new Error(relayErrors.upstream_error);
      }

      const result: { reply?: unknown; code?: unknown } | null = await response.json().catch(() => null);
      signal?.throwIfAborted();
      if (!response.ok || typeof result?.reply !== "string" || !result.reply.trim()) {
        const code = typeof result?.code === "string" && Object.hasOwn(relayErrors, result.code)
          ? result.code
          : "upstream_error";
        throw new Error(relayErrors[code]);
      }

      return newReflection(input, result.reply.trim(), "fastapi");
    },
  };
}

export function reviseReflection(reflection: Reflection, fields: HypothesisFields): Reflection {
  const hypotheses = { ...fields };
  for (const key of hypothesisKeys) {
    hypotheses[key] = fields[key].trim();
    if (hypotheses[key].length > maxHypothesisLength) {
      throw new Error(`${hypothesisLabels[key]}最多可填写 ${maxHypothesisLength} 个字符。`);
    }
  }

  const changed = hypothesisKeys.some((key) => hypotheses[key] !== reflection.originalHypotheses[key]);
  return {
    ...reflection,
    revision: reflection.revision + 1,
    hypotheses,
    status: changed ? "corrected" : "confirmed",
  };
}

export function chooseSupport(reflection: Reflection, choice: SupportChoice): SupportOutcome {
  if (reflection.status === "proposed") throw new Error("请先确认当前画布，再选择下一步。");

  const { emotion, needs } = reflection.hypotheses;
  const heard = emotion ? `你把此刻的感受写成「${emotion}」。` : "感受可以暂时留白。";
  const need = needs ? `「${needs}」` : "你想照顾的那部分自己";
  const outcome = {
    keep: {
      response: `${heard}我会把${need}留在故事里，作为你选择记住的一点。`,
      action: "给自己留一句话，下次遇到相似的时刻可以再看看。",
    },
    release: {
      response: `${heard}关于${need}，现在也可以先停一停。我们把这一页轻轻合上。`,
      action: "允许自己今天先到这里，把注意力放回当下。",
    },
    next: {
      response: `${heard}如果你愿意，我们从${need}出发，找一个你觉得做得到的小动作。`,
      action: "想一个两分钟以内的小动作。现在尝试，或留到准备好时再做。",
    },
  }[choice];

  return { ...outcome, choice, reflectionId: reflection.id, reflectionRevision: reflection.revision };
}

export function saveExperience(reflection: Reflection, outcome: SupportOutcome): ExperienceCard {
  if (reflection.status === "proposed" || outcome.reflectionId !== reflection.id || outcome.reflectionRevision !== reflection.revision) {
    throw new Error("画布已有变化，请用当前版本重新选择后再保存。");
  }

  return {
    id: reflection.id,
    title: preview(reflection.hypotheses.facts, 24) || "一段留给自己的经历",
    hypotheses: { ...reflection.hypotheses },
    choice: outcome.choice,
    action: outcome.action,
    createdAt: new Date().toISOString(),
    reflectionStatus: reflection.status,
    source: reflection.source,
  };
}
