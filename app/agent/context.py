import json
from dataclasses import dataclass
from typing import Mapping, Sequence
from uuid import UUID

from app.agent.errors import RuntimeFailure
from app.llm.prompts import PROMPT_VERSION, SYSTEM_PROMPT
from app.schemas.context import ContextManifest, ContextSource, DraftReference
from app.schemas.draft import Draft, DraftStatus
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
) -> ContextBundle:
    # 只选择本会话当前、且确认版本完全匹配的草稿。
    confirmed = [
        draft for draft in drafts
        if draft.session_id == session.session_id
        and draft.status == DraftStatus.CONFIRMED
        and draft.confirmed_revision == draft.revision
    ]
    valid = {(draft.draft_id, draft.revision) for draft in confirmed}
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
    ))
