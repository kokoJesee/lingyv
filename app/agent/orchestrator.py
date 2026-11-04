import asyncio
import hashlib
import logging
from time import perf_counter
from uuid import UUID

from app.agent.errors import RuntimeFailure
from app.calls.scheduler import call_scope
from app.agent.context import build_context
from app.agent.events import emit
from app.agent.workflow import WorkflowEngine
from app.config.settings import Settings, get_settings
from app.llm.client import ChatModel
from app.llm.errors import LLMError
from app.llm.prompts import SYSTEM_PROMPT
from app.llm.structured import parse_structured_output, validate_reply
from app.schemas.message import SendMessageRequest, SendMessageResponse
from app.schemas.session import Message, Session, SessionState, SessionStatus
from app.schemas.workflow import DemoDraft, StopReason, WorkflowPhase, WorkflowSnapshot
from app.services.session_service import OperationRecord, SessionRecord, SessionService

logger = logging.getLogger(__name__)


class AgentOrchestrator:
    def __init__(self, sessions: SessionService, settings: Settings | None = None):
        self.sessions = sessions
        self.settings = settings or get_settings()
        self.engine = WorkflowEngine(self.settings.runtime_max_steps)

    @staticmethod
    def _active(record: SessionRecord) -> None:
        if record.lifecycle_pending > 0:
            raise RuntimeFailure("session_busy", "会话正在结束或清空，请稍后重试。")
        if record.session.status != SessionStatus.ACTIVE:
            raise RuntimeFailure("session_ended", "该会话已结束，请创建新会话。")

    async def handle_message(
        self, session_id: UUID, request: SendMessageRequest, model: ChatModel,
    ) -> SendMessageResponse:
        record = self.sessions.require(session_id)
        fingerprint = hashlib.sha256(request.content.encode("utf-8")).hexdigest()
        async with record.lock:
            previous = record.operations.get(request.request_id)
            if previous is not None:
                if previous.fingerprint != fingerprint:
                    raise RuntimeFailure(
                        "request_id_conflict", "同一请求编号不能用于不同的消息。"
                    )
                emit(
                    record, "request_replayed", request.request_id,
                    workflow_snapshot=previous.workflow,
                )
                if previous.error:
                    raise RuntimeFailure(*previous.error)
                return SendMessageResponse.model_validate(previous.result)

            self._active(record)
            if len(record.operations) + len(record.draft_operations) >= self.settings.runtime_max_requests_per_session:
                raise RuntimeFailure(
                    "request_limit_exceeded", "当前会话达到请求记录上限，请创建新会话。"
                )
            # 忙碌或等待确认时拒绝新回合，不覆盖未完成流程。
            workflow = self.engine.start(record.session.workflow, "chat", "context")
            record.session.workflow = workflow
            record.cancel_reason = None
            operation = OperationRecord(fingerprint=fingerprint)
            record.operations[request.request_id] = operation
            started = perf_counter()
            emit(record, "run_started", request.request_id)
            try:
                user_message = Message(role="user", content=request.content)
                bundle = build_context(
                    record.session, list(record.drafts.values()), record.turn_sources,
                    request.content, request.request_id, self.settings.runtime_max_context_chars,
                )
                context = bundle.messages
                self.engine.advance(workflow, "model_call")
                emit(record, "step_started", request.request_id)
                with call_scope(
                    request.request_id, workflow.run_id,
                    deadline=asyncio.get_running_loop().time() + self.settings.llm_timeout_seconds,
                ):
                    record.active_task = asyncio.create_task(model.generate_reply(context))
                try:
                    reply = await asyncio.wait_for(
                        record.active_task, timeout=self.settings.llm_timeout_seconds
                    )
                except TimeoutError:
                    raise LLMError(
                        "model_timeout", "模型回复超时，请稍后重试。", 504
                    ) from None
                if record.cancel_reason is not None:
                    raise RuntimeFailure("run_cancelled", "本轮调用已取消。")
                self.engine.advance(workflow, "output_validation")
                emit(record, "step_started", request.request_id)
                reply = validate_reply(reply)
                assistant = Message(role="assistant", content=reply)
                self.engine.advance(workflow, "commit")
                completed = workflow.model_copy(deep=True)
                self.engine.finish(completed)
                # 先完整构造返回值，再一次性提交一轮消息。
                response = SendMessageResponse(
                    session_id=session_id, state=record.session.state, reply=reply,
                    request_id=request.request_id, run_id=workflow.run_id,
                    revision=record.session.revision + 1,
                    workflow=completed,
                    context_manifest=bundle.manifest.model_copy(deep=True),
                )
                cached_result = response.model_dump(mode="json")
                assistant_index = len(record.session.messages) + 1
                record.session.messages.extend([user_message, assistant])
                record.turn_sources[assistant_index] = bundle.manifest.draft_references()
                record.last_context = bundle.manifest.model_copy(deep=True)
                logger.info(
                    'Context committed | request_id=%s | messages=%d | chars=%d | drafts=%d | excluded_replies=%d',
                    request.request_id, bundle.manifest.model_message_count,
                    bundle.manifest.character_count, len(bundle.manifest.draft_references()),
                    len(bundle.manifest.excluded_history_indices),
                )
                record.session.workflow = completed
                record.session.revision += 1
                operation.result = cached_result
                operation.workflow = completed.model_copy(deep=True)
                emit(
                    record, "run_completed", request.request_id,
                    (perf_counter() - started) * 1000,
                )
                return response
            except asyncio.CancelledError:
                error = RuntimeFailure("run_cancelled", "本轮调用已取消。")
                record.cancel_reason = record.cancel_reason or StopReason.REQUEST_CANCELLED
                self._failure(record, operation, request.request_id, error, started)
                raise error from None
            except (LLMError, RuntimeFailure) as error:
                self._failure(record, operation, request.request_id, error, started)
                raise
            except Exception as error:
                logger.error("Unexpected runtime error | type=%s", type(error).__name__)
                safe = RuntimeFailure("internal_error", "本轮处理失败，请稍后重试。", 500)
                self._failure(record, operation, request.request_id, safe, started)
                raise safe from None
            finally:
                record.active_task = None

    def _failure(self, record, operation, request_id, error, started):
        workflow = record.session.workflow
        if record.cancel_reason is not None or error.code == "run_cancelled":
            reason = record.cancel_reason or StopReason.USER_CANCELLED
            self.engine.cancel(workflow, reason)
        else:
            reason = {
                "context_limit_exceeded": StopReason.CONTEXT_LIMIT,
                "step_limit_exceeded": StopReason.STEP_LIMIT,
                "output_validation_failed": StopReason.OUTPUT_INVALID,
                "invalid_model_response": StopReason.OUTPUT_INVALID,
                "incomplete_model_response": StopReason.OUTPUT_INVALID,
                "internal_error": StopReason.INTERNAL_ERROR,
            }.get(error.code, StopReason.MODEL_ERROR)
            self.engine.fail(workflow, reason)
        operation.error = (error.code, error.message, error.status_code)
        operation.workflow = workflow.model_copy(deep=True)
        record.session.revision += 1
        emit(record, "run_stopped", request_id, (perf_counter() - started) * 1000)

    async def start_demo(self, session_id: UUID) -> Session:
        record = self.sessions.require(session_id)
        async with record.lock:
            self._active(record)
            record.session.workflow = self.engine.start(
                record.session.workflow, "demo", "prepare_demo"
            )
            emit(record, "run_started")
            try:
                # 通用 JSON 校验的实际调用点；此示例不使用模型或心理规则。
                parse_structured_output(
                    '{"title":"流程机制演示","items":["等待用户确认后继续"]}',
                    DemoDraft,
                )
                self.engine.advance(record.session.workflow, "review_demo")
                self.engine.wait(record.session.workflow)
            except RuntimeFailure as error:
                reason = StopReason.STEP_LIMIT if error.code == "step_limit_exceeded" else StopReason.OUTPUT_INVALID
                self.engine.fail(record.session.workflow, reason)
                record.session.revision += 1
                emit(record, "run_stopped")
                raise
            record.session.revision += 1
            emit(record, "confirmation_required")
            return record.session.model_copy(deep=True)

    async def confirm(self, session_id: UUID, run_id: UUID, token: UUID) -> Session:
        record = self.sessions.require(session_id)
        async with record.lock:
            self._active(record)
            self.engine.resume(record.session.workflow, run_id, token)
            emit(record, "confirmation_accepted")
            try:
                self.engine.advance(record.session.workflow, "complete_demo")
                self.engine.finish(record.session.workflow)
            except RuntimeFailure:
                self.engine.fail(record.session.workflow, StopReason.STEP_LIMIT)
                record.session.revision += 1
                emit(record, "run_stopped")
                raise
            record.session.revision += 1
            emit(record, "run_completed")
            return record.session.model_copy(deep=True)

    @staticmethod
    def _signal(record: SessionRecord, reason: StopReason) -> None:
        workflow = record.session.workflow
        if workflow.kind == "chat" and workflow.phase == WorkflowPhase.RUNNING:
            record.cancel_reason = record.cancel_reason or reason
            if record.active_task is not None and not record.active_task.done():
                record.active_task.cancel()

    async def cancel(self, session_id: UUID, run_id: UUID) -> Session:
        record = self.sessions.require(session_id)
        if record.session.workflow.run_id != run_id:
            raise RuntimeFailure("stale_run", "该流程已失效，请查看当前流程。")
        self._signal(record, StopReason.USER_CANCELLED)
        async with record.lock:
            workflow = record.session.workflow
            if workflow.run_id != run_id:
                raise RuntimeFailure("stale_run", "该流程已失效。")
            if workflow.phase == WorkflowPhase.CANCELLED:
                return record.session.model_copy(deep=True)
            self.engine.cancel(workflow, StopReason.USER_CANCELLED)
            record.session.revision += 1
            emit(record, "run_cancelled")
            return record.session.model_copy(deep=True)

    async def end_session(self, session_id: UUID) -> Session:
        record = self.sessions.require(session_id)
        record.lifecycle_pending += 1
        self._signal(record, StopReason.SESSION_ENDED)
        try:
            async with record.lock:
                session = record.session
                if session.status == SessionStatus.ENDED:
                    return session.model_copy(deep=True)
                if session.workflow.phase in (
                    WorkflowPhase.RUNNING, WorkflowPhase.WAITING_CONFIRMATION
                ):
                    self.engine.cancel(session.workflow, StopReason.SESSION_ENDED)
                session.status = SessionStatus.ENDED
                session.revision += 1
                emit(record, "session_ended")
                return session.model_copy(deep=True)
        finally:
            record.lifecycle_pending -= 1

    async def clear_history(self, session_id: UUID) -> Session:
        record = self.sessions.require(session_id)
        record.lifecycle_pending += 1
        self._signal(record, StopReason.HISTORY_CLEARED)
        try:
            async with record.lock:
                session = record.session
                session.messages.clear()
                session.state = SessionState.INITIAL
                session.workflow = WorkflowSnapshot()
                session.revision += 1
                # 移除回复内容，保留编号墓碑，避免旧请求重新写入。
                record.drafts.clear()
                record.turn_sources.clear()
                record.last_context = None
                for operation in [*record.operations.values(), *record.draft_operations.values()]:
                    operation.result = None
                    operation.workflow = None
                    operation.error = ("request_cleared", "该请求对应的内容已清空。", 409)
                record.events.clear()
                emit(record, "history_cleared")
                return session.model_copy(deep=True)
        finally:
            record.lifecycle_pending -= 1
