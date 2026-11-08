import asyncio
import json
from uuid import UUID, uuid4

import pytest

from app.agent.errors import RuntimeFailure
from app.agent.orchestrator import AgentOrchestrator
from app.config.settings import Settings
from app.psychology.mock import fixture_result
from app.schemas.message import SendMessageRequest
from app.schemas.understanding_service import AddUserReport, ProposeUnderstanding, ConfirmUnderstanding, EditUnderstanding
from app.schemas.wearable_service import SetWearableConsent
from app.services.session_service import SessionService
from app.services.understanding_service import UnderstandingService
from app.services.wearable_service import WearableService
from app.wearables.base import MockQuery
from app.wearables.mock import MockWearableAdapter
from app.wearables.metric_registry import METRIC_REGISTRY


def setup(**kwargs):
    sessions = SessionService(); sid = sessions.create().session_id
    return sessions, sid, AgentOrchestrator(sessions, Settings(psychology_enabled=True, **kwargs))


class Model:
    def __init__(self, route='routine_support', text=None):
        self.route, self.text, self.requests = route, text, []
    async def generate_structured(self, *, system_prompt, request_json):
        self.requests.append(json.loads(request_json))
        result = fixture_result(self.route)
        result.reply = self.text or f'fixture reply {len(self.requests)}'
        return result.model_dump_json()


async def confirmed(sessions, sid):
    service = UnderstandingService(sessions)
    report = await service.add_report(sid, AddUserReport(content='最近在备考'))
    view = await service.propose(sid, ProposeUnderstanding(interpretation='作息变化与备考有关', origin='user_report',
        evidence_refs=[dict(kind='user_report', source_id=report.report_id, revision=1)]))
    return await service.confirm(sid, view.understanding.understanding_id, ConfirmUnderstanding(expected_revision=1, confirmation_token=view.confirmation_token))


async def device_data(sessions, sid, scopes=('model_context',)):
    service = WearableService(sessions)
    # Actual current complete days make integration independent of a fixed wall date.
    from datetime import datetime, timezone
    await MockWearableAdapter().import_into(service, sid, MockQuery(as_of=datetime.now(timezone.utc), scenario_id='S02'), expected_revision=0, operation_id=uuid4())
    await service.set_consent(sid, SetWearableConsent(expected_revision=1, provider='mock',
        allowed_metrics=list(METRIC_REGISTRY), scopes=list(scopes)))
    return service


def test_single_turn_and_concurrent_idempotency():
    async def run():
        sessions, sid, agent = setup()
        model = Model(); req = SendMessageRequest(content='想聊聊')
        first, second = await asyncio.gather(agent.handle_message(sid,req,model),agent.handle_message(sid,req,model))
        assert first == second and len(model.requests) == 1
        assert first.psychology.result.memory_update is None and first.review_status == 'unreviewed'
        assert first.workflow.phase.value == 'COMPLETED' and first.workflow.step_count == 4
        assert len(sessions.get(sid).messages) == 2
        assert sessions.require(sid).support_route == 'routine_support'
    asyncio.run(run())


def test_inline_correction_suppresses_old_understanding_before_confirmation():
    async def run():
        sessions, sid, agent = setup(); view = await confirmed(sessions,sid)
        model = Model(text='旧回复引用备考')
        old_req = SendMessageRequest(content='分析一下')
        await agent.handle_message(sid,old_req,model)
        assert any('备考' in r['content'] for r in model.requests[-1]['records'])
        correction = SendMessageRequest(content='其实是旅行，不是备考', correction_of=dict(
            understanding_id=view.understanding.understanding_id, expected_revision=2))
        model.text = '已采用旅行这个纠正'
        corrected = await agent.handle_message(sid,correction,model)
        payload = model.requests[-1]
        assert payload['current_message']['content'] == correction.content
        assert not payload['records']
        assert all('旧回复' not in h['content'] for h in payload['history'])
        assert payload['history'][0]['content'] == '分析一下'  # User history is retained.
        assert corrected.context_manifest.excluded_history_indices == [1]
        current = UnderstandingService(sessions).get(sid, view.understanding.understanding_id)
        assert current.understanding.status == 'proposed' and current.understanding.revision == 3
        assert not UnderstandingService(sessions).current(sid)
        assert await agent.handle_message(sid,correction,model) == corrected
        with pytest.raises(RuntimeFailure) as error: await agent.handle_message(sid,old_req,model)
        assert error.value.code == 'source_withdrawn'
        await agent.handle_message(sid,SendMessageRequest(content='继续'),model)
        assert not any('旧回复引用备考' in h['content'] for h in model.requests[-1]['history'])
        assert any(h['content'] == correction.content for h in model.requests[-1]['history'])
    asyncio.run(run())


