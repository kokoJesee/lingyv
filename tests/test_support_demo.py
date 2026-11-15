import asyncio
from fastapi.testclient import TestClient
from app import main
from app.llm import client as llm
from app.config.settings import Settings
from app.psychology.demo import ScriptedSupportModel
from app.schemas.message import SendMessageRequest
from app.services.session_service import SessionService
from app.agent.orchestrator import AgentOrchestrator


def test_explicit_server_demo_uses_real_runtime_and_never_live_transport(monkeypatch):
    monkeypatch.setenv('PSYCHOLOGY_ENABLED', 'true')
    monkeypatch.setenv('PSYCHOLOGY_DEMO_MODE', 'true')
    monkeypatch.setattr(main, 'session_service', SessionService())
    def forbidden(*args, **kwargs): raise AssertionError('live model not allowed')
    monkeypatch.setattr(llm, 'DeepSeekClient', forbidden)
    with TestClient(main.app) as client:
        health = client.get('/health').json()['runtime']
        assert health['psychology_enabled'] and health['psychology_demo_mode']
        sid = client.post('/sessions').json()['session_id']
        reply = client.post(f'/sessions/{sid}/messages', json={'content':'虚构：最近旅行'}).json()
        assert '固定脚本演示' in reply['reply']
        assert reply['psychology']['result']['route'] == 'routine_support'
        assert reply['review_status'] == 'unreviewed'
        assert len(client.get(f'/sessions/{sid}').json()['messages']) == 2


def test_model_simulation_is_opt_in(monkeypatch):
    monkeypatch.setenv('PSYCHOLOGY_ENABLED', 'true')
    monkeypatch.setenv('PSYCHOLOGY_DEMO_MODE', 'false')
    assert isinstance(llm.get_chat_model(), llm.DeepSeekClient)


def test_running_request_identity_clears_after_success_and_cancel():
    async def scenario():
        sessions=SessionService(); sid=sessions.create().session_id
        started=asyncio.Event(); finish=asyncio.Event()
        class Delayed(ScriptedSupportModel):
            async def generate_structured(self, **kwargs):
                started.set(); await finish.wait()
                return await super().generate_structured(**kwargs)
        agent=AgentOrchestrator(sessions,Settings(psychology_enabled=True))
        request=SendMessageRequest(content='虚构')
        task=asyncio.create_task(agent.handle_message(sid, request, Delayed()))
        await started.wait()
        assert sessions.require(sid).active_request_id == request.request_id
        finish.set(); await task
        assert sessions.require(sid).active_request_id is None
        started.clear(); finish.clear()
        task=asyncio.create_task(agent.handle_message(sid, SendMessageRequest(content='虚构2'), Delayed()))
        await started.wait()
        await agent.cancel(sid, sessions.get(sid).workflow.run_id)
        await asyncio.gather(task, return_exceptions=True)
        assert sessions.require(sid).active_request_id is None
    asyncio.run(scenario())


async def _confirmed_understanding(sessions, sid, text):
    from app.schemas.understanding_service import AddUserReport, ProposeUnderstanding, ConfirmUnderstanding
    from app.services.understanding_service import UnderstandingService
    service = UnderstandingService(sessions)
    report = await service.add_report(sid, AddUserReport(content=text))
    proposed = await service.propose(sid, ProposeUnderstanding(
        interpretation=text, origin='user_report',
        evidence_refs=[dict(kind='user_report', source_id=report.report_id, revision=1)],
    ))
    return await service.confirm(sid, proposed.understanding.understanding_id, ConfirmUnderstanding(
        expected_revision=proposed.understanding.revision, confirmation_token=proposed.confirmation_token))


def test_demo_prioritizes_latest_understanding_before_appended_legacy_drafts():
    async def scenario():
        from app.schemas.draft import CreateDraftRequest, DraftContent, ConfirmDraftRequest
        from app.services.draft_service import DraftService
        sessions = SessionService(); sid = sessions.create().session_id
        await _confirmed_understanding(sessions, sid, '较早确认的解释')
        newest = await _confirmed_understanding(sessions, sid, '最近确认的是旅行安排')
        drafts = DraftService(sessions)
        legacy = await drafts.create(sid, CreateDraftRequest(content=DraftContent(title='通用草稿不是优先理解', items=['旧接口演示'])))
        await drafts.confirm(sid, legacy.draft_id, ConfirmDraftRequest(
            expected_revision=legacy.revision, confirmation_token=legacy.confirmation_token))
        response = await AgentOrchestrator(sessions, Settings(psychology_enabled=True)).handle_message(
            sid, SendMessageRequest(content='看看当前理解'), ScriptedSupportModel())
        assert newest.understanding.interpretation in response.reply
        assert '较早确认的解释' not in response.reply and '通用草稿不是优先理解' not in response.reply
        selected = response.psychology.result.evidence_refs[-1]
        assert next(s for s in response.context_manifest.sources if s.source_id == selected).evidence_type == 'confirmed_memory'
    asyncio.run(scenario())


def test_demo_updated_existing_understanding_moves_ahead_of_newer_created_entry():
    async def scenario():
        from app.schemas.understanding_service import EditUnderstanding, ConfirmUnderstanding
        from app.services.understanding_service import UnderstandingService
        sessions = SessionService(); sid = sessions.create().session_id
        first = await _confirmed_understanding(sessions, sid, '最初解释')
        await _confirmed_understanding(sessions, sid, '之后建立的另一解释')
        service = UnderstandingService(sessions)
        edited = await service.edit(sid, first.understanding.understanding_id, EditUnderstanding(
            expected_revision=first.understanding.revision,
            interpretation='用户刚刚修正的旅行理解', origin='user_report',
            evidence_refs=first.understanding.evidence_refs))
        await service.confirm(sid, first.understanding.understanding_id, ConfirmUnderstanding(
            expected_revision=edited.understanding.revision, confirmation_token=edited.confirmation_token))
        response = await AgentOrchestrator(sessions, Settings(psychology_enabled=True)).handle_message(
            sid, SendMessageRequest(content='看看最新确认'), ScriptedSupportModel())
        assert '用户刚刚修正的旅行理解' in response.reply
        assert '之后建立的另一解释' not in response.reply
    asyncio.run(scenario())


def test_demo_legacy_only_respects_explicit_input_order_without_inventing_timestamps():
    import json
    from uuid import uuid4
    from app.schemas.psychology import PsychologyRequest
    request = PsychologyRequest(
        schema_version='0.1.0', session_id=uuid4(), request_id=uuid4(), session_revision=0,
        current_message=dict(source_id='current_1', content='虚构输入'), history=[],
        records=[dict(source_id='record_0001', evidence_type='confirmed_memory', content='首位通用草稿'),
                 dict(source_id='record_0002', evidence_type='confirmed_memory', content='末位通用草稿')])
    result = json.loads(asyncio.run(ScriptedSupportModel().generate_structured(
        system_prompt='fixture', request_json=request.model_dump_json())))
    assert '首位通用草稿' in result['reply'] and '末位通用草稿' not in result['reply']
    assert result['evidence_refs'][-1] == 'record_0001'
