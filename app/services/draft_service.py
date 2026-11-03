import hashlib
import json
import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

from app.agent.errors import RuntimeFailure
from app.config.settings import Settings, get_settings
from app.schemas.draft import (
    ConfirmDraftRequest, CreateDraftRequest, Draft, DraftStatus,
    EditDraftRequest, RejectDraftRequest,
)
from app.schemas.session import SessionStatus
from app.services.session_service import OperationRecord, SessionService

logger = logging.getLogger(__name__)


class DraftService:
    """仅管理草稿机械流程，不决定心理字段或业务策略。"""

    def __init__(self, sessions: SessionService, settings: Settings | None = None):
        self.sessions = sessions
        self.settings = settings or get_settings()

    def list(self, session_id: UUID) -> list[Draft]:
        return [
            draft.model_copy(deep=True)
            for draft in self.sessions.require(session_id).drafts.values()
        ]

    def get(self, session_id: UUID, draft_id: UUID) -> Draft:
        draft = self.sessions.require(session_id).drafts.get(draft_id)
        if draft is None:
            raise RuntimeFailure("draft_not_found", "草稿不存在或已清空。", 404)
        return draft.model_copy(deep=True)

    async def create(self, session_id: UUID, request: CreateDraftRequest) -> Draft:
        return await self._apply(session_id, "create", None, request)

    async def edit(self, session_id: UUID, draft_id: UUID, request: EditDraftRequest) -> Draft:
        return await self._apply(session_id, "edit", draft_id, request)

    async def confirm(self, session_id: UUID, draft_id: UUID, request: ConfirmDraftRequest) -> Draft:
        return await self._apply(session_id, "confirm", draft_id, request)

    async def reject(self, session_id: UUID, draft_id: UUID, request: RejectDraftRequest) -> Draft:
        return await self._apply(session_id, "reject", draft_id, request)

    async def _apply(self, session_id, action, draft_id, request) -> Draft:
        record = self.sessions.require(session_id)
        fingerprint = hashlib.sha256(json.dumps(
            {"action": action, "draft_id": str(draft_id),
             "request": request.model_dump(mode="json", exclude={"operation_id"})},
            sort_keys=True, ensure_ascii=False,
        ).encode("utf-8")).hexdigest()
        async with record.lock:
            previous = record.draft_operations.get(request.operation_id)
            if previous:
                if previous.fingerprint != fingerprint:
                    raise RuntimeFailure("operation_id_conflict", "同一操作编号不能用于不同操作。")
                if previous.error:
                    raise RuntimeFailure(*previous.error)
                return Draft.model_validate(previous.result)
            if record.lifecycle_pending or record.session.status != SessionStatus.ACTIVE:
                raise RuntimeFailure("session_not_editable", "会话正在清空或已结束。")
            if len(record.operations) + len(record.draft_operations) >= self.settings.runtime_max_requests_per_session:
                raise RuntimeFailure("request_limit_exceeded", "会话操作记录已达上限，请新建会话。")
            operation = OperationRecord(fingerprint=fingerprint)
            record.draft_operations[request.operation_id] = operation
            try:
                if action == "create":
                    candidate = Draft(session_id=session_id, content=request.content.model_copy(deep=True))
                else:
                    current = record.drafts.get(draft_id)
                    if current is None:
                        raise RuntimeFailure("draft_not_found", "草稿不存在或已清空。", 404)
                    if request.expected_revision != current.revision:
                        raise RuntimeFailure("stale_revision", "草稿版本已变化，请读取当前版本后重试。")
                    if current.status == DraftStatus.REJECTED:
                        raise RuntimeFailure("draft_rejected", "该草稿已拒绝，请创建新草稿。")
                    candidate = current.model_copy(deep=True)
                    candidate.revision += 1
                    candidate.updated_at = datetime.now(timezone.utc)
                    if action == "edit":
                        candidate.content = request.content.model_copy(deep=True)
                        candidate.status = DraftStatus.PROPOSED
                        candidate.confirmed_revision = None
                        candidate.confirmation_token = uuid4()
                    elif action == "confirm":
                        if current.status != DraftStatus.PROPOSED:
                            raise RuntimeFailure("draft_not_proposed", "当前草稿不处于待确认状态。")
                        if request.confirmation_token != current.confirmation_token:
                            raise RuntimeFailure("stale_confirmation", "确认凭据已失效。")
                        candidate.status = DraftStatus.CONFIRMED
                        candidate.confirmed_revision = candidate.revision
                        candidate.confirmation_token = None
                    else:
                        candidate.status = DraftStatus.REJECTED
                        candidate.confirmed_revision = None
                        candidate.confirmation_token = None
                if len(candidate.content.model_dump_json()) > self.settings.runtime_max_context_chars:
                    raise RuntimeFailure("draft_too_large", "草稿内容超过长度预算。", 413)
                result = candidate.model_dump(mode="json")
                record.drafts[candidate.draft_id] = candidate
                record.session.revision += 1
                operation.result = result
                logger.info(
                    "draft_action=%s | operation_id=%s | draft_id=%s | revision=%d | status=%s",
                    action, request.operation_id, candidate.draft_id,
                    candidate.revision, candidate.status.value,
                )
                return candidate.model_copy(deep=True)
            except RuntimeFailure as error:
                operation.error = (error.code, error.message, error.status_code)
                raise
