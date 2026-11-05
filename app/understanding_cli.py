"""Offline correction demo; no model, device, persistence or long-term memory."""
import asyncio
import json

from app.agent.errors import RuntimeFailure
from app.schemas.understanding_service import AddUserReport, ProposeUnderstanding, EditUnderstanding, ConfirmUnderstanding
from app.schemas.wearable import EvidenceReference
from app.services.session_service import SessionService
from app.services.understanding_service import UnderstandingService


async def demo():
    sessions = SessionService(); sid = sessions.create().session_id
    service = UnderstandingService(sessions)
    initial = await service.add_report(sid, AddUserReport(content='最近作息变晚了'))
    proposed = await service.propose(sid, ProposeUnderstanding(
        interpretation='可能与备考有关，待用户确认', origin='agent_hypothesis',
        evidence_refs=[dict(kind='user_report', source_id=initial.report_id, revision=1)]))
    identity = proposed.understanding.understanding_id
    old = await service.confirm(sid, identity, ConfirmUnderstanding(expected_revision=1, confirmation_token=proposed.confirmation_token))
    correction = await service.add_report(sid, AddUserReport(content='其实是旅行，不是备考'))
    edited = await service.edit(sid, identity, EditUnderstanding(expected_revision=2,
        interpretation='用户说明作息变化与旅行有关', origin='user_report',
        evidence_refs=[dict(kind='user_report', source_id=correction.report_id, revision=1)]))
    assert not service.current(sid)
    current = await service.confirm(sid, identity, ConfirmUnderstanding(expected_revision=3, confirmation_token=edited.confirmation_token))
    old_invalid = False
    try:
        service.validate_reference(sid, EvidenceReference(kind='understanding', source_id=identity, revision=old.understanding.revision))
    except RuntimeFailure:
        old_invalid = True
    return dict(is_simulated=True, old_revision=old.understanding.revision,
                current_revision=current.understanding.revision, old_reference_invalid=old_invalid,
                current_interpretations=[v.interpretation for v in service.current(sid)], memory_update=None)


def main():
    print(json.dumps(asyncio.run(demo()), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