def test_external_edit_changes_next_turn_and_blocks_cached_reply():
    async def run():
        sessions,sid,agent=setup(); view=await confirmed(sessions,sid); model=Model()
        req=SendMessageRequest(content='hello'); await agent.handle_message(sid,req,model)
        service=UnderstandingService(sessions)
        report=await service.add_report(sid,AddUserReport(content='旅行'))
        edited=await service.edit(sid,view.understanding.understanding_id,EditUnderstanding(expected_revision=2,
            interpretation='旅行',origin='user_report',evidence_refs=[dict(kind='user_report',source_id=report.report_id,revision=1)]))
        await service.confirm(sid,view.understanding.understanding_id,ConfirmUnderstanding(expected_revision=3,confirmation_token=edited.confirmation_token))
        with pytest.raises(RuntimeFailure): await agent.handle_message(sid,req,model)
        await agent.handle_message(sid,SendMessageRequest(content='现在呢'),model)
        assert all('备考' not in r['content'] for r in model.requests[-1]['records'])
        assert any('旅行' in r['content'] for r in model.requests[-1]['records'])
        assert model.requests[-1]['history'] == [{'source_id':'history_0001','content':'hello','role':'user'}]
    asyncio.run(run())


def test_revocation_removes_direct_and_inherited_history_and_cache():
    async def run():
        sessions,sid,agent=setup(); data=await device_data(sessions,sid); model=Model()
        first=SendMessageRequest(content='看下设备趋势'); a=await agent.handle_message(sid,first,model)
        assert a.context_manifest.business_dependencies.wearable_revision == 2
        # Simulate future selection excluding fresh device records: inherited dependencies still count.
        import app.agent.support_context as context
        original=context.PsychologyInputBuilder.prepare_locked
        def no_records(builder,*args,**kwargs):
            from dataclasses import replace
            import hashlib
            p=original(builder,*args,**kwargs)
            p.request.records=[]
            sources={k:v for k,v in p.source_manifest.items() if v['kind'] not in ('analysis','understanding')}
            return replace(p,source_manifest=sources,fingerprint=hashlib.sha256(p.request.model_dump_json().encode()).hexdigest())
        context.PsychologyInputBuilder.prepare_locked=no_records
        try:
            second=SendMessageRequest(content='继续说'); b=await agent.handle_message(sid,second,model)
        finally: context.PsychologyInputBuilder.prepare_locked=original
        assert b.context_manifest.business_dependencies.wearable_revision == 2
        await data.set_consent(sid,SetWearableConsent(expected_revision=2,provider='mock',allowed_metrics=[],scopes=[]))
        for req in (first,second):
            with pytest.raises(RuntimeFailure) as error: await agent.handle_message(sid,req,model)
            assert error.value.code == 'source_withdrawn'
        next_reply=await agent.handle_message(sid,SendMessageRequest(content='再聊聊'),model)
        assert next_reply.context_manifest.excluded_history_indices == [1,3]
        assert all(h['role']=='user' for h in model.requests[-1]['history'])
        assert not model.requests[-1]['records']
        assert len(model.requests)==3
    asyncio.run(run())


def test_local_only_does_not_reach_model():
    async def run():
        sessions,sid,agent=setup(); await device_data(sessions,sid,('local_analysis',)); model=Model()
        response=await agent.handle_message(sid,SendMessageRequest(content='hello'),model)
        assert not model.requests[0]['records']
        assert response.context_manifest.business_dependencies.wearable_revision is None
    asyncio.run(run())


