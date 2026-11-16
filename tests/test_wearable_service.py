import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.agent.errors import RuntimeFailure
from app.agent.orchestrator import AgentOrchestrator
from app.config.settings import Settings
from app.schemas.message import SendMessageRequest
from app.schemas.wearable_service import ImportWearables, SetWearableConsent, DeleteWearableRecord
from app.services.session_service import SessionService
from app.services.wearable_service import WearableService


def setup():
    sessions = SessionService()
    sid = sessions.create().session_id
    return sessions, sid, WearableService(sessions)


def incoming(revision=0):
    batch = json.loads((Path(__file__).parents[1] / 'examples/wearable/batch.json').read_text(encoding='utf-8'))
    daily = batch['daily'][0]
    daily['metrics'] = [m for m in daily['metrics'] if m['metric_id'] == 'steps']
    return ImportWearables(expected_revision=revision, batch={'daily': [daily]})


def consent(revision, scopes=('local_analysis',), metrics=('steps',)):
    return SetWearableConsent(expected_revision=revision, provider='mock', allowed_metrics=list(metrics), scopes=list(scopes))


def fails(code, callable):
    with pytest.raises(RuntimeFailure) as error:
        callable()
    assert error.value.code == code


def test_import_consent_isolation_copy_and_revision():
    async def run():
        sessions, sid, service = setup()
        request = incoming()
        result = await service.import_records(sid, request)
        assert result == {'accepted': 1, 'unchanged': 0, 'invalid_metrics': 0, 'revision': 1}
        assert await service.import_records(sid, request) == result
        assert (await service.import_records(sid, incoming(1)))['unchanged'] == 1
        assert not service.snapshot(sid, 'local_analysis').records
        await service.set_consent(sid, consent(1))
        snap = service.snapshot(sid, 'local_analysis')
        assert len(snap.records) == 1
        assert not service.snapshot(sid, 'model_context').records
        other = sessions.create().session_id
        assert not service.snapshot(other, 'local_analysis').records
        fails('stale_wearable_snapshot', lambda: service.validate_snapshot(other, snap))
        snap.records[0].metrics[0].value = 999
        assert service.snapshot(sid, 'local_analysis').records[0].metrics[0].value == 10
        request.batch.daily[0].metrics[0].value = 88
        assert service.snapshot(sid, 'local_analysis').records[0].metrics[0].value == 10
        await service.set_consent(sid, consent(2, ('model_context',)))
        assert service.snapshot(sid, 'model_context').records
        assert not service.snapshot(sid, 'local_analysis').records
        fails('stale_wearable_snapshot', lambda: service.validate_snapshot(sid, snap))
    asyncio.run(run())


def test_atomic_batch_conflicts_updates_and_noop():
    async def run():
        _, sid, service = setup()
        request = incoming(); await service.import_records(sid, request)
        bad = incoming(1)
        bad.batch.daily[0].metrics[0].value = 99  # unchanged record revision
        with pytest.raises(RuntimeFailure, match='下一记录版本'):
            await service.import_records(sid, bad)
        assert service.revision(sid) == 1
        bad.operation_id = uuid4(); bad.batch.daily[0].record_revision = 2
        assert (await service.import_records(sid, bad))['revision'] == 2
        with pytest.raises(RuntimeFailure) as error:
            await service.import_records(sid, incoming(1))
        assert error.value.code == 'stale_revision'
        changed_id = incoming(2); changed_id.batch.daily[0].record_id = 'another'
        with pytest.raises(RuntimeFailure) as error:
            await service.import_records(sid, changed_id)
        assert error.value.code == 'record_identity_conflict'
        assert service.revision(sid) == 2
        request.batch.daily[0].metrics[0].value = 123
        with pytest.raises(RuntimeFailure) as error:
            await service.import_records(sid, request)
        assert error.value.code == 'operation_id_conflict'
    asyncio.run(run())


def test_delete_revocation_clear_and_no_resurrection():
    async def run():
        sessions, sid, service = setup()
        req = incoming(); await service.import_records(sid, req)
        await service.set_consent(sid, consent(1))
        snap = service.snapshot(sid, 'local_analysis')
        await service.set_consent(sid, consent(2, ()))
        assert not service.snapshot(sid, 'local_analysis').records
        fails('stale_wearable_snapshot', lambda: service.validate_snapshot(sid, snap))
        await service.delete(sid, DeleteWearableRecord(expected_revision=3, record_id='fixture-daily-1'))
        with pytest.raises(RuntimeFailure) as error:
            await service.import_records(sid, incoming(4))
        assert error.value.code == 'record_deleted'
        await AgentOrchestrator(sessions).clear_history(sid)
        assert not service.consent(sid, 'mock')
        assert not sessions.require(sid).wearables.records
        with pytest.raises(RuntimeFailure) as error:
            await service.import_records(sid, req)
        assert error.value.code == 'request_cleared'
    asyncio.run(run())


