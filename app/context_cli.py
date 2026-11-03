import json

from app.agent.orchestrator import AgentOrchestrator
from app.schemas.draft import (
    ConfirmDraftRequest, CreateDraftRequest, DraftContent, EditDraftRequest, RejectDraftRequest,
)
from app.schemas.message import SendMessageRequest
from app.services.draft_service import DraftService
from app.services.session_service import SessionService


async def context_demo() -> int:
    sessions = SessionService()
    session_id = sessions.create().session_id
    drafts = DraftService(sessions)
    runtime = AgentOrchestrator(sessions)

    class InspectionModel:
        def __init__(self):
            self.messages = []

        async def generate_reply(self, messages):
            self.messages = messages
            blocks = [message.content for message in messages if "current_user_confirmed_drafts" in message.content]
            return blocks[-1] if blocks else "没有当前确认草稿"

    model = InspectionModel()

    async def ask():
        result = await runtime.handle_message(
            session_id, SendMessageRequest(content="继续这个虚构测试"), model
        )
        return result, "\n".join(message.content for message in model.messages)

    first = await drafts.create(session_id, CreateDraftRequest(
        content=DraftContent(title="VERSION_A_SENTINEL", items=["示例A"])
    ))
    first = await drafts.confirm(session_id, first.draft_id, ConfirmDraftRequest(
        expected_revision=first.revision, confirmation_token=first.confirmation_token,
    ))
    _, text = await ask()
    assert "VERSION_A_SENTINEL" in text
    print("已确认草稿 A：已进入模型输入。")
    edited = await drafts.edit(session_id, first.draft_id, EditDraftRequest(
        expected_revision=first.revision,
        content=DraftContent(title="VERSION_B_SENTINEL", items=["示例B"]),
    ))
    result, text = await ask()
    assert "VERSION_A_SENTINEL" not in text and "VERSION_B_SENTINEL" not in text
    assert result.context_manifest.excluded_history_indices
    print("修改后尚未确认：旧草稿及依赖它的旧回复已排除，新草稿也未注入。")
    confirmed = await drafts.confirm(session_id, edited.draft_id, ConfirmDraftRequest(
        expected_revision=edited.revision, confirmation_token=edited.confirmation_token,
    ))
    result, text = await ask()
    assert "VERSION_B_SENTINEL" in text and "VERSION_A_SENTINEL" not in text
    print("重新确认 B：使用当前版本，来源记录含草稿编号与版本。")
    await drafts.reject(session_id, confirmed.draft_id, RejectDraftRequest(
        expected_revision=confirmed.revision,
    ))
    _, text = await ask()
    assert "VERSION_A_SENTINEL" not in text and "VERSION_B_SENTINEL" not in text
    print("撤回 B：草稿与依赖它的旧回复不再进入新请求。")
    await runtime.clear_history(session_id)
    assert sessions.require(session_id).last_context is None
    print("清空后：来源记录与草稿依赖已清除。本演示不访问模型服务。")
    return 0
