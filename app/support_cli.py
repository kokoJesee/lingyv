"""Offline end-to-end demo through the real orchestrator; no live model or device."""
import asyncio
from datetime import datetime, timezone
import json
from uuid import uuid4

from app.agent.errors import RuntimeFailure
from app.agent.orchestrator import AgentOrchestrator
from app.config.settings import Settings
from app.psychology.mock import fixture_result
from app.schemas.message import SendMessageRequest
from app.schemas.understanding_service import AddUserReport, ProposeUnderstanding, ConfirmUnderstanding
from app.schemas.wearable_service import SetWearableConsent
from app.services.session_service import SessionService
from app.services.understanding_service import UnderstandingService
from app.services.wearable_service import WearableService
from app.wearables.base import MockQuery
from app.wearables.mock import MockWearableAdapter
from app.wearables.metric_registry import METRIC_REGISTRY


class DemoModel:
    """Scripted demonstration of input wiring, not an evaluated reasoning model."""
    def __init__(self): self.requests=[]
    async def generate_structured(self, *, system_prompt, request_json):
        request=json.loads(request_json); self.requests.append(request)
        result=fixture_result('routine_support')
        result.reply='演示：已把当前消息作为优先输入。'+request['current_message']['content']
        return result.model_dump_json()


async def demo():
    sessions=SessionService(); sid=sessions.create().session_id
    agent=AgentOrchestrator(sessions,Settings(psychology_enabled=True))
    wearables=WearableService(sessions); understandings=UnderstandingService(sessions)
    await MockWearableAdapter().import_into(wearables,sid,MockQuery(as_of=datetime.now(timezone.utc),scenario_id='S02'),expected_revision=0,operation_id=uuid4())
    await wearables.set_consent(sid,SetWearableConsent(expected_revision=1,provider='mock',allowed_metrics=list(METRIC_REGISTRY),scopes=['model_context']))
    report=await understandings.add_report(sid,AddUserReport(content='我在备考'))
    proposed=await understandings.propose(sid,ProposeUnderstanding(interpretation='晚睡与备考有关',origin='user_report',
        evidence_refs=[dict(kind='user_report',source_id=report.report_id,revision=1)]))
    await understandings.confirm(sid,proposed.understanding.understanding_id,ConfirmUnderstanding(expected_revision=1,confirmation_token=proposed.confirmation_token))
    model=DemoModel(); old=SendMessageRequest(content='帮我看看变化')
    await agent.handle_message(sid,old,model)
    correction=SendMessageRequest(content='其实最近是旅行，不是备考',correction_of=dict(understanding_id=proposed.understanding.understanding_id,expected_revision=2))
    result=await agent.handle_message(sid,correction,model)
    edited=understandings.get(sid,proposed.understanding.understanding_id)
    await understandings.confirm(sid,edited.understanding.understanding_id,ConfirmUnderstanding(expected_revision=3,confirmation_token=edited.confirmation_token))
    await agent.handle_message(sid,SendMessageRequest(content='现在继续'),model)
    after_correction=model.requests[-1]
    await wearables.set_consent(sid,SetWearableConsent(expected_revision=2,provider='mock',allowed_metrics=[],scopes=[]))
    replay_blocked=False
    try: await agent.handle_message(sid,old,model)
    except RuntimeFailure as error: replay_blocked=error.code=='source_withdrawn'
    await agent.handle_message(sid,SendMessageRequest(content='不用设备数据了'),model)
    return dict(review_status='unreviewed',live_model_calls=0,fixture_calls=len(model.requests),
        correction_reply=result.reply,old_replay_blocked=replay_blocked,
        confirmed_understanding_uses_travel=any('旅行' in r['content'] for r in after_correction['records'] if r['evidence_type']=='confirmed_memory'),
        device_records_after_revoke=sum(r['evidence_type']=='device_observation' for r in model.requests[-1]['records']),
        excluded_old_replies=sessions.require(sid).last_context.excluded_history_indices,
        memory_update=result.psychology.result.memory_update)


def main():
    print(json.dumps(asyncio.run(demo()),ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__': raise SystemExit(main())
