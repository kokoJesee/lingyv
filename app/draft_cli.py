import json
from uuid import UUID

from app.agent.errors import RuntimeFailure
from app.agent.orchestrator import AgentOrchestrator
from app.schemas.draft import (
    ConfirmDraftRequest, CreateDraftRequest, DraftContent,
    EditDraftRequest, RejectDraftRequest,
)
from app.services.draft_service import DraftService
from app.services.session_service import SessionService

DRAFT_HELP = """
/draft-new 标题   创建通用示例草稿
/draft           查看当前会话最新创建的草稿
/draft-list      列出当前会话草稿
/draft-edit 标题  修改最新草稿标题，并使原确认失效
/draft-confirm   确认最新草稿的当前版本
/draft-reject    拒绝或撤回最新草稿
""".strip()


def show(draft):
    print(json.dumps(draft.model_dump(mode="json"), ensure_ascii=False, indent=2))


async def handle_draft_command(text: str, session_id: UUID, sessions: SessionService) -> bool:
    command, _, argument = text.partition(" ")
    if command not in (
        "/draft-new", "/draft", "/draft-list", "/draft-edit",
        "/draft-confirm", "/draft-reject",
    ):
        return False
    service = DraftService(sessions)
    if command == "/draft-new":
        draft = await service.create(session_id, CreateDraftRequest(
            content=DraftContent(title=argument or "通用草稿示例", items=["示例条目"])
        ))
        show(draft)
        return True
    drafts = service.list(session_id)
    if command == "/draft-list":
        for draft in drafts:
            print(draft.draft_id, draft.revision, draft.status.value)
        return True
    if not drafts:
        print("当前会话没有草稿，请先输入 /draft-new。")
        return True
    current = drafts[-1]
    if command == "/draft":
        show(current)
    elif command == "/draft-edit":
        updated = current.content.model_copy()
        updated.title = argument
        # 通过输入 Schema 重新校验，不靠赋值绕过校验。
        show(await service.edit(session_id, current.draft_id, EditDraftRequest(
            expected_revision=current.revision,
            content=DraftContent.model_validate(updated.model_dump()),
        )))
    elif command == "/draft-confirm":
        if current.confirmation_token is None:
            print("该草稿当前没有有效确认凭据。")
        else:
            show(await service.confirm(session_id, current.draft_id, ConfirmDraftRequest(
                expected_revision=current.revision,
                confirmation_token=current.confirmation_token,
            )))
    else:
        show(await service.reject(session_id, current.draft_id, RejectDraftRequest(
            expected_revision=current.revision,
        )))
    return True


async def draft_demo() -> int:
    sessions = SessionService()
    service = DraftService(sessions)
    session_id = sessions.create().session_id
    first = await service.create(session_id, CreateDraftRequest(
        content=DraftContent(title="版本一", items=["通用示例"])
    ))
    print("创建：", first.revision, first.status.value)
    second = await service.edit(session_id, first.draft_id, EditDraftRequest(
        expected_revision=first.revision,
        content=DraftContent(title="版本二", items=["已修改"]),
    ))
    print("修改：", second.revision, second.status.value)
    try:
        await service.confirm(session_id, first.draft_id, ConfirmDraftRequest(
            expected_revision=first.revision, confirmation_token=first.confirmation_token,
        ))
    except RuntimeFailure as error:
        if error.code != "stale_revision":
            raise
        print("旧版本确认已阻止：", error.code)
    confirmed = await service.confirm(session_id, second.draft_id, ConfirmDraftRequest(
        expected_revision=second.revision, confirmation_token=second.confirmation_token,
    ))
    print("确认：", confirmed.revision, confirmed.status.value)
    edited = await service.edit(session_id, confirmed.draft_id, EditDraftRequest(
        expected_revision=confirmed.revision,
        content=DraftContent(title="再次编辑", items=["需要重新确认"]),
    ))
    assert edited.confirmed_revision is None
    print("再编辑：", edited.revision, edited.status.value, "原确认已失效")
    await AgentOrchestrator(sessions).clear_history(session_id)
    assert service.list(session_id) == []
    print("清空：草稿与缓存内容已移除。演示不访问模型服务。")
    return 0
