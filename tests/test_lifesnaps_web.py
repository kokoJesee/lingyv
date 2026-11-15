import csv
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app import main
from app.llm.client import get_chat_model
from app.psychology.demo import ScriptedSupportModel
from app.wearables.lifesnaps import CASES, COLUMNS, PROVIDER, SLEEP


@pytest.fixture
def replay_csv(tmp_path, monkeypatch):
    path = tmp_path / 'sample.csv'
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=['case_id', 'day', *COLUMNS.values()])
        writer.writeheader()
        for case in CASES:
            for day in range(1, 22):
                writer.writerow({'case_id': case, 'day': day, 'sleep_minutes': 400 if day < 19 else 315})
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