@pytest.mark.parametrize('action',['cancel','clear','end','revoke','correct'])
@pytest.mark.parametrize('ignore_cancel',[False,True])
def test_interruptions_discard_late_result(action,ignore_cancel):
    async def run():
        sessions,sid,agent=setup(); data=await device_data(sessions,sid)
        view=await confirmed(sessions,sid)
        started=asyncio.Event(); finished=asyncio.Event()
        class Blocking(Model):
            async def generate_structured(self,**kwargs):
                started.set()
                try: await asyncio.sleep(30)
                except asyncio.CancelledError:
                    if ignore_cancel: return fixture_result('routine_support').model_dump_json()
                    raise
                finally: finished.set()
        req=SendMessageRequest(content='pending')
        task=asyncio.create_task(agent.handle_message(sid,req,Blocking()))
        await started.wait()
        if action=='cancel': await agent.cancel(sid,sessions.get(sid).workflow.run_id)
        elif action=='clear': await agent.clear_history(sid)
        elif action=='end': await agent.end_session(sid)
        elif action=='revoke': await data.set_consent(sid,SetWearableConsent(expected_revision=2,provider='mock',allowed_metrics=[],scopes=[]))
        else:
            service=UnderstandingService(sessions)
            refs=view.understanding.evidence_refs
            await service.edit(sid,view.understanding.understanding_id,EditUnderstanding(expected_revision=2,
                interpretation='更正的解释',origin='user_report',evidence_refs=refs))
        with pytest.raises(RuntimeFailure): await task
        await asyncio.wait_for(finished.wait(),1)
        record=sessions.require(sid)
        assert not record.session.messages and not record.business_turn_sources
        assert record.support_route is None and record.active_task is None and record.lifecycle_pending==0
    asyncio.run(run())


def test_failed_inline_correction_rolls_back_all_business_state():
    async def run():
        sessions,sid,agent=setup(); view=await confirmed(sessions,sid)
        before=len(sessions.require(sid).understandings.reports)
        class Bad:
            async def generate_structured(self,**kwargs): return 'broken json'
        req=SendMessageRequest(content='是旅行',correction_of=dict(understanding_id=view.understanding.understanding_id,expected_revision=2))
        with pytest.raises(RuntimeFailure): await agent.handle_message(sid,req,Bad())
        assert UnderstandingService(sessions).get(sid,view.understanding.understanding_id)==view
        record=sessions.require(sid)
        assert len(record.understandings.reports)==before and not record.session.messages
    asyncio.run(run())


def test_unresolved_route_failure_and_replay_do_not_reset_state():
    async def run():
        sessions,sid,agent=setup(); model=Model('urgent_real_world_help')
        first=SendMessageRequest(content='虚构的及时支持场景'); await agent.handle_message(sid,first,model)
        model.route='routine_support'; bad=SendMessageRequest(content='谢谢')
        for _ in range(2):
            with pytest.raises(RuntimeFailure) as error: await agent.handle_message(sid,bad,model)
            assert error.value.code=='route_transition_unreviewed'
            assert getattr(error.value,'support_notice',None)
        assert len(model.requests)==2 and len(sessions.get(sid).messages)==2
        assert sessions.require(sid).support_route=='urgent_real_world_help'
        await agent.handle_message(sid,first,model)
        assert sessions.require(sid).support_route=='urgent_real_world_help'
    asyncio.run(run())


def test_archive_v1_rejects_support_and_mode_cannot_downgrade(tmp_path):
    async def run():
        from app.services.archive_service import ArchiveService
        from app.services.archive_store import SQLiteArchiveStore
        from app.schemas.archive import SaveArchiveRequest
        sessions,sid,agent=setup(); req=SendMessageRequest(content='hello')
        await agent.handle_message(sid,req,Model())
        archives=ArchiveService(sessions,SQLiteArchiveStore(tmp_path/'archive.db'))
        with pytest.raises(RuntimeFailure) as error: await archives.save(sid,SaveArchiveRequest(expected_revision=sessions.get(sid).revision))
        assert error.value.code=='archive_business_consent_required'
        with pytest.raises(RuntimeFailure): await AgentOrchestrator(sessions).handle_message(sid,SendMessageRequest(content='fallback'),Model())
        with pytest.raises(RuntimeFailure): await agent.start_demo(sid)
    asyncio.run(run())


