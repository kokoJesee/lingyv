from uuid import UUID, uuid4

from app.agent.errors import RuntimeFailure
from app.schemas.workflow import StopReason, WorkflowPhase, WorkflowSnapshot


class WorkflowEngine:
    def __init__(self, max_steps: int = 8):
        self.max_steps = max_steps

    def start(self, current: WorkflowSnapshot, kind: str, step: str) -> WorkflowSnapshot:
        if current.phase in (WorkflowPhase.RUNNING, WorkflowPhase.WAITING_CONFIRMATION):
            raise RuntimeFailure("workflow_busy", "当前流程尚未结束，请先确认或取消。")
        return WorkflowSnapshot(
            run_id=uuid4(), kind=kind, phase=WorkflowPhase.RUNNING,
            step=step, step_count=1,
        )

    def advance(self, workflow: WorkflowSnapshot, step: str) -> None:
        self._require(workflow, WorkflowPhase.RUNNING)
        if workflow.step_count >= self.max_steps:
            raise RuntimeFailure("step_limit_exceeded", "本次流程达到步骤上限。")
        workflow.step = step
        workflow.step_count += 1

    def wait(self, workflow: WorkflowSnapshot) -> None:
        self._require(workflow, WorkflowPhase.RUNNING)
        workflow.phase = WorkflowPhase.WAITING_CONFIRMATION
        workflow.stop_reason = StopReason.AWAITING_USER
        workflow.confirmation_token = uuid4()

    def resume(self, workflow: WorkflowSnapshot, run_id: UUID, token: UUID) -> None:
        self._require(workflow, WorkflowPhase.WAITING_CONFIRMATION)
        if workflow.run_id != run_id or workflow.confirmation_token != token:
            raise RuntimeFailure("stale_confirmation", "这份确认已失效，请查看当前流程。")
        workflow.phase = WorkflowPhase.RUNNING
        workflow.stop_reason = None
        workflow.confirmation_token = None

    def finish(self, workflow: WorkflowSnapshot) -> None:
        self._require(workflow, WorkflowPhase.RUNNING)
        workflow.phase = WorkflowPhase.COMPLETED
        workflow.stop_reason = StopReason.COMPLETED
        workflow.confirmation_token = None

    def cancel(self, workflow: WorkflowSnapshot, reason: StopReason) -> None:
        if workflow.phase not in (WorkflowPhase.RUNNING, WorkflowPhase.WAITING_CONFIRMATION):
            raise RuntimeFailure("invalid_transition", "当前流程不能取消。")
        workflow.phase = WorkflowPhase.CANCELLED
        workflow.stop_reason = reason
        workflow.confirmation_token = None

    def fail(self, workflow: WorkflowSnapshot, reason: StopReason) -> None:
        self._require(workflow, WorkflowPhase.RUNNING)
        workflow.phase = WorkflowPhase.FAILED
        workflow.stop_reason = reason
        workflow.confirmation_token = None

    @staticmethod
    def _require(workflow: WorkflowSnapshot, expected: WorkflowPhase) -> None:
        if workflow.phase != expected:
            raise RuntimeFailure(
                "invalid_transition", "当前流程状态不支持此操作。"
            )
