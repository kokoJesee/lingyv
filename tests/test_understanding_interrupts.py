"""Focused module-7 cancellation seam: only valid changes interrupt a chat."""
import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.agent.errors import RuntimeFailure
from app.agent.orchestrator import AgentOrchestrator
from app.schemas.message import SendMessageRequest
from app.schemas.understanding_service import AddUserReport, ProposeUnderstanding, EditUnderstanding, DecideUnderstanding
from app.schemas.wearable_service import ImportWearables, SetWearableConsent
from app.services.session_service import SessionService
from app.services.understanding_service import UnderstandingService
from app.services.wearable_service import WearableService


class BlockingModel:
    def __init__(self, ignores_cancel=True):
        self.started = asyncio.Event()
        self.cancelled = False
        self.ignores_cancel = ignores_cancel

    async def generate_reply(self, messages):
        self.started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            self.cancelled = True
            if self.ignores_cancel:
                return 'late reply which must not commit'
            raise


async def setup():
    sessions = SessionService(); sid = sessions.create().session_id
    service = UnderstandingService(sessions)
    report = await service.add_report(sid, AddUserReport(content='作息变化'))
    refs = [dict(kind='user_report', source_id=report.report_id, revision=1)]
    proposed = await service.propose(sid, ProposeUnderstanding(interpretation='备考假设', origin='agent_hypothesis', evidence_refs=refs))
    return sessions, sid, service, report, refs, proposed


async def start_chat(sessions, sid, ignores_cancel=True):
    runtime = AgentOrchestrator(sessions)
    model = BlockingModel(ignores_cancel)
    task = asyncio.create_task(runtime.handle_message(sid, SendMessageRequest(content='fixture'), model))
    await asyncio.wait_for(model.started.wait(), 1)
    return runtime, model, task


@pytest.mark.parametrize('action', ['edit', 'reject', 'delete_report'])
@pytest.mark.parametrize('ignores_cancel', [False, True])
def test_valid_change_interrupts_and_discards_late_result(action, ignores_cancel):
    async def run():
        sessions, sid, service, report, refs, proposed = await setup()
        _, model, task = await start_chat(sessions, sid, ignores_cancel)
        identity = proposed.understanding.understanding_id
        if action == 'edit':
            changed = await service.edit(sid, identity, EditUnderstanding(expected_revision=1,
                interpretation='旅行解释', origin='agent_hypothesis', evidence_refs=refs))
            assert changed.understanding.interpretation == '旅行解释'
        elif action == 'reject':
            changed = await service.reject(sid, identity, DecideUnderstanding(expected_revision=1))
            assert changed.understanding.status == 'rejected'
        else:
            await service.delete_report(sid, report.report_id, DecideUnderstanding(expected_revision=1))
            assert service.get(sid, identity).understanding.status == 'invalidated'
        with pytest.raises(RuntimeFailure) as error:
            await task
        assert error.value.code == 'run_cancelled'
        assert model.cancelled and not sessions.get(sid).messages
        assert sessions.require(sid).lifecycle_pending == 0
    asyncio.run(run())


@pytest.mark.parametrize('problem', ['stale', 'missing', 'invalid_source', 'no_change'])
def test_invalid_change_does_not_cancel_active_task(problem):
    async def run():
        sessions, sid, service, _, refs, proposed = await setup()
        runtime, model, task = await start_chat(sessions, sid)
        identity = proposed.understanding.understanding_id
        change = EditUnderstanding(expected_revision=1, interpretation='旅行解释', origin='agent_hypothesis', evidence_refs=refs)
        if problem == 'stale': change.expected_revision = 2
        if problem == 'missing': identity = str(uuid4())
        if problem == 'invalid_source': change.evidence_refs[0].source_id = 'missing-source'
        if problem == 'no_change': change.interpretation = proposed.understanding.interpretation
        with pytest.raises(RuntimeFailure): await service.edit(sid, identity, change)
        assert not model.cancelled and not task.done()
        assert sessions.require(sid).lifecycle_pending == 0
        await runtime.cancel(sid, sessions.get(sid).workflow.run_id)
        with pytest.raises(RuntimeFailure): await task
    asyncio.run(run())


