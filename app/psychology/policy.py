"""Draft engineering constraints, not validated clinical classification."""
from dataclasses import dataclass

from app.agent.errors import RuntimeFailure
from app.schemas.psychology import PsychologyRequest, PsychologyResult, Route

POLICY_VERSION = 'psychology_policy.draft.v1'
REVIEW_STATUS = 'unreviewed'


@dataclass(frozen=True)
class SupportNotice:
    review_status: str = REVIEW_STATUS
    message: str = '你可以随时选择联系信任的人或查找所在地的现实支持。'
    actions: tuple[str, ...] = ('联系信任的人', '查找所在地现实支持')
    automatic_contact: bool = False


def support_entry() -> SupportNotice:
    """Always available without a device, model call, form or draft confirmation."""
    return SupportNotice()


class PsychologyFailure(RuntimeFailure):
    def __init__(self, code, message, status_code=502):
        super().__init__(code, message, status_code)
        self.support_notice = SupportNotice(message='本次自动支持未能完成。你仍可选择联系信任的人或查找所在地的现实支持。')


def validate_policy(request: PsychologyRequest, result: PsychologyResult, *, previous_route: Route | None = None,
                    unresolved: bool = False):
    source_ids = {request.current_message.source_id, *(h.source_id for h in request.history), *(r.source_id for r in request.records)}
    if not set(result.evidence_refs).issubset(source_ids):
        raise PsychologyFailure('invalid_evidence_reference', '心理结果引用了未提供的来源。')
    human_sources = {request.current_message.source_id}
    human_sources.update(h.source_id for h in request.history if h.role == 'user')
    human_sources.update(r.source_id for r in request.records if r.evidence_type == 'user_report')
    if result.route != 'routine_support' and not human_sources.intersection(result.evidence_refs):
        raise PsychologyFailure('unsupported_safety_route', '安全路线不能仅由设备、假设或助手内容触发。')
    # No claim that these conservative locks establish resolution. Expert-reviewed exit rules are pending.
    if unresolved and previous_route == 'urgent_real_world_help' and result.route != previous_route:
        raise PsychologyFailure('route_transition_unreviewed', '未解决的及时现实帮助情境不能自动转为其他路线。')
    if unresolved and previous_route == 'safety_support' and result.route not in ('safety_support', 'urgent_real_world_help'):
        raise PsychologyFailure('route_transition_unreviewed', '未解决的安全支持情境不能自动转为普通互动。')
