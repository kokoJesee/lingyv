import logging
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.responses import JSONResponse

from app.agent.errors import RuntimeFailure
from app.agent.context import build_context
from app.schemas.context import ContextManifest
from app.agent.orchestrator import AgentOrchestrator
from app.config.logging import configure_logging
from app.config.settings import get_settings
from app.llm.client import ChatModel, get_chat_model
from app.llm.errors import LLMError
from app.llm.prompts import PROMPT_VERSION
from app.schemas.draft import (
    ConfirmDraftRequest, CreateDraftRequest, Draft,
    EditDraftRequest, RejectDraftRequest,
)
from app.services.draft_service import DraftService
from app.schemas.archive import ArchiveInfo, SaveArchiveRequest
from app.services.archive_service import ArchiveService
from app.services.archive_store import DEFAULT_ARCHIVE_PATH, SQLiteArchiveStore
from app.schemas.events import RuntimeEvent
from app.schemas.message import SendMessageRequest, SendMessageResponse
from app.schemas.session import Session, SessionSummary
from app.schemas.workflow import CancelWorkflowRequest, ConfirmWorkflowRequest
from app.services.session_service import session_service

logger = logging.getLogger(__name__)


def get_orchestrator() -> AgentOrchestrator:
    return AgentOrchestrator(session_service)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = get_settings()
    logger.info(
        "Agent starting | model=%s | key_configured=%s | prompt_version=%s",
        settings.deepseek_model, settings.key_configured, PROMPT_VERSION,
    )
    yield
    logger.info("Agent stopped")


app = FastAPI(
    title="Personal Emotional Agent",
    description="Personal Emotional Agent runtime: sessions, workflows and model calls.",
    version="0.4.0", lifespan=lifespan,
)


@app.exception_handler(RuntimeFailure)
@app.exception_handler(LLMError)
async def handle_agent_error(request: Request, error):
    detail = (
        "Session not found" if error.code == "session_not_found"
        else {"code": error.code, "message": error.message}
    )
    return JSONResponse(status_code=error.status_code, content={"detail": detail})


@app.get("/")
async def root():
    return {"message": "Welcome to Personal Emotional Agent!", "status": "running"}


@app.get("/health")
async def health_check():
    settings = get_settings()
    return {
        "status": "ok", "service": "Personal Emotional Agent",
        "llm": {
            "provider": "deepseek", "model": settings.deepseek_model,
            "key_configured": settings.key_configured,
        },
        "runtime": {
            "storage": "memory", "prompt_version": PROMPT_VERSION,
            "max_steps": settings.runtime_max_steps,
            "max_context_chars": settings.runtime_max_context_chars,
        },
    }


@app.post("/sessions", response_model=Session, status_code=201)
async def create_session():
    return session_service.create().model_copy(deep=True)


@app.get("/sessions", response_model=list[SessionSummary])
async def list_sessions():
    return session_service.list()


@app.get("/sessions/{session_id}", response_model=Session)
async def get_session(session_id: UUID):
    return session_service.require(session_id).session.model_copy(deep=True)


@app.post(
    "/sessions/{session_id}/messages", response_model=SendMessageResponse,
    summary="发送消息，成功后一次性保存本轮",
    description="可携带 request_id 去重；同一编号的失败结果也会被复用，重新尝试需使用新编号。",
    responses={
        409: {"description": "会话或流程状态冲突、请求编号冲突"},
        413: {"description": "超过上下文字符预算"},
        502: {"description": "模型调用或输出异常"},
        503: {"description": "模型不可用"},
        504: {"description": "模型调用超时"},
    },
)
async def send_message(
    session_id: UUID, request: SendMessageRequest,
    model: Annotated[ChatModel, Depends(get_chat_model)],
):
    return await get_orchestrator().handle_message(session_id, request, model)


@app.post("/sessions/{session_id}/end", response_model=Session)
async def end_session(session_id: UUID):
    return await get_orchestrator().end_session(session_id)


@app.delete("/sessions/{session_id}/messages", response_model=Session)
async def clear_history(session_id: UUID):
    return await get_orchestrator().clear_history(session_id)


