import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from app.agent.errors import RuntimeFailure
from app.agent.orchestrator import AgentOrchestrator
from app.config.settings import Settings
from app.psychology.demo import ScriptedSupportModel
from app.schemas.archive import SaveArchiveRequest
from app.schemas.message import SendMessageRequest
from app.schemas.understanding_service import AddUserReport, ProposeUnderstanding, ConfirmUnderstanding
from app.schemas.wearable_service import SetWearableConsent
from app.services.archive_service import ArchiveService
from app.services.archive_store import SQLiteArchiveStore
from app.services.session_service import SessionService
from app.services.understanding_service import UnderstandingService
from app.services.wearable_service import WearableService
from app.wearables.mock import MockWearableAdapter
from app.wearables.base import MockQuery
from app.wearables.metric_registry import METRIC_REGISTRY


def run(coro):
    return asyncio.run(coro)


async def fixture(tmp_path, *, route='routine_support'):
    sessions = SessionService()
    sid = sessions.create().session_id
    wearables = WearableService(sessions)
    await MockWearableAdapter().import_into(wearables, sid,
        MockQuery(as_of=datetime.now(timezone.utc), scenario_id='S02'), expected_revision=0, operation_id=uuid4())
    grant = SetWearableConsent(expected_revision=1, provider='mock', allowed_metrics=list(METRIC_REGISTRY), scopes=['local_analysis', 'model_context'])
    await wearables.set_consent(sid, grant)
    understandings = UnderstandingService(sessions)
    report = await understandings.add_report(sid, AddUserReport(content='虚构：最近旅行'))
    proposed = await understandings.propose(sid, ProposeUnderstanding(interpretation='用户说最近旅行', origin='user_report',
        evidence_refs=[dict(kind='user_report', source_id=report.report_id, revision=1)]))
    await understandings.confirm(sid, proposed.understanding.understanding_id,
        ConfirmUnderstanding(expected_revision=1, confirmation_token=proposed.confirmation_token))
    pending = await understandings.propose(sid, ProposeUnderstanding(interpretation='待核对的旅行作息', origin='agent_hypothesis',
        evidence_refs=[dict(kind='user_report', source_id=report.report_id, revision=1)]))
    daily = next(r for r in sessions.require(sid).wearables.records.values() if r.record_type == 'daily')
    device = await understandings.propose(sid, ProposeUnderstanding(interpretation='需要核对这条设备观察', origin='agent_hypothesis',
        evidence_refs=[dict(kind='wearable_record', source_id=daily.record_id, revision=1)]))
    request = SendMessageRequest(content='虚构：请和我核对')
    await AgentOrchestrator(sessions, Settings(psychology_enabled=True)).handle_message(sid, request, ScriptedSupportModel(route))
    store = SQLiteArchiveStore(tmp_path/'business.sqlite3')
    archives = ArchiveService(sessions, store)
    return sessions, sid, store, archives, grant, request, pending, device


async def save(archives, sid, **kwargs):
    return await archives.save(sid, SaveArchiveRequest(expected_revision=archives.sessions.get(sid).revision,
        save_business_data=True, **kwargs))


def test_business_save_requires_separate_choice_before_any_disk_write(tmp_path):
    sessions, sid, store, archives, *_ = run(fixture(tmp_path))
    with pytest.raises(RuntimeFailure, match='保存') as error:
        run(archives.save(sid, SaveArchiveRequest(expected_revision=sessions.get(sid).revision)))
    assert error.value.code == 'archive_business_consent_required'
    assert not store.path.exists()
    info = run(save(archives, sid))
    assert info.snapshot_version == 2
    assert run(archives.list())[0].snapshot_version == 2


