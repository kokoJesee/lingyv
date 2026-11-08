import asyncio
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from app.agent.errors import RuntimeFailure
from app.config.settings import Settings
from app.llm.client import DeepSeekClient
from app.psychology.adapter import PsychologyAdapter
from app.psychology.inputs import PsychologyInputBuilder
from app.psychology.mock import FixedPsychologyModel, fixture_result
from app.psychology.policy import PsychologyFailure, support_entry
from app.schemas.psychology import PsychologyRequest, PsychologyResponse, PsychologyResult
from app.services.session_service import SessionService

ROUTES = ['routine_support', 'clarify_safety', 'safety_support', 'urgent_real_world_help']


def request():
    return PsychologyRequest(schema_version='0.1.0', session_id=uuid4(), request_id=uuid4(), session_revision=0,
        current_message={'source_id':'current_1', 'content':'虚构输入'}, history=[], records=[])


class RawModel:
    def __init__(self, text): self.text = text; self.calls = 0
    async def generate_structured(self, **kwargs):
        self.calls += 1
        return self.text


@pytest.mark.parametrize('route', ROUTES)
def test_four_routes_echo_server_ids_and_do_not_write_memory(route):
    async def run():
        req = request(); model = FixedPsychologyModel(fixture_result(route))
        response = await PsychologyAdapter(model).evaluate(req)
        assert response.result.route == route
        assert response.session_id == req.session_id and response.request_id == req.request_id
        assert response.result.memory_update is None and model.calls == 1
    asyncio.run(run())


@pytest.mark.parametrize('case', ['routine_support','clarify_safety'])
def test_original_contract_examples_still_parse(case):
    data = json.loads((Path(__file__).parents[1]/f'docs/examples/psychology/{case}.json').read_text(encoding='utf-8'))
    PsychologyRequest.model_validate(data['request'])
    PsychologyResponse.model_validate(data['expected_response'])


@pytest.mark.parametrize('patch', [
    {'route':'low_risk'}, {'reply':' '}, {'evidence_refs':[]}, {'evidence_refs':['current_1','current_1']},
    {'memory_update':{}}, {'follow_up_offer':''}, {'unexpected':True}, {'uncertainties':None},
])
def test_bad_output_is_failure_not_routine(patch):
    async def run():
        payload = fixture_result('routine_support').model_dump(); payload.update(patch)
        model = RawModel(json.dumps(payload))
        with pytest.raises(PsychologyFailure) as error:
            await PsychologyAdapter(model).evaluate(request())
        assert error.value.code == 'output_validation_failed'
        assert error.value.support_notice.automatic_contact is False
        assert model.calls == 1
    asyncio.run(run())


@pytest.mark.parametrize('text', ['{"route":"routine_support","route":"clarify_safety"}', '{"value": NaN}', 'not json'])
def test_invalid_json_rejected(text):
    async def run():
        with pytest.raises(PsychologyFailure) as error:
            await PsychologyAdapter(RawModel(text)).evaluate(request())
        assert error.value.code == 'output_validation_failed'
    asyncio.run(run())


def test_request_ids_unique_and_no_unknown_privileges():
    req = request().model_dump()
    req['history'] = [{'source_id':'current_1', 'role':'user', 'content':'x'}]
    with pytest.raises(ValidationError): PsychologyRequest.model_validate(req)
    req['history'] = []; req['session_revision'] = True
    with pytest.raises(ValidationError): PsychologyRequest.model_validate(req)
    req['session_revision'] = 0; req['execute'] = 'contact someone'
    with pytest.raises(ValidationError): PsychologyRequest.model_validate(req)


@pytest.mark.parametrize('route', ROUTES[1:])
@pytest.mark.parametrize('kind', ['device_observation', 'hypothesis', 'confirmed_memory', 'assistant'])
def test_safety_cannot_be_supported_only_by_non_user_sources(route, kind):
    async def run():
        req = request()
        if kind == 'assistant':
            from app.schemas.psychology import HistorySource
            req.history = [HistorySource(source_id='source_1', role='assistant', content='past assistant claim')]
        else:
            from app.schemas.psychology import RecordSource
            req.records = [RecordSource(source_id='source_1', evidence_type=kind, content='fixture')]
        result = fixture_result(route); result.evidence_refs = ['source_1']
        with pytest.raises(PsychologyFailure) as error:
            await PsychologyAdapter(FixedPsychologyModel(result)).evaluate(req)
        assert error.value.code == 'unsupported_safety_route'
    asyncio.run(run())


def test_unknown_source_rejected():
    async def run():
        result = fixture_result('routine_support'); result.evidence_refs=['fabricated_source']
        with pytest.raises(PsychologyFailure) as error:
            await PsychologyAdapter(FixedPsychologyModel(result)).evaluate(request())
        assert error.value.code == 'invalid_evidence_reference'
    asyncio.run(run())


