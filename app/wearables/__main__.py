"""Explicit offline export. Files contain only generated fictional data."""
import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from app.schemas.wearable_service import ImportWearables
from app.services.session_service import SessionService
from app.services.wearable_service import WearableService
from app.wearables.base import MockQuery
from app.wearables.mock_generator import generate


def main(argv=None):
    parser = argparse.ArgumentParser(description='Generate fictional wearable records; no model/device calls.')
    parser.add_argument('--scenario', default='S01')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--as-of', required=True)
    parser.add_argument('--days', type=int, default=30)
    parser.add_argument('--timezone', default='Asia/Shanghai')
    parser.add_argument('--profile', default='mock_research_combined')
    parser.add_argument('--variant', default='normal')
    parser.add_argument('--no-intervals', action='store_true')
    parser.add_argument('--output', type=Path, required=True, help='New directory; existing directories are not overwritten.')
    parser.add_argument('--import-demo', action='store_true', help='Validate import into a fresh ephemeral session, without consent.')
    args = parser.parse_args(argv)
    try:
        generated = generate(MockQuery(scenario_id=args.scenario, seed=args.seed, as_of=args.as_of,
            days=args.days, timezone=args.timezone, capability_profile=args.profile, variant=args.variant,
            include_intervals=not args.no_intervals))
        receipt = None
        if args.import_demo:
            async def run_import():
                sessions = SessionService()
                sid = sessions.create().session_id
                service = WearableService(sessions)
                result = await service.import_records(sid, ImportWearables(
                    operation_id=uuid4(), expected_revision=0, batch=generated.batch))
                assert not service.snapshot(sid, 'model_context').records
                return result
            receipt = asyncio.run(run_import())
        args.output.mkdir(parents=True, exist_ok=False)
        (args.output / 'batch.json').write_text(generated.batch.model_dump_json(indent=2), encoding='utf-8')
        for filename, value in [('capabilities.json', [c.model_dump(mode='json') for c in generated.capabilities]),
                                ('replay-manifest.json', generated.manifest), ('evaluation-only.json', generated.evaluation)]:
            (args.output / filename).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(dict(is_simulated=True, daily=len(generated.batch.daily), sleeps=len(generated.batch.sleeps),
                              workouts=len(generated.batch.workouts), intervals=len(generated.batch.intervals),
                              import_receipt=receipt), ensure_ascii=False))
        return 0
    except (ValueError, OSError) as error:
        parser.exit(2, f'Generation/export failed: {error}\n')


if __name__ == '__main__':
    raise SystemExit(main())
