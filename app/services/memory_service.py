"""Long-term memory service: propose/confirm/edit/delete/select/export.

Selection follows the three-signal retrieval described in the design doc
(recency decay + importance + topic relevance), capped by a small top-k and a
summary character budget. L1 confirmed records are not handled here yet.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.agent.errors import RuntimeFailure
from app.config.settings import get_settings
from app.schemas.memory import (
    MAX_SUMMARY_CHARS,
    Memory,
    MemoryConfirmRequest,
    MemoryCreateRequest,
    MemoryReference,
    MemorySelection,
    MemorySource,
    MemoryUpdateRequest,
    summarise,
)
from app.services.memory_store import MemoryStore

_WORD = re.compile(r"[0-9a-zA-Z_]+")


def _terms(text: str) -> set[str]:
    """Cheap bilingual tokenizer: latin words plus CJK character bigrams."""
    lowered = text.lower()
    terms = set(_WORD.findall(lowered))
    cjk = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]", "", lowered)
    terms.update(cjk[i:i + 2] for i in range(len(cjk) - 1))
    return {term for term in terms if term.strip()}


def relevance(topic: str, memory: Memory) -> float:
    if not topic.strip():
        return 0.0
    topic_terms = _terms(topic)
    if not topic_terms:
        return 0.0
    memory_terms = _terms(memory.summary + memory.text)
    shared = topic_terms & memory_terms
    if not shared:
        return 0.0
    return min(1.0, len(shared) / min(len(topic_terms), 12))


class MemoryService:
    def __init__(self, store: MemoryStore | None = None, settings=None, clock=None):
        self.settings = settings or get_settings()
        self.store = store or MemoryStore(self.settings.memory_db_path)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    # ---- helpers ---------------------------------------------------------
    def _now(self) -> datetime:
        return self._clock()

    def _require_enabled(self) -> None:
        if not self.settings.memory_enabled:
            raise RuntimeFailure("memory_disabled", "长期记忆当前未启用。", 409)

    def _require(self, memory_id: str, owner_id: str) -> Memory:
        memory = self.store.get(memory_id, owner_id)
        if memory is None or memory.status == "deleted":
            raise RuntimeFailure("memory_not_found", "记忆不存在或已删除。", 404)
        return memory

    def _check_revision(self, memory: Memory, expected: int) -> None:
        if memory.revision != expected:
            raise RuntimeFailure("memory_revision_conflict", "记忆版本已变化，请刷新后重试。", 409)

    # ---- writes ----------------------------------------------------------
    def create(self, request: MemoryCreateRequest, owner_id: str = "local") -> Memory:
        self._require_enabled()
        if self.store.count_active(owner_id) >= self.settings.memory_max_items:
            raise RuntimeFailure("memory_limit_exceeded", "记忆条数已达上限，请先删除一些。", 409)
        cleaned = request.text.strip()
        # 重复的手写条目直接拒绝：合并候选留给画布提议阶段的写入裁决。
        for existing in self.store.list(owner_id):
            if existing.status != "deleted" and existing.kind == request.kind and existing.text == cleaned:
                raise RuntimeFailure(
                    "memory_duplicate",
                    f"已有一条相同的记忆：{existing.memory_id}",
                    409,
                )
        now = self._now()
        summary = (request.summary or summarise(cleaned)).strip()
        if len(summary) > MAX_SUMMARY_CHARS:
            raise RuntimeFailure("memory_summary_invalid", "摘要过长。", 422)
        if len(cleaned) > self.settings.memory_max_text_chars:
            raise RuntimeFailure("memory_text_invalid", "记忆正文过长。", 422)
        memory = Memory(
            memory_id=f"mem_{uuid4().hex[:12]}",
            owner_id=owner_id,
            kind=request.kind,
            summary=summary,
            text=cleaned,
            importance=request.importance,
            source=MemorySource(
                type="manual" if request.session_id is None else "user_stated",
                session_id=request.session_id,
                message_index=request.message_index,
            ),
            status="proposed",
            revision=1,
            valid_from=now,
            valid_to=None,
            derived_from_record_id=request.derived_from_record_id,
            created_at=now,
            updated_at=now,
        )
        self.store.insert(memory)
        return memory

    def get(self, memory_id: str, owner_id: str = "local") -> Memory:
        return self._require(memory_id, owner_id)

    def confirm(self, memory_id: str, request: MemoryConfirmRequest, owner_id: str = "local") -> Memory:
        self._require_enabled()
        memory = self._require(memory_id, owner_id)
        self._check_revision(memory, request.expected_revision)
        now = self._now()
        memory.status = "confirmed"
        memory.revision += 1
        memory.updated_at = now
        self.store.save(memory)
        return memory

    def update(self, memory_id: str, request: MemoryUpdateRequest, owner_id: str = "local") -> Memory:
        self._require_enabled()
        memory = self._require(memory_id, owner_id)
        self._check_revision(memory, request.expected_revision)
        if request.text is not None:
            if len(request.text) > self.settings.memory_max_text_chars:
                raise RuntimeFailure("memory_text_invalid", "记忆正文过长。", 422)
            memory.text = request.text.strip()
        if request.summary is not None:
            memory.summary = request.summary.strip()[:MAX_SUMMARY_CHARS]
        if request.kind is not None:
            memory.kind = request.kind
        if request.importance is not None:
            memory.importance = request.importance
        memory.revision += 1
        memory.updated_at = self._now()
        # Edited content must be confirmed again; the old confirmation referred
        # to the previous wording.
        memory.status = "proposed"
        self.store.save(memory)
        return memory

    def delete(self, memory_id: str, owner_id: str = "local") -> Memory:
        self._require_enabled()
        memory = self._require(memory_id, owner_id)
        now = self._now()
        memory.status = "deleted"
        memory.summary = ""
        memory.text = ""
        memory.deleted_at = now
        memory.updated_at = now
        memory.revision += 1
        self.store.save(memory)
        return memory

    # ---- reads -----------------------------------------------------------
    def list(self, status: str | None = None, owner_id: str = "local") -> list[Memory]:
        return self.store.list(owner_id, status)

    def active_references(self, owner_id: str = "local") -> set[tuple[str, int]]:
        now = self._now()
        return {(m.memory_id, m.revision) for m in self.store.list(owner_id) if m.is_active(now)}

    def select(self, topic: str, owner_id: str = "local", k: int | None = None) -> MemorySelection:
        if not self.settings.memory_enabled:
            return MemorySelection()
        now = self._now()
        limit = k or self.settings.memory_top_k
        excluded: list[str] = []
        scored: list[tuple[float, Memory]] = []
        for memory in self.store.list(owner_id, status="confirmed"):
            if not memory.is_active(now):
                excluded.append(memory.memory_id)
                continue
            score_relevance = relevance(topic, memory)
            if topic.strip() and score_relevance < self.settings.memory_relevance_floor:
                excluded.append(memory.memory_id)
                continue
            anchor = memory.last_used_at or memory.created_at
            days = max(0.0, (now - anchor).total_seconds() / 86400)
            recency = math.pow(self.settings.memory_decay, days)
            importance = memory.importance / 5
            scored.append((recency + importance + score_relevance, memory))
        scored.sort(key=lambda pair: (pair[0], pair[1].updated_at), reverse=True)
        picked: list[Memory] = []
        budget = self.settings.memory_budget_chars
        for _, memory in scored:
            if len(picked) >= limit:
                break
            if len(memory.summary) > budget:
                excluded.append(memory.memory_id)
                continue
            budget -= len(memory.summary)
            picked.append(memory)
        return MemorySelection(memories=picked, excluded_memory_ids=excluded)

    def touch(self, references: list[MemoryReference], owner_id: str = "local") -> None:
        self.store.touch([ref.memory_id for ref in references], self._now(), owner_id)

    def export(self, fmt: str = "markdown", owner_id: str = "local") -> str:
        items = [m for m in self.store.list(owner_id) if m.status != "deleted"]
        if fmt == "json":
            return json.dumps(
                {"memories": [m.model_dump(mode="json") for m in items]},
                ensure_ascii=False, indent=2,
            )
        lines = ["# 我的记忆", ""]
        for memory in items:
            lines.append(f"- [{memory.kind}] {memory.summary}（v{memory.revision}，{memory.status}）")
            if memory.text and memory.text != memory.summary:
                lines.append(f"  - {memory.text}")
        return "\n".join(lines) + "\n"

    def purge_tombstones(self, owner_id: str = "local") -> int:
        cutoff = self._now() - timedelta(days=self.settings.memory_tombstone_days)
        return self.store.purge_deleted(cutoff, owner_id)
