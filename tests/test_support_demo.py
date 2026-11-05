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
