import { ArrowUpRight, ChevronLeft, ChevronRight, Footprints, Heart, Wind } from "lucide-react";
import { useRef, useState, type CSSProperties } from "react";
import { hypothesisKeys, hypothesisLabels, reflectionStatusLabels, supportChoices, type ExperienceCard, type SupportChoice } from "../lib/agent";

type Point = readonly [number, number];
type ArchiveFilter = "all" | SupportChoice;

const center: Point = [50, 59];
const branches = [
  {
    choice: "keep", label: "留下", color: "#aee5ff", icon: Heart,
    hub: [25, 53], points: [[12, 32], [10, 70], [29, 25], [29, 80]],
  },
  {
    choice: "release", label: "放下", color: "#d6bfff", icon: Wind,
    hub: [50, 31], points: [[42, 12], [60, 11], [39, 43], [62, 41]],
  },
  {
    choice: "next", label: "向前", color: "#ffdda4", icon: Footprints,
    hub: [75, 53], points: [[88, 32], [90, 70], [71, 25], [71, 80]],
  },
] as const;

const starsPerBranch = branches[0].points.length;
const archiveDate = new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "long", day: "numeric" });

function starPosition([x, y]: Point, color?: string): CSSProperties {
  return {
    left: `${x}%`,
    top: `${y}%`,
    "--cr-starlight": color,
    "--cr-twinkle-delay": `-${x * 31 + y * 17}ms`,
  } as CSSProperties;
}

export function ExperienceConstellation({ cards, filter, onFilterChange, showingExamples }: {
  cards: readonly ExperienceCard[];
  filter: ArchiveFilter;
  onFilterChange: (filter: ArchiveFilter) => void;
  showingExamples: boolean;
}) {
  const [selection, setSelection] = useState<{ filter: ArchiveFilter; page: number; cardId: string | null }>({ filter, page: 0, cardId: null });
  if (selection.filter !== filter) {
    setSelection({ filter, page: 0, cardId: null });
  }

  const detail = useRef<HTMLElement>(null);
  const groups = branches.map((branch) => ({ ...branch, cards: cards.filter((card) => card.choice === branch.choice) }));
  const pageCount = Math.max(1, ...groups.map((group) => Math.ceil(group.cards.length / starsPerBranch)));
  const currentPage = Math.min(selection.page, pageCount - 1);
  const start = currentPage * starsPerBranch;
  const visibleGroups = groups.map((group) => ({
    ...group,
    stars: group.cards.slice(start, start + starsPerBranch).map((card, index) => ({ card, point: group.points[index] })),
  }));
  const selected = visibleGroups.flatMap((group) => group.stars).find(({ card }) => card.id === selection.cardId)?.card;
  const CardIcon = selected && branches.find((branch) => branch.choice === selected.choice)?.icon;

  function changePage(nextPage: number) {
    setSelection({ filter, page: nextPage, cardId: null });
  }

  function selectCard(card: ExperienceCard) {
    const nextId = selected?.id === card.id ? null : card.id;
    setSelection({ filter, page: currentPage, cardId: nextId });
    if (nextId) requestAnimationFrame(() => {
      const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      detail.current?.scrollIntoView({ block: "nearest", behavior: reduced ? "instant" : "smooth" });
    });
  }

  return (
    <div className="cr-experience-map">
      <div className="cr-starfield cr-archive-constellation" role="group" aria-label="经历星图">
        {cards.length > 0 && <p className="cr-map-hint">点亮一颗星，查看那段经历</p>}
        <svg className="cr-star-lines" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
          {visibleGroups.map(({ choice, hub, stars, color }) => (
            <g key={choice} stroke={color}>
              <line className="cr-branch-line" data-active={filter === "all" || filter === choice} x1={center[0]} y1={center[1]} x2={hub[0]} y2={hub[1]} vectorEffect="non-scaling-stroke" />
              {stars.map(({ card, point }) => (
                <line key={card.id} className="cr-experience-link" data-selected={card.id === selected?.id}
                  x1={hub[0]} y1={hub[1]} x2={point[0]} y2={point[1]} vectorEffect="non-scaling-stroke" />
              ))}
            </g>
          ))}
        </svg>
        <button type="button" className="cr-star-node cr-map-center" style={starPosition(center)}
          aria-label="显示全部经历" title="显示全部经历" aria-pressed={filter === "all"} onClick={() => onFilterChange("all")}>
          <span className="cr-star-mark" aria-hidden="true" />
        </button>
        {visibleGroups.map(({ choice, label, color, hub, stars }) => (
          <div key={choice}>
            <button type="button" className="cr-star-node cr-branch-hub" style={starPosition(hub, color)}
              aria-label={`筛选：${label}`} aria-pressed={filter === choice} onClick={() => onFilterChange(choice)}>
              <span className="cr-star-mark" aria-hidden="true" /><span className="cr-star-label">{label}</span>
            </button>
            {stars.map(({ card, point }) => (
              <button type="button" key={card.id} className="cr-star-node cr-experience-star"
                title={card.title} aria-label={`${card.title} · ${supportChoices[choice].label}`}
                aria-expanded={card.id === selected?.id} aria-controls="cr-experience-detail"
                style={starPosition(point, color)} onClick={() => selectCard(card)}>
                <span className="cr-star-mark" aria-hidden="true" />
                <span className="cr-star-label" aria-hidden="true">{card.title}</span>
              </button>
            ))}
          </div>
        ))}
      </div>
      {pageCount > 1 && (
        <div className="cr-map-pages" role="group" aria-label="经历星图分页">
          <button type="button" className="cr-map-page" aria-label="上一页" disabled={currentPage === 0} onClick={() => changePage(currentPage - 1)}><ChevronLeft size={18} /></button>
          <span aria-live="polite">第 {currentPage + 1} / {pageCount} 页</span>
          <button type="button" className="cr-map-page" aria-label="下一页" disabled={currentPage + 1 === pageCount} onClick={() => changePage(currentPage + 1)}><ChevronRight size={18} /></button>
        </div>
      )}
      <article ref={detail} id="cr-experience-detail" className="cr-glass cr-experience" hidden={!selected}>
        {selected && CardIcon && (
          <>
            <header className="cr-card-header">
              <span className="cr-card-icon" aria-hidden="true"><CardIcon size={19} /></span>
              <div className="cr-card-title">
                <h3>{selected.title}</h3>
                <small>{showingExamples
                  ? `${supportChoices[selected.choice].label} · 虚构示例`
                  : `${archiveDate.format(new Date(selected.createdAt))} · ${supportChoices[selected.choice].label} · ${reflectionStatusLabels[selected.reflectionStatus]}`}</small>
              </div>
            </header>
            <div className="cr-card-details">
              {!showingExamples && <span className="cr-card-provenance">{selected.source === "fastapi" ? "对话记录 · 用户确认" : "演示来源"}</span>}
              <p>{selected.action}</p>
              <dl>{hypothesisKeys.map((key) => <div key={key}><dt>{hypothesisLabels[key]}</dt><dd>{selected.hypotheses[key] || "留白"}</dd></div>)}</dl>
            </div>
          </>
        )}
      </article>
      {!cards.length && (
        <div className="cr-empty-cards">
          <h3>这个筛选下暂时没有卡片</h3>
          <button type="button" className="cr-button cr-glass" onClick={() => onFilterChange("all")}>查看全部<ArrowUpRight size={16} /></button>
        </div>
      )}
    </div>
  );
}