def test_derived_data_cannot_bypass_source_permissions_or_revision():
    async def run():
        _, sid, service = setup()
        req = incoming()
        derived = req.batch.daily[0].model_copy(deep=True)
        derived.record_id = 'derived'; derived.provider_record_id = 'derived'
        metric = derived.metrics[0]
        metric.dependency_refs = []
        from app.schemas.wearable import EvidenceReference
        metric.dependency_refs = [EvidenceReference(kind='wearable_record', source_id='fixture-daily-1', revision=1)]
        derived.provider = 'other'
        req.batch.daily.append(derived)
        await service.import_records(sid, req)
        c = consent(1); c.provider = 'other'
        await service.set_consent(sid, c)
        assert not service.snapshot(sid, 'local_analysis').records
        await service.set_consent(sid, consent(2))
        assert len(service.snapshot(sid, 'local_analysis').records) == 2
        changed = incoming(3); changed.batch.daily[0].record_revision = 2
        await service.import_records(sid, changed)
        assert len(service.snapshot(sid, 'local_analysis').records) == 1
    asyncio.run(run())


@pytest.mark.parametrize('ignore_cancel', [False, True])
def test_revoke_cancels_running_chat_and_rejects_late_reply(ignore_cancel):
    async def run():
        sessions, sid, service = setup()
        await service.import_records(sid, incoming())
        await service.set_consent(sid, consent(1, ('model_context',)))
        started = asyncio.Event()
        class Model:
            async def generate_reply(self, messages):
                started.set()
                try:
                    await asyncio.sleep(30)
                except asyncio.CancelledError:
                    if ignore_cancel:
                        return 'late response'
                    raise
        task = asyncio.create_task(AgentOrchestrator(sessions).handle_message(sid, SendMessageRequest(content='hello'), Model()))
        await started.wait()
        with pytest.raises(RuntimeFailure) as error:
            await service.import_records(sid, incoming(2))
        assert error.value.code == 'session_busy'
        await service.set_consent(sid, consent(2, ()))
        with pytest.raises(RuntimeFailure):
            await task
        assert not sessions.get(sid).messages
        assert not service.snapshot(sid, 'model_context').records
        assert sessions.require(sid).lifecycle_pending == 0
    asyncio.run(run())


def test_concurrent_import_single_commit():
    async def run():
        _, sid, service = setup()
        req = incoming()
        results = await asyncio.gather(service.import_records(sid, req), service.import_records(sid, req))
        assert results[0] == results[1]
        assert service.revision(sid) == 1
    asyncio.run(run())


def test_budget_cannot_prevent_privacy_withdrawal():
    async def run():
        sessions, sid, _ = setup()
        service = WearableService(sessions, Settings(runtime_max_requests_per_session=2))
        await service.import_records(sid, incoming())
        await service.set_consent(sid, consent(1))
        await service.set_consent(sid, consent(2, ()))
        assert not service.snapshot(sid, 'local_analysis').records
        assert len(sessions.require(sid).wearables.operations) == 2
        with pytest.raises(RuntimeFailure) as error:
            await service.set_consent(sid, consent(3))
        assert error.value.code == 'request_limit_exceeded'
    asyncio.run(run())


def test_batch_failure_rolls_back_earlier_candidates():
    async def run():
        sessions, sid, service = setup()
        await service.import_records(sid, incoming())
        request = incoming(1)
        new = request.batch.daily[0].model_copy(deep=True)
        new.record_id = 'new'; new.provider_record_id = 'new'
        request.batch.daily[0].metrics[0].value = 999
        request.batch.daily.insert(0, new)
        with pytest.raises(RuntimeFailure):
            await service.import_records(sid, request)
        assert set(sessions.require(sid).wearables.records) == {'fixture-daily-1'}
        assert service.revision(sid) == 1
        with pytest.raises(RuntimeFailure):  # same failed operation remains failed
            await service.import_records(sid, request)
    asyncio.run(run())


def test_dependency_cycle_is_not_exposed():
    async def run():
        from app.schemas.wearable import EvidenceReference
        _, sid, service = setup()
        req = incoming()
        req.batch.daily[0].metrics[0].dependency_refs = [EvidenceReference(
            kind='wearable_record', source_id='fixture-daily-1', revision=1)]
        await service.import_records(sid, req)
        await service.set_consent(sid, consent(1))
        assert not service.snapshot(sid, 'local_analysis').records
    asyncio.run(run())


def test_ended_session_can_withdraw_but_cannot_expand():
    async def run():
        sessions, sid, service = setup()
        await service.set_consent(sid, consent(0))
        await AgentOrchestrator(sessions).end_session(sid)
        # Replacing one scope with a different scope is still an expansion.
        with pytest.raises(RuntimeFailure) as error:
            await service.set_consent(sid, consent(1, ('model_context',)))
        assert error.value.code == 'session_ended'
        await service.set_consent(sid, consent(1, ()))
        assert not service.consent(sid, 'mock').scopes
    asyncio.run(run())


def test_storage_limit_rejects_without_commit(monkeypatch):
    async def run():
        import app.services.wearable_service as module
        sessions, sid, service = setup()
        monkeypatch.setattr(module, 'MAX_RECORDS', 0)
        with pytest.raises(RuntimeFailure) as error:
            await service.import_records(sid, incoming())
        assert error.value.code == 'wearable_storage_limit'
        assert not sessions.require(sid).wearables.records
    asyncio.run(run())
