import type { ExperienceCard } from "../lib/agent";

const examples: Pick<ExperienceCard, "id" | "title" | "choice" | "hypotheses" | "action">[] = [
  {
    id: "example-meeting",
    title: "第一次在会上发言",
    choice: "keep",
    hypotheses: { facts: "我在会上说出了自己的想法。", interpretation: "紧张也可以把想法说清楚。", emotion: "紧张，有一点自豪。", needs: "给自己一点肯定。" },
    action: "写下一句想留给自己的话。",
  },
  {
    id: "example-walk",
    title: "傍晚沿河散步",
    choice: "keep",
    hypotheses: { facts: "晚饭后沿河走了一小段。", interpretation: "给自己留一点安静的时间很有帮助。", emotion: "放松、踏实。", needs: "记住这种从容的节奏。" },
    action: "记下散步时最想留住的一个瞬间。",
  },
  {
    id: "example-rain",
    title: "雨天改了计划",
    choice: "release",
    hypotheses: { facts: "下雨了，我取消了原本的出门计划。", interpretation: "临时改计划也可以过得轻松一些。", emotion: "有点失落，后来松了一口气。", needs: "给自己留一点空白。" },
    action: "把暂时不用决定的事留到明天。",
  },
  {
    id: "example-list",
    title: "放下没做完的清单",
    choice: "release",
    hypotheses: { facts: "睡前，清单上还有两件事没做。", interpretation: "剩下的可以明天再决定。", emotion: "着急，也有点累。", needs: "给这一天留一个结束的时刻。" },
    action: "合上清单，给今天画个句号。",
  },
  {
    id: "example-music",
    title: "想学一首新曲子",
    choice: "next",
    hypotheses: { facts: "我找到了一首想学的曲子。", interpretation: "会弹几个小节就算有进展。", emotion: "期待，也有点忐忑。", needs: "找到一个容易开始的动作。" },
    action: "挑一个小节，慢慢试弹两遍。",
  },
  {
    id: "example-desk",
    title: "整理窗边的书桌",
    choice: "next",
    hypotheses: { facts: "窗边的书桌堆了几本还没收好的书。", interpretation: "只整理手边的一小块，也能看到变化。", emotion: "有点期待。", needs: "从五分钟开始。" },
    action: "先把最靠近自己的两本书放回原处。",
  },
];

export const exampleExperienceCards: readonly ExperienceCard[] = examples.map((example) => ({
  ...example,
  createdAt: "2026-01-01T00:00:00.000Z",
  reflectionStatus: "confirmed",
  source: "demo",
}));