@app.post("/sessions/{session_id}/workflow/demo", response_model=Session)
async def start_workflow_demo(session_id: UUID):
    """纯运行机制演示：结构校验后暂停，等待明确确认；不调用模型。"""
    return await get_orchestrator().start_demo(session_id)


@app.post("/sessions/{session_id}/workflow/confirm", response_model=Session)
async def confirm_workflow(session_id: UUID, request: ConfirmWorkflowRequest):
    return await get_orchestrator().confirm(
        session_id, request.run_id, request.confirmation_token
    )


@app.post("/sessions/{session_id}/workflow/cancel", response_model=Session)
async def cancel_workflow(session_id: UUID, request: CancelWorkflowRequest):
    return await get_orchestrator().cancel(session_id, request.run_id)


@app.get("/sessions/{session_id}/events", response_model=list[RuntimeEvent])
async def get_events(session_id: UUID):
    return list(session_service.require(session_id).events)

# 通用示例草稿：仅定义工程版本与确认机制。



@app.post("/sessions/{session_id}/drafts", response_model=Draft, status_code=201)
async def create_draft(session_id: UUID, request: CreateDraftRequest):
    return await DraftService(session_service).create(session_id, request)


@app.get("/sessions/{session_id}/drafts", response_model=list[Draft])
async def list_drafts(session_id: UUID):
    return DraftService(session_service).list(session_id)


@app.get("/sessions/{session_id}/drafts/{draft_id}", response_model=Draft)
async def get_draft(session_id: UUID, draft_id: UUID):
    return DraftService(session_service).get(session_id, draft_id)


@app.put("/sessions/{session_id}/drafts/{draft_id}", response_model=Draft)
async def edit_draft(session_id: UUID, draft_id: UUID, request: EditDraftRequest):
    return await DraftService(session_service).edit(session_id, draft_id, request)


@app.post("/sessions/{session_id}/drafts/{draft_id}/confirm", response_model=Draft)
async def confirm_draft(session_id: UUID, draft_id: UUID, request: ConfirmDraftRequest):
    return await DraftService(session_service).confirm(session_id, draft_id, request)


@app.post("/sessions/{session_id}/drafts/{draft_id}/reject", response_model=Draft)
async def reject_draft(session_id: UUID, draft_id: UUID, request: RejectDraftRequest):
    return await DraftService(session_service).reject(session_id, draft_id, request)


@app.get("/sessions/{session_id}/context", response_model=ContextManifest | None)
async def get_last_context(session_id: UUID):
    manifest = session_service.require(session_id).last_context
    return manifest.model_copy(deep=True) if manifest else None


@app.post("/sessions/{session_id}/context/preview", response_model=ContextManifest)
async def preview_context(session_id: UUID, request: SendMessageRequest):
    record = session_service.require(session_id)
    async with record.lock:
        return build_context(
            record.session, list(record.drafts.values()), record.turn_sources,
            request.content, request.request_id, get_settings().runtime_max_context_chars,
        ).manifest


def get_archive_service() -> ArchiveService:
    return ArchiveService(session_service, SQLiteArchiveStore(DEFAULT_ARCHIVE_PATH))


@app.post("/sessions/{session_id}/archive", response_model=ArchiveInfo)
async def save_archive(
    session_id: UUID, request: SaveArchiveRequest,
    archives: Annotated[ArchiveService, Depends(get_archive_service)],
):
    return await archives.save(session_id, request)


@app.get("/archives", response_model=list[ArchiveInfo])
async def list_archives(archives: Annotated[ArchiveService, Depends(get_archive_service)]):
    return await archives.list()


@app.post("/archives/{archive_id}/restore", response_model=Session)
async def restore_archive(
    archive_id: UUID, archives: Annotated[ArchiveService, Depends(get_archive_service)],
):
    return await archives.restore(archive_id)


@app.delete("/archives/{archive_id}", status_code=204)
async def delete_archive(
    archive_id: UUID, archives: Annotated[ArchiveService, Depends(get_archive_service)],
    expected_version: Annotated[int, Query(ge=1)],
):
    await archives.delete(archive_id, expected_version)
    return Response(status_code=204)