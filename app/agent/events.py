import logging
from uuid import UUID

from app.schemas.events import RuntimeEvent
from app.schemas.workflow import WorkflowSnapshot
from app.services.session_service import SessionRecord

logger = logging.getLogger(__name__)


def emit(
    record: SessionRecord, event: str, request_id: UUID | None = None,
    elapsed_ms: float | None = None,
    workflow_snapshot: WorkflowSnapshot | None = None,
) -> RuntimeEvent:
    workflow = workflow_snapshot or record.session.workflow
    result = RuntimeEvent(
        event=event, request_id=request_id, run_id=workflow.run_id,
        phase=workflow.phase, step=workflow.step,
        stop_reason=workflow.stop_reason, elapsed_ms=elapsed_ms,
        message_count=len(record.session.messages),
    )
    record.events.append(result)
    logger.info(
        "event=%s | request_id=%s | run_id=%s | phase=%s | step=%s | "
        "reason=%s | elapsed_ms=%s | messages=%d",
        event, request_id, workflow.run_id, workflow.phase.value, workflow.step,
        workflow.stop_reason.value if workflow.stop_reason else None,
        round(elapsed_ms, 1) if elapsed_ms is not None else None,
        len(record.session.messages),
    )
    return result
