import { hypothesisKeys, maxHypothesisLength, type HypothesisFields, type Reflection } from "./agent";
import type { UnderstandingView } from "./support-types";

/** Presentation envelope only. It grants no confirmation, provenance or execution authority. */
export const canvasPresentationVersion = "mind_canvas.v1";
export const canvasPresentationPrefix = `[${canvasPresentationVersion}]\n`;
export const maxUnderstandingTextLength = 2000;

function checkedFields(value: unknown): HypothesisFields {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("画布内容格式无效。");
  const keys = Object.keys(value);
  if (keys.length !== hypothesisKeys.length || keys.some((key) => !hypothesisKeys.some((allowed) => allowed === key))) {
    throw new Error("画布只接受事实、解释、情绪和需要四栏。");
  }
  const fields = {} as HypothesisFields;
  for (const key of hypothesisKeys) {
    const item: unknown = (value as Record<string, unknown>)[key];
    if (typeof item !== "string") throw new Error("画布每一栏都需要文字。");
    fields[key] = item.trim();
    if (fields[key].length > maxHypothesisLength) throw new Error(`画布每栏最多 ${maxHypothesisLength} 个字符。`);
  }
  return fields;
}

/** Keep a readable, versioned representation inside the existing interpretation field. */
export function encodeHypotheses(value: HypothesisFields): string {
  const fields = checkedFields(value);
  if (!hypothesisKeys.some((key) => fields[key])) throw new Error("请至少填写一栏，再确认这份理解。");
  const encoded = canvasPresentationPrefix + JSON.stringify(fields, null, 2);
  if (encoded.length > maxUnderstandingTextLength) throw new Error("四栏合计过长，请缩短内容后再确认（含格式最多 2000 个字符）。");
  return encoded;
}

/** Unknown, damaged or noncanonical envelopes stay literal; never guess missing fields. */
export function decodeHypotheses(text: string): HypothesisFields {
  const literal: HypothesisFields = { facts: text, interpretation: "", emotion: "", needs: "" };
  if (typeof text !== "string") throw new Error("理解内容需要文字。");
  if (!text.startsWith(canvasPresentationPrefix) || text.length > maxUnderstandingTextLength) return literal;
  try {
    const value: unknown = JSON.parse(text.slice(canvasPresentationPrefix.length));
    const fields = checkedFields(value);
    // Our encoder is canonical. This also rejects duplicate keys, hidden fields,
    // alternate versions, and JSON whose last duplicate key would silently win.
    return encodeHypotheses(fields) === text ? fields : literal;
  } catch { return literal; }
}

export type ReflectionProjection = {
  understanding?: UnderstandingView | null;
  currentInput: string;
  reply: string;
  id?: string;
  createdAt?: string;
};

/** Adapt server/user data to the existing canvas UI without inferring psychology from prose. */
export function projectReflection({ understanding, currentInput, reply, id, createdAt }: ReflectionProjection): Reflection | null {
  const saved = understanding?.understanding;
  if (saved && !["proposed", "confirmed"].includes(saved.status)) return null;
  if (saved?.status === "confirmed" && saved.confirmed_revision !== saved.revision) return null;
  if (!saved && (!id || !createdAt)) throw new Error("新回应需要真实请求编号和显示时间。");
  const hypotheses: HypothesisFields = saved
    ? decodeHypotheses(saved.interpretation)
    : { facts: currentInput, interpretation: "", emotion: "", needs: "" };
  return {
    id: saved?.understanding_id ?? id!,
    revision: saved?.revision ?? 0,
    input: currentInput,
    reply,
    hypotheses,
    originalHypotheses: { ...hypotheses },
    status: saved?.status === "confirmed" ? "confirmed" : "proposed",
    source: "fastapi",
    hypothesisSource: "fastapi",
    createdAt: saved?.created_at ?? createdAt!,
  };
}
