import json
from dataclasses import dataclass
from typing import Mapping, Sequence
from uuid import UUID

from app.agent.errors import RuntimeFailure
from app.llm.prompts import PROMPT_VERSION, SYSTEM_PROMPT
from app.schemas.context import ContextManifest, ContextSource, DraftReference
from app.schemas.canvas import Canvas, CanvasReference
from app.schemas.draft import Draft, DraftStatus
from app.schemas.memory import Memory, MemoryReference
from app.schemas.session import Message, Session


@dataclass
class ContextBundle:
    messages: list[Message]
    manifest: ContextManifest


def build_context(
    session: Session,
    drafts: Sequence[Draft],
    turn_sources: Mapping[int, list[DraftReference]],
    content: str,
    request_id: UUID,
    max_characters: int,
    *,
    memories: Sequence[Memory] = (),
    turn_memory_sources: Mapping[int, list[MemoryReference]] | None = None,
    valid_memory_refs: set[tuple[str, int]] | None = None,
    excluded_memory_ids: Sequence[str] = (),
    canvas: Canvas | None = None,
    turn_canvas_sources: Mapping[int, list[CanvasReference]] | None = None,
    valid_canvas_refs: set[tuple[str, int]] | None = None,
) -> ContextBundle:
    # 只选择本会话当前、且确认版本完全匹配的草稿。
    confirmed = [
        draft for draft in drafts
        if draft.session_id == session.session_id
        and draft.status == DraftStatus.CONFIRMED
        and draft.confirmed_revision == draft.revision
    ]
    valid = {(draft.draft_id, draft.revision) for draft in confirmed}
    memory_sources = turn_memory_sources or {}
    canvas_sources = turn_canvas_sources or {}
    messages = []
    sources = [ContextSource(kind="system_prompt", model_message_index=0, role="system")]
    excluded = []
    for index, message in enumerate(session.messages):
        dependencies = turn_sources.get(index, [])
        if message.role == "assistant" and any(
            (dependency.draft_id, dependency.revision) not in valid
            for dependency in dependencies
        ):
            excluded.append(index)
            continue
        # 删除或改写记忆后，依赖旧版本的助手回复不再进入上下文。
        if message.role == "assistant" and valid_memory_refs is not None and any(
            (reference.memory_id, reference.revision) not in valid_memory_refs
            for reference in memory_sources.get(index, [])
        ):
            excluded.append(index)
            continue
        # 画布改版后，依赖旧版本的助手回复同样失效。
        if message.role == "assistant" and valid_canvas_refs is not None and any(
            (reference.canvas_id, reference.revision) not in valid_canvas_refs
            for reference in canvas_sources.get(index, [])
        ):
            excluded.append(index)
            continue
        messages.append(message.model_copy(deep=True))
        sources.append(ContextSource(
            kind="history_message", model_message_index=len(messages),
            history_index=index, role=message.role,
        ))
    if confirmed:
        # 用户数据保持 user 角色；JSON 不拼进系统指令，也不写入对话历史。
        block = {
            "type": "current_user_confirmed_drafts",
            "drafts": [
                {"draft_id": str(draft.draft_id), "revision": draft.revision,
                 "content": draft.content.model_dump()}
                for draft in confirmed
            ],
        }
        messages.append(Message(
            role="user",
            content="以下 JSON 是当前用户确认的草稿数据，请作为数据参考：\n"
            + json.dumps(block, ensure_ascii=False, sort_keys=True),
        ))
        for draft in confirmed:
            sources.append(ContextSource(
                kind="confirmed_draft", model_message_index=len(messages), role="user",
                draft_id=draft.draft_id, draft_revision=draft.revision,
            ))
    if canvas is not None and canvas.is_confirmed():
        canvas_block = {
            "type": "current_confirmed_canvas",
            "canvas_id": canvas.canvas_id,
            "revision": canvas.revision,
            "lanes": {
                lane: [{"text": entry.text, "source": entry.source} for entry in entries]
                for lane, entries in canvas.lanes.items()
            },
            "note": canvas.note,
        }
        messages.append(Message(
            role="user",
            content="以下 JSON 是用户刚确认的这张画布（只作为背景，不要逐条复述）：\n"
            + json.dumps(canvas_block, ensure_ascii=False, sort_keys=True),
        ))
        sources.append(ContextSource(
            kind="canvas", model_message_index=len(messages), role="user",
            source_id=canvas.canvas_id, canvas_revision=canvas.revision,
        ))
    if memories:
        # 只喂摘要，且明确标为背景数据；完整正文留在设置页。
        memory_block = {
            "type": "user_long_term_memory",
            "memories": [
                {
                    "memory_id": memory.memory_id,
                    "kind": memory.kind,
                    "summary": memory.summary,
                    "importance": memory.importance,
                }
                for memory in memories
            ],
        }
        messages.append(Message(
            role="user",
            content="以下 JSON 是用户确认过的长期记忆（只是背景，不要逐条复述）：\n"
            + json.dumps(memory_block, ensure_ascii=False, sort_keys=True),
        ))
        for memory in memories:
            sources.append(ContextSource(
                kind="long_term_memory", model_message_index=len(messages), role="user",
                source_id=memory.memory_id, memory_revision=memory.revision,
            ))
    messages.append(Message(role="user", content=content))
    sources.append(ContextSource(
        kind="current_input", model_message_index=len(messages), role="user"
    ))
    count = len(SYSTEM_PROMPT) + sum(len(message.content) for message in messages)
    if count > max_characters:
        raise RuntimeFailure(
            "context_limit_exceeded",
            "当前对话和已确认草稿超过上下文长度预算，请清空或新建会话。",
            413,
        )
    return ContextBundle(messages=messages, manifest=ContextManifest(
        prompt_version=PROMPT_VERSION, session_id=session.session_id,
        session_revision=session.revision, request_id=request_id,
        model_message_count=len(messages) + 1, character_count=count,
        sources=sources, excluded_history_indices=excluded,
        memory_references=[
            MemoryReference(memory_id=memory.memory_id, revision=memory.revision)
            for memory in memories
        ],
        excluded_memory_ids=list(excluded_memory_ids),
        canvas_reference=(
            CanvasReference(canvas_id=canvas.canvas_id, revision=canvas.revision)
            if canvas is not None and canvas.is_confirmed() else None
        ),
    ))