def test_roundtrip_restores_data_but_not_permission_or_old_control_credentials(tmp_path):
    sessions, sid, store, archives, grant, request, pending, device = run(fixture(tmp_path))
    run(save(archives, sid))
    raw = store.path.read_bytes()
    assert str(pending.confirmation_token).encode() not in raw
    fresh = SessionService()
    run(ArchiveService(fresh, store).restore(sid))
    record = fresh.require(sid)
    assert record.support_mode and record.support_route == 'routine_support'
    assert record.wearables.records and not record.wearables.consents
    assert not WearableService(fresh).snapshot(sid, 'model_context').records
    controls = UnderstandingService(fresh)
    current = controls.get(sid, pending.understanding.understanding_id)
    assert current.confirmation_token and current.confirmation_token != pending.confirmation_token
    assert controls.get(sid, device.understanding.understanding_id).understanding.status == 'invalidated'
    assert any('旅行' in x.interpretation for x in controls.current(sid, scope='model_context'))
    with pytest.raises(RuntimeFailure) as error:
        run(WearableService(fresh).set_consent(sid, grant))
    assert error.value.code == 'request_restored'
    with pytest.raises(RuntimeFailure) as error:
        run(AgentOrchestrator(fresh, Settings(psychology_enabled=True)).handle_message(sid, request, ScriptedSupportModel()))
    assert error.value.code == 'request_restored'
    # Old device-dependent replies cannot re-enter the next prompt after restoration.
    reply = run(AgentOrchestrator(fresh, Settings(psychology_enabled=True)).handle_message(sid, SendMessageRequest(content='继续'), ScriptedSupportModel()))
    assert reply.context_manifest.excluded_history_indices == [1]
    assert '0 组已授权设备观察' in reply.reply
    # A second v2 save works even though old device references are now invalidated.
    info = run(ArchiveService(fresh, store).list())[0]
    run(save(ArchiveService(fresh, store), sid, expected_archive_version=info.archive_version))


def test_unresolved_route_survives_restore(tmp_path):
    _, sid, store, archives, *_ = run(fixture(tmp_path, route='urgent_real_world_help'))
    run(save(archives, sid))
    fresh = SessionService()
    run(ArchiveService(fresh, store).restore(sid))
    assert fresh.require(sid).support_route == 'urgent_real_world_help'
    with pytest.raises(RuntimeFailure) as error:
        run(AgentOrchestrator(fresh, Settings(psychology_enabled=True)).handle_message(sid, SendMessageRequest(content='谢谢'), ScriptedSupportModel()))
    assert error.value.code == 'route_transition_unreviewed'
    assert len(fresh.get(sid).messages) == 2


@pytest.mark.parametrize('damage', ['missing_dependency', 'future_device_version', 'missing_report', 'duplicate_report', 'foreign_turn_understanding', 'wrong_version', 'control_token', 'mode_downgrade'])
def test_damaged_business_snapshot_never_installs_partial_session(tmp_path, damage):
    _, sid, store, archives, *_ = run(fixture(tmp_path))
    run(save(archives, sid))
    with sqlite3.connect(store.path) as db:
        data = json.loads(db.execute('SELECT payload FROM archives').fetchone()[0])
        b = data['business']
        if damage == 'missing_dependency': b['turn_sources'] = {}
        elif damage == 'future_device_version': b['turn_sources']['1']['wearable_revision'] = 999
        elif damage == 'missing_report': b['reports'] = []
        elif damage == 'duplicate_report': b['reports'].append(b['reports'][0])
        elif damage == 'foreign_turn_understanding': b['turn_sources']['1']['understanding_refs'][0]['source_id'] = 'unknown'
        elif damage == 'wrong_version': data['version'] = 1
        elif damage == 'control_token': b['understandings'][0]['confirmation_token'] = str(uuid4())
        elif damage == 'mode_downgrade': b.update(support_mode=False, support_route=None)
        db.execute('UPDATE archives SET payload=?', (json.dumps(data),))
    fresh = SessionService()
    with pytest.raises(RuntimeFailure) as error:
        run(ArchiveService(fresh, store).restore(sid))
    assert error.value.code == 'archive_invalid'
    assert not fresh.list()


def test_loaded_memory_collision_and_explicit_disk_delete_remain_separate(tmp_path):
    sessions, sid, store, archives, *_ = run(fixture(tmp_path))
    info = run(save(archives, sid))
    with pytest.raises(RuntimeFailure) as error: run(archives.restore(sid))
    assert error.value.code == 'session_already_loaded'
    run(archives.delete(sid, info.archive_version))
    assert not run(archives.list())
    assert sessions.get(sid).messages


def test_devices_before_first_chat_also_require_explicit_business_save(tmp_path):
    sessions = SessionService(); sid = sessions.create().session_id
    run(UnderstandingService(sessions).add_report(sid, AddUserReport(content='虚构自述')))
    store = SQLiteArchiveStore(tmp_path/'before.sqlite3')
    archives = ArchiveService(sessions, store)
    with pytest.raises(RuntimeFailure) as error:
        run(archives.save(sid, SaveArchiveRequest(expected_revision=sessions.get(sid).revision)))
    assert error.value.code == 'archive_business_consent_required'
    assert not store.path.exists()