def test_old_operation_replay_does_not_cancel_new_run():
    async def run():
        sessions, sid, service, _, refs, proposed = await setup()
        change = EditUnderstanding(expected_revision=1, interpretation='旅行解释', origin='agent_hypothesis', evidence_refs=refs)
        identity = proposed.understanding.understanding_id
        changed = await service.edit(sid, identity, change)
        runtime, model, task = await start_chat(sessions, sid)
        assert await service.edit(sid, identity, change) == changed
        assert not model.cancelled and not task.done()
        await runtime.cancel(sid, sessions.get(sid).workflow.run_id)
        with pytest.raises(RuntimeFailure): await task
    asyncio.run(run())


def test_interrupting_edit_revalidates_wearable_reference_under_own_barrier():
    async def run():
        sessions, sid, service, _, _, _ = await setup()
        data = WearableService(sessions)
        payload = json.loads((Path(__file__).parents[1]/'examples/wearable/batch.json').read_text())
        daily = payload['daily'][0]
        daily['metrics'] = [m for m in daily['metrics'] if m['metric_id'] == 'steps']
        await data.import_records(sid, ImportWearables(expected_revision=0, batch={'daily':[daily]}))
        await data.set_consent(sid, SetWearableConsent(expected_revision=1, provider='mock', allowed_metrics=['steps'], scopes=['local_analysis']))
        refs = [dict(kind='wearable_record', source_id=daily['record_id'], revision=1)]
        proposed = await service.propose(sid, ProposeUnderstanding(interpretation='旧解释', origin='agent_hypothesis', evidence_refs=refs))
        _, _, task = await start_chat(sessions, sid)
        edited = await service.edit(sid, proposed.understanding.understanding_id, EditUnderstanding(
            expected_revision=1, interpretation='新解释', origin='agent_hypothesis', evidence_refs=refs))
        assert edited.understanding.revision == 2
        assert sessions.require(sid).lifecycle_pending == 0
        with pytest.raises(RuntimeFailure): await task
    asyncio.run(run())


def test_waiting_demo_and_manual_lock_still_reject_changes():
    async def run():
        sessions, sid, service, _, refs, proposed = await setup()
        change = EditUnderstanding(expected_revision=1, interpretation='旅行解释', origin='agent_hypothesis', evidence_refs=refs)
        identity = proposed.understanding.understanding_id
        async with sessions.require(sid).lock:
            with pytest.raises(RuntimeFailure) as error: await service.edit(sid, identity, change)
            assert error.value.code == 'session_busy'
        await AgentOrchestrator(sessions).start_demo(sid)
        with pytest.raises(RuntimeFailure) as error: await service.edit(sid, identity, change)
        assert error.value.code == 'session_busy'
        assert sessions.get(sid).workflow.phase.value == 'WAITING_CONFIRMATION'
    asyncio.run(run())


def test_cancelled_waiting_correction_releases_barrier_without_edit():
    async def run():
        sessions, sid, service, _, refs, proposed = await setup()
        started = asyncio.Event(); interrupted = asyncio.Event(); release = asyncio.Event()
        class SlowCancellationModel:
            async def generate_reply(self, messages):
                started.set()
                try: await asyncio.sleep(30)
                except asyncio.CancelledError:
                    interrupted.set()
                    await release.wait()
                    return 'late'
        runtime = AgentOrchestrator(sessions)
        chat = asyncio.create_task(runtime.handle_message(sid, SendMessageRequest(content='fixture'), SlowCancellationModel()))
        await started.wait()
        edit = asyncio.create_task(service.edit(sid, proposed.understanding.understanding_id, EditUnderstanding(
            expected_revision=1, interpretation='新解释', origin='agent_hypothesis', evidence_refs=refs)))
        await interrupted.wait()
        assert sessions.require(sid).lifecycle_pending == 1
        edit.cancel()
        with pytest.raises(asyncio.CancelledError): await edit
        assert sessions.require(sid).lifecycle_pending == 0
        assert service.get(sid, proposed.understanding.understanding_id).understanding.revision == 1
        release.set()
        with pytest.raises(RuntimeFailure): await chat
    asyncio.run(run())