@pytest.mark.parametrize('previous,candidate,blocked', [
    ('urgent_real_world_help','routine_support',True), ('urgent_real_world_help','safety_support',True),
    ('safety_support','clarify_safety',True), ('safety_support','urgent_real_world_help',False),
    ('clarify_safety','routine_support',False),
])
def test_transition_lock_for_unresolved_context(previous, candidate, blocked):
    async def run():
        adapter = PsychologyAdapter(FixedPsychologyModel(fixture_result(candidate)))
        if blocked:
            with pytest.raises(PsychologyFailure) as error:
                await adapter.evaluate(request(), previous_route=previous, unresolved=True)
            assert error.value.code == 'route_transition_unreviewed'
        else:
            assert (await adapter.evaluate(request(), previous_route=previous, unresolved=True)).result.route == candidate
    asyncio.run(run())


def test_no_hidden_retries_and_request_budget():
    async def run():
        model = FixedPsychologyModel(fixture_result('routine_support'))
        with pytest.raises(PsychologyFailure) as error:
            await PsychologyAdapter(model, Settings(runtime_max_context_chars=100)).evaluate(request())
        assert error.value.code == 'context_limit_exceeded' and model.calls == 0
        with pytest.raises(PsychologyFailure):
            await PsychologyAdapter(model, max_output_chars=5).evaluate(request())
        assert model.calls == 1
    asyncio.run(run())


@pytest.mark.parametrize('ignores_cancel',[False, True])
def test_timeout_discards_late_output(ignores_cancel):
    async def run():
        finished = asyncio.Event()
        class Model:
            async def generate_structured(self, **kwargs):
                try:
                    await asyncio.sleep(30)
                except asyncio.CancelledError:
                    if not ignores_cancel: raise
                    return fixture_result('routine_support').model_dump_json()
                finally:
                    finished.set()
        with pytest.raises(PsychologyFailure) as error:
            await PsychologyAdapter(Model(), Settings(llm_timeout_seconds=.01)).evaluate(request())
        assert error.value.code == 'model_timeout'
        await asyncio.wait_for(finished.wait(), 1)
    asyncio.run(run())


def test_caller_cancellation_is_not_a_fallback_reply():
    async def run():
        started = asyncio.Event(); finished = asyncio.Event()
        class Model:
            async def generate_structured(self, **kwargs):
                started.set()
                try: await asyncio.sleep(30)
                except asyncio.CancelledError: return fixture_result('routine_support').model_dump_json()
                finally: finished.set()
        task = asyncio.create_task(PsychologyAdapter(Model()).evaluate(request()))
        await started.wait(); task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        await asyncio.wait_for(finished.wait(), 1)
    asyncio.run(run())


def test_model_exception_is_sanitized():
    async def run():
        class Model:
            async def generate_structured(self, **kwargs): raise ValueError('secret user body/key')
        with pytest.raises(PsychologyFailure) as error: await PsychologyAdapter(Model()).evaluate(request())
        assert 'secret' not in str(error.value)
        assert error.value.code == 'model_unavailable'
    asyncio.run(run())


def test_live_client_transport_has_only_psychology_system_prompt():
    async def run():
        payloads = []
        def handler(req):
            payloads.append(json.loads(req.content))
            return httpx.Response(200, json={'choices':[{'finish_reason':'stop','message':{'content':fixture_result('routine_support').model_dump_json()}}]})
        model = DeepSeekClient(Settings(deepseek_api_key='fixture-not-a-real-key'), transport=httpx.MockTransport(handler))
        response = await PsychologyAdapter(model).evaluate(request())
        assert response.result.route == 'routine_support'
        assert len(payloads) == 1
        assert len(payloads[0]['messages']) == 2
        assert 'memory_update' in payloads[0]['messages'][0]['content']
        assert payloads[0]['messages'][1]['role'] == 'user'
    asyncio.run(run())


def test_input_builder_uses_only_model_authorized_data_and_sidecar():
    async def run():
        from app.wearables.mock import MockWearableAdapter
        from app.wearables.base import MockQuery
        from app.services.wearable_service import WearableService
        from app.schemas.wearable_service import SetWearableConsent
        from app.wearables.metric_registry import METRIC_REGISTRY
        sessions = SessionService(); sid = sessions.create().session_id
        data = WearableService(sessions); builder = PsychologyInputBuilder(sessions)
        await MockWearableAdapter().import_into(data, sid, MockQuery(as_of='2026-11-04T00:00:00+08:00', scenario_id='S02'), expected_revision=0, operation_id=uuid4())
        await data.set_consent(sid, SetWearableConsent(expected_revision=1, provider='mock', allowed_metrics=list(METRIC_REGISTRY), scopes=['local_analysis']))
        kwargs=dict(current_message='其实最近是旅行', history=[], as_of='2026-11-04T00:00:00+08:00')
        local = await builder.prepare(sid, uuid4(), **kwargs)
        assert not local.request.records
        await data.set_consent(sid, SetWearableConsent(expected_revision=2, provider='mock', allowed_metrics=list(METRIC_REGISTRY), scopes=['model_context']))
        prepared = await builder.prepare(sid, uuid4(), **kwargs)
        assert 0 < len(prepared.request.records) <= 6
        groups = {json.loads(r.content)['evidence_group'] for r in prepared.request.records}
        assert len(groups) <= 3
        encoded = prepared.request.model_dump_json()
        for key in ('expected_behavior','family_id','record_refs','scenario_id'): assert key not in encoded
        builder.validate_current(prepared)
        prepared.request.current_message.content = 'modified'
        with pytest.raises(RuntimeFailure): builder.validate_current(prepared)
        prepared = await builder.prepare(sid, uuid4(), **kwargs)
        await data.set_consent(sid, SetWearableConsent(expected_revision=3, provider='mock', allowed_metrics=[], scopes=[]))
        with pytest.raises(RuntimeFailure): builder.validate_current(prepared)
    asyncio.run(run())


