"""Run LifeSnaps device development cases with local services and a fixed model."""
import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from app.config.settings import Settings
from app.psychology.adapter import PsychologyAdapter
from app.psychology.demo import ScriptedSupportModel
from app.psychology.inputs import PsychologyInputBuilder
from app.schemas.support_views import TrendQuery
from app.schemas.wearable_service import ImportWearables, SetWearableConsent
from app.services.session_service import SessionService
from app.services.state_analysis import AnalysisConfig, StateAnalysisService
from app.services.support_views import SupportViewService
from app.wearables.lifesnaps import PROVIDER, REPLAY_AS_OF, SLEEP, COLUMNS, check, load_batches

# Expectations were calculated on the source CSV before it was converted to records.
EXPECTED = {
    'LS01': (-86.5, 2315.0, True, True, 3),
    'LS02': (45.0, 11712.5, False, True, 3),
    'LS03': (0.0, -316.0, False, False, 3),
    'LS04': (None, -76.5, None, False, 1),
}


def device_inputs(prepared):
    return {json.loads(item.content)['metric'] for item in prepared.request.records
            if item.evidence_type == 'device_observation'}


async def replay_case(sessions, case, batch):
    sid = sessions.create().session_id
    views, builder = SupportViewService(sessions), PsychologyInputBuilder(sessions)
    service = views.wearables
    analysis = StateAnalysisService(service)
    config = AnalysisConfig(timezone='UTC')
    query = TrendQuery(as_of=REPLAY_AS_OF, days=30, timezone='UTC')

    async def prepare():
        return await builder.prepare(sid, uuid4(), current_message='这是本机的工程测试输入。',
                                     history=[], as_of=REPLAY_AS_OF, config=config)

    async def permit(revision, scopes):
        await service.set_consent(sid, SetWearableConsent(
            provider=PROVIDER, expected_revision=revision,
            allowed_metrics=list(COLUMNS) if scopes else [], scopes=scopes,
        ))

    receipt = await service.import_records(sid, ImportWearables(expected_revision=0, batch=batch))
    check(receipt['accepted'] == 21, f'{case}: import count differs')
    check((await analysis.analyze(sid, as_of=REPLAY_AS_OF, config=config)).status == 'not_authorized',
          f'{case}: import granted analysis automatically')
    check(all(not s.points for s in views.trends(sid, query).series), f'{case}: pre-consent trend exposed points')
    await permit(receipt['revision'], ['local_analysis'])
    check(not device_inputs(await prepare()), f'{case}: local grant leaked into model context')

    result = await analysis.analyze(sid, as_of=REPLAY_AS_OF, config=config)
    observations = {o.metric_id: o for o in result.observations}
    statuses = {s.metric_id: s for s in result.metric_statuses}
    sleep, steps = observations.get(SLEEP), observations['steps']
    sleep_delta = sleep.delta if sleep else None
    sleep_trigger = sleep.inquiry_candidate if sleep else None
    recent_days = statuses[SLEEP].recent_valid_days
    check((sleep_delta, steps.delta, sleep_trigger, steps.inquiry_candidate, recent_days) == EXPECTED[case],
          f'{case}: baseline deltas or inquiry threshold differ from the curated source')
    series = next(s for s in views.trends(sid, query).series if s.metric_id == SLEEP)
    check(series.source and not series.source.is_simulated, f'{case}: source provenance missing')
    check(sum(point.value is not None for point in series.points[-3:]) == recent_days,
          f'{case}: recent trend points disagree with analysis coverage')
    check(all(point.value is None for point in series.points[:9]), f'{case}: dates beyond import were filled')
    if sleep is None:
        check(all(p.value is None and p.quality == 'missing' for p in series.points[-3:] if p.value is None),
              f'{case}: missing sleep was not preserved')

    await permit(result.evidence_revision, ['local_analysis', 'model_context'])
    prepared = await prepare()
    included = device_inputs(prepared)
    candidates = {key for key, observation in observations.items() if observation.inquiry_candidate}
    check(candidates <= included <= set(observations) and 0 < len(included) <= 6,
          f'{case}: fixed-model request contains unexpected observations')
    check((SLEEP in included) == (sleep is not None), f'{case}: sleep evidence disagrees with valid-day count')
    check(prepared.omitted_observations == len(observations) - len(included),
          f'{case}: context budget omitted-count mismatch')
    model = PsychologyAdapter(ScriptedSupportModel(), Settings(_env_file=None))
    response = await model.evaluate(prepared.request, ensure_current=lambda: builder.validate_current(prepared))
    check(response.result.route == 'routine_support', f'{case}: unexpected scripted route')
    await permit(prepared.wearable_revision, [])
    check(not device_inputs(await prepare()), f'{case}: revoked observations still reachable')
    check(all(not s.points for s in views.trends(sid, query).series), f'{case}: revoked trends still exposed')
    return {
        'case': case, 'days_imported': receipt['accepted'], 'sleep_delta_minutes': sleep_delta,
        'steps_delta': steps.delta, 'sleep_inquiry_candidate': sleep_trigger,
        'steps_inquiry_candidate': steps.inquiry_candidate, 'recent_valid_sleep_days': recent_days,
        'sleep_status': statuses[SLEEP].status, 'test_model_device_metrics': sorted(included),
        'auth_checks': 'passed',
    }


async def run_replay(path):
    batches = load_batches(path)
    sessions = SessionService()
    results = [await replay_case(sessions, case, batch) for case, batch in batches.items()]
    empty = sessions.create().session_id
    check(SupportViewService(sessions).state(empty).wearables.record_count == 0,
          'Device records leaked into a fresh session')
    return {'source': 'https://zenodo.org/records/7229547', 'replay_timeline': 'UTC test dates',
            'mapped_metrics': list(COLUMNS), 'live_model_calls': 0, 'cases': results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv', type=Path, help='Local selected-wearable-daily.csv')
    parser.add_argument('--output', type=Path, help='Write to a new local JSON file')
    args = parser.parse_args(argv)
    try:
        payload = json.dumps(asyncio.run(run_replay(args.csv)), ensure_ascii=False, indent=2) + '\n'
        if args.output:
            with args.output.open('x', encoding='utf-8') as output:
                output.write(payload)
        else:
            print(payload, end='')
    except (ValueError, OSError) as error:
        parser.exit(2, f'LifeSnaps replay failed: {error}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