def test_api_full_path_and_identical_fault_notice(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    from app.llm.client import get_chat_model
    sessions=SessionService(); monkeypatch.setattr(main,'session_service',sessions)
    monkeypatch.setattr(main,'get_orchestrator',lambda:AgentOrchestrator(sessions,Settings(psychology_enabled=True)))
    model=Model(); main.app.dependency_overrides[get_chat_model]=lambda:model
    try:
        with TestClient(main.app) as client:
            sid=client.post('/sessions').json()['session_id']; base=f'/sessions/{sid}'
            report=client.post(base+'/reports',json={'content':'在备考'}).json()
            view=client.post(base+'/understandings',json={'interpretation':'备考','origin':'user_report',
                'evidence_refs':[{'kind':'user_report','source_id':report['report_id'],'revision':1}]}).json()
            identity=view['understanding']['understanding_id']
            assert client.post(base+f'/understandings/{identity}/confirm',json={'expected_revision':1,'confirmation_token':view['confirmation_token']}).status_code==200
            response=client.post(base+'/messages',json={'content':'想聊聊'}).json()
            assert response['review_status']=='unreviewed'
            assert response['psychology']['result']['route']=='routine_support'
            async def broken(**kwargs): model.requests.append({}); return '{}'
            model.generate_structured=broken
            request={'content':'再试','request_id':str(uuid4())}
            first=client.post(base+'/messages',json=request); second=client.post(base+'/messages',json=request)
            assert first.status_code==502 and first.json()==second.json()
            assert first.json()['support_notice']['automatic_contact'] is False
            assert len(model.requests)==2
            assert client.get('/support').status_code==200
    finally: main.app.dependency_overrides.pop(get_chat_model,None)


@pytest.mark.parametrize('settings,code', [({'runtime_max_steps':2},'step_limit_exceeded'),({'runtime_max_context_chars':100},'context_limit_exceeded')])
def test_failure_budgets_leave_messages_and_route_untouched(settings,code):
    async def run():
        sessions,sid,agent=setup(**settings); model=Model()
        with pytest.raises(RuntimeFailure) as error: await agent.handle_message(sid,SendMessageRequest(content='hello'),model)
        assert error.value.code==code
        assert not sessions.get(sid).messages and not sessions.require(sid).business_turn_sources
        assert sessions.require(sid).support_route is None
        if code=='context_limit_exceeded': assert not model.requests
    asyncio.run(run())


def test_end_to_end_cli_demo():
    from app.support_cli import demo
    result=asyncio.run(demo())
    assert result['old_replay_blocked']
    assert result['confirmed_understanding_uses_travel']
    assert result['device_records_after_revoke']==0
    assert result['live_model_calls']==0 and result['memory_update'] is None


def test_support_mode_cannot_reenable_blocking_demo_and_preempts_old_demo():
    async def run():
        sessions,sid,agent=setup()
        legacy=AgentOrchestrator(sessions,Settings(psychology_enabled=False))
        await legacy.start_demo(sid)
        assert sessions.get(sid).workflow.phase.value=='WAITING_CONFIRMATION'
        response=await agent.handle_message(sid,SendMessageRequest(content='我现在需要支持'),Model())
        assert response.workflow.phase.value=='COMPLETED'
        with pytest.raises(RuntimeFailure) as error: await legacy.start_demo(sid)
        assert error.value.code=='demo_disabled'
    asyncio.run(run())


def test_api_upload_limit_rejects_before_parsing(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    sessions=SessionService(); monkeypatch.setattr(main,'session_service',sessions)
    sid=sessions.create().session_id
    with TestClient(main.app) as client:
        response=client.post(f'/sessions/{sid}/wearables/import',content=b' '*(4*1024*1024+1),headers={'Content-Type':'application/json'})
        assert response.status_code==413
        assert not sessions.require(sid).wearables.records


def test_api_wearable_import_and_analysis(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    from app.wearables.mock_generator import generate
    sessions=SessionService(); monkeypatch.setattr(main,'session_service',sessions)
    sid=sessions.create().session_id; base=f'/sessions/{sid}'
    batch=generate(MockQuery(as_of='2026-11-05T00:00:00+08:00',include_intervals=False)).batch
    with TestClient(main.app) as client:
        imported=client.post(base+'/wearables/import',json={'expected_revision':0,'batch':batch.model_dump(mode='json')})
        assert imported.status_code==200
        assert client.post(base+'/wearables/consent',json={'expected_revision':1,'provider':'mock','allowed_metrics':list(METRIC_REGISTRY),'scopes':['local_analysis']}).status_code==200
        analysis=client.post(base+'/analysis',json={'as_of':'2026-11-05T00:00:00+08:00'})
        assert analysis.status_code==200 and analysis.json()['status']=='ready'
        model=client.post(base+'/analysis',json={'as_of':'2026-11-05T00:00:00+08:00','scope':'model_context'})
        assert model.json()['status']=='not_authorized'
