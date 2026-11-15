import csv
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app import main
from app.llm.client import get_chat_model
from app.psychology.demo import ScriptedSupportModel
from app.wearables.lifesnaps import CASES, COLUMNS, PROVIDER, REPLAY_TREND_METRICS, SLEEP


@pytest.fixture
def replay_csv(tmp_path, monkeypatch):
    path = tmp_path / 'sample.csv'
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=['case_id', 'day', *COLUMNS.values()])
        writer.writeheader()
        for case in CASES:
            for day in range(1, 22):
                writer.writerow({
                    'case_id': case, 'day': day,
                    'sleep_minutes': '' if case == 'LS04' and day >= 20 else 400 if day < 19 else 315,
                    'resting_hr_bpm': 63 if day < 19 else 66,
                    'minutes_to_fall_asleep': 0, 'steps': 5000, 'sedentary_minutes': 450,
                    'sleep_respiration_breaths_per_min': 14, 'spo2_pct': 97,
                })
    monkeypatch.setenv('PSYCHOLOGY_ENABLED', 'true')
    monkeypatch.setenv('LIFESNAPS_REPLAY_CSV', str(path))
    return path


def post_replay(client, base, as_of):
    return client.post(base + '/wearables/replay', json={
        'operation_id': str(uuid4()), 'expected_revision': 0, 'as_of': as_of, 'case_id': 'LS01',
    })


def test_replay_requires_local_demo(client, monkeypatch, replay_csv):
    monkeypatch.setenv('PSYCHOLOGY_DEMO_MODE', 'false')
    base = '/sessions/' + client.post('/sessions').json()['session_id']
    assert post_replay(client, base, datetime.now(timezone.utc).isoformat()).status_code == 503


def test_replay_connects_trends_and_local_dialogue(client, monkeypatch, replay_csv, recording_model):
    monkeypatch.setenv('PSYCHOLOGY_DEMO_MODE', 'true')
    monkeypatch.setitem(main.app.dependency_overrides, get_chat_model, ScriptedSupportModel)
    base = '/sessions/' + client.post('/sessions').json()['session_id']
    as_of = datetime.now(timezone.utc).isoformat()
    assert post_replay(client, base, as_of).json()['accepted'] == 21

    def sleep_series():
        body = client.get(base + '/wearables/trends', params={'as_of': as_of, 'days': 14}).json()
        return next(item for item in body['series'] if item['metric_id'] == SLEEP)

    def consent(revision, scopes):
        response = client.post(base + '/wearables/consent', json={
            'expected_revision': revision, 'provider': PROVIDER, 'allowed_metrics': [SLEEP], 'scopes': scopes,
        })
        assert response.status_code == 200

    def turn(content):
        return client.post(base + '/messages', json={'content': content, 'request_id': str(uuid4())})

    assert sleep_series()['points'] == []
    consent(1, ['local_analysis'])
    series = sleep_series()
    assert series['source']['is_simulated'] is False
    assert [point['value'] for point in series['points'][-3:]] == [315] * 3
    assert '0 组已授权设备观察' in turn('想一起看看。').json()['reply']
    consent(2, ['local_analysis', 'model_context'])
    assert '短了 85 分钟' in turn('最近作息有点变化。').json()['reply']
    assert '谢谢你补充' in turn('我想先说说这两天的事。').json()['reply']

    monkeypatch.setitem(main.app.dependency_overrides, get_chat_model, lambda: recording_model)
    response = turn('可以继续吗？')
    assert response.json()['detail']['code'] == 'replay_local_only'
    assert not recording_model.calls


@pytest.mark.parametrize('case_id', ['LS01', 'LS04'])
def test_live_replay_requires_current_revision_and_sends_only_selected_observations(client, monkeypatch, replay_csv, case_id):
    import json
    from app.psychology.mock import fixture_result

    captured = []

    class CapturingModel:
        async def generate_structured(self, *, system_prompt, request_json):
            captured.append(json.loads(request_json))
            return fixture_result('routine_support').model_dump_json()

    monkeypatch.setenv('PSYCHOLOGY_DEMO_MODE', 'false')
    monkeypatch.setenv('LIFESNAPS_LIVE_MODEL_ENABLED', 'true')
    monkeypatch.setitem(main.app.dependency_overrides, get_chat_model, CapturingModel)
    base = '/sessions/' + client.post('/sessions').json()['session_id']
    as_of = datetime.now(timezone.utc).isoformat()

    assert client.post(base+'/wearables/replay', json={
        'case_id': case_id, 'expected_revision': 0, 'as_of': as_of,
    }).status_code == 200
    grant = client.post(base+'/wearables/consent', json={
        'expected_revision': 1, 'provider': PROVIDER, 'allowed_metrics': list(COLUMNS),
        'scopes': ['local_analysis', 'model_context'],
    })
    rev = grant.json()['revision']
    assert grant.status_code == 200
    for consent in ({}, {'replay_context_revision': rev - 1}):
        result = client.post(base+'/messages', json={'content': '虚构对话', **consent})
        assert result.status_code == 409
    assert not captured

    result = client.post(base+'/messages', json={'content': '静息心率怎么样？', 'replay_context_revision': rev})
    assert result.status_code == 200
    records = captured[0]['records']
    summaries = {summary['metric']: summary for summary in (json.loads(item['content']) for item in records)}
    assert set(summaries) == set(REPLAY_TREND_METRICS)
    assert all(item['evidence_type'] == 'device_observation' for item in records)
    heart = summaries['resting_heart_rate_bpm']
    assert (heart['baseline'], heart['recent'], heart['delta']) == (63, 66, 3)
    assert not heart['inquiry_candidate']
    if case_id == 'LS01':
        assert summaries[SLEEP]['delta'] == -85
    else:
        assert summaries[SLEEP]['comparison_available'] is False
        assert summaries[SLEEP]['recent_valid_days'] == 1 and 'delta' not in summaries[SLEEP]
    assert f'{case_id}-day' not in json.dumps(captured[0]) and 'record_refs' not in summaries[SLEEP]
