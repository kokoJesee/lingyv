"""Keyword screening for sensitive content in a canvas.

This is a *notice*, not a diagnosis: a hit only asks the interface to show a
gentle banner and tells the memory layer to skip. It never blocks writing and
never stores or logs the matched words (only category names).
"""
from __future__ import annotations

from collections.abc import Iterable

from app.schemas.canvas import Sensitivity

CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "self_harm": (
        "自杀", "自残", "自伤", "不想活", "想死", "轻生", "结束生命", "活着没意思",
        "割腕", "跳楼", "跳桥", "吞药", "安眠药", "suicide", "kill myself", "self-harm",
    ),
    "abuse": ("虐待", "家暴", "被打", "挨打", "打我", "虐待儿童", "abuse", "beaten"),
    "violence": ("杀人", "想杀", "报复", "打死", "砍死", "捅死", "violence", "revenge"),
    "sexual": ("性侵", "强奸", "猥亵", "性骚扰", "rape", "assault"),
    "legal": ("报警", "立案", "起诉", "律师", "拘留", "判刑", "police report", "lawsuit"),
}
CATEGORIES = tuple(CATEGORY_KEYWORDS)


def scan(texts: Iterable[str]) -> Sensitivity:
    content = "\n".join(text for text in texts if text)
    if not content:
        return Sensitivity()
    lowered = content.lower()
    hits = [
        category
        for category, keywords in CATEGORY_KEYWORDS.items()
        if any(keyword.lower() in lowered for keyword in keywords)
    ]
    if not hits:
        return Sensitivity()
    return Sensitivity(level="notice", categories=hits)