def test_stale_result_discarded_after_model_returns():
    async def run():
        sessions = SessionService(); sid = sessions.create().session_id
        builder = PsychologyInputBuilder(sessions)
        prepared = await builder.prepare(sid, uuid4(), current_message='fixture', history=[], as_of='2026-11-04T00:00:00+08:00')
        class Model:
            async def generate_structured(self, **kwargs):
                sessions.require(sid).session.revision += 1
                return fixture_result('routine_support').model_dump_json()
        with pytest.raises(PsychologyFailure) as error:
            await PsychologyAdapter(Model()).evaluate(prepared.request, ensure_current=lambda: builder.validate_current(prepared))
        assert error.value.code == 'stale_psychology_input'
        assert not sessions.get(sid).messages
    asyncio.run(run())


def test_offline_entry_and_cli(capsys):
    assert support_entry().automatic_contact is False
    from app.psychology.__main__ import main
    assert main(['--route','urgent_real_world_help']) == 0
    output = json.loads(capsys.readouterr().out)
    assert output['review_status'] == 'unreviewed' and output['live_model_calls'] == 0
    assert output['response']['result']['route'] == 'urgent_real_world_help'


def test_prior_safety_route_defaults_to_unresolved():
    async def run():
        adapter = PsychologyAdapter(FixedPsychologyModel(fixture_result('routine_support')))
        with pytest.raises(PsychologyFailure) as error:
            await adapter.evaluate(request(), previous_route='urgent_real_world_help')
        assert error.value.code == 'route_transition_unreviewed'
    asyncio.run(run())


def test_model_must_not_supply_outer_ids_or_partial_results():
    async def run():
        req = request()
        outer = PsychologyResponse(schema_version='0.1.0', session_id=uuid4(), request_id=req.request_id,
            session_revision=0, result=fixture_result('routine_support'))
        for text in [outer.model_dump_json(), '{"route":"routine_support","reply":"hi"}']:
            with pytest.raises(PsychologyFailure) as error:
                await PsychologyAdapter(RawModel(text)).evaluate(req)
            assert error.value.code == 'output_validation_failed'
    asyncio.run(run())


def test_builder_only_includes_current_confirmed_understanding():
    async def run():
        from app.services.understanding_service import UnderstandingService
        from app.schemas.understanding_service import AddUserReport, ProposeUnderstanding, EditUnderstanding, ConfirmUnderstanding
        sessions = SessionService(); sid = sessions.create().session_id
        understanding = UnderstandingService(sessions); builder = PsychologyInputBuilder(sessions)
        report = await understanding.add_report(sid, AddUserReport(content='我在备考'))
        refs = [dict(kind='user_report', source_id=report.report_id, revision=1)]
        view = await understanding.propose(sid, ProposeUnderstanding(interpretation='与备考有关', origin='user_report', evidence_refs=refs))
        kwargs = dict(current_message='其实是旅行', history=[], as_of='2026-11-04T00:00:00+08:00')
        assert not (await builder.prepare(sid, uuid4(), **kwargs)).request.records
        await understanding.confirm(sid, view.understanding.understanding_id, ConfirmUnderstanding(expected_revision=1, confirmation_token=view.confirmation_token))
        old = await builder.prepare(sid, uuid4(), **kwargs)
        assert '备考' in old.request.records[0].content
        corrected = await understanding.add_report(sid, AddUserReport(content='是旅行'))
        edit = await understanding.edit(sid, view.understanding.understanding_id, EditUnderstanding(expected_revision=2,
            interpretation='与旅行有关', origin='user_report', evidence_refs=[dict(kind='user_report', source_id=corrected.report_id, revision=1)]))
        with pytest.raises(RuntimeFailure): builder.validate_current(old)
        assert not (await builder.prepare(sid, uuid4(), **kwargs)).request.records
        await understanding.confirm(sid, view.understanding.understanding_id, ConfirmUnderstanding(expected_revision=3, confirmation_token=edit.confirmation_token))
        new = await builder.prepare(sid, uuid4(), **kwargs)
        assert '旅行' in new.request.records[0].content and '备考' not in new.request.records[0].content
        assert 'confirmation_token' not in new.request.model_dump_json()
    asyncio.run(run())


def test_result_schema_export_and_standalone_entry():
    path = Path(__file__).parents[1]/'docs/schemas/psychology-result.schema.json'
    assert json.loads(path.read_text(encoding='utf-8')) == PsychologyResult.model_json_schema()
    assert '未能完成' not in support_entry().message
    assert '未能完成' in PsychologyFailure('fixture_error','fixture').support_notice.message
