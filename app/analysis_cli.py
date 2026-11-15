"""Offline fictional-data demo: grants local analysis in a fresh ephemeral session."""
import argparse
import asyncio
from pathlib import Path
from uuid import uuid4

from app.schemas.wearable_service import SetWearableConsent
from app.services.session_service import SessionService
from app.services.wearable_service import WearableService
from app.services.state_analysis import AnalysisConfig, StateAnalysisService
from app.wearables.mock import MockWearableAdapter
from app.wearables.base import MockQuery
from app.wearables.metric_registry import METRIC_REGISTRY


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', default='S02')
    parser.add_argument('--as-of', required=True)
    parser.add_argument('--timezone', default='Asia/Shanghai')
    parser.add_argument('--output', type=Path, help='Optional new JSON file; never overwrite.')
    args = parser.parse_args(argv)

    async def run():
        query = MockQuery(scenario_id=args.scenario, as_of=args.as_of, timezone=args.timezone, include_intervals=False)
        sessions = SessionService(); sid = sessions.create().session_id
        data = WearableService(sessions)
        await MockWearableAdapter().import_into(data, sid, query, expected_revision=0, operation_id=uuid4())
        await data.set_consent(sid, SetWearableConsent(expected_revision=1, provider='mock',
                              allowed_metrics=list(METRIC_REGISTRY), scopes=['local_analysis']))
        return await StateAnalysisService(data).analyze(sid, as_of=args.as_of, config=AnalysisConfig(timezone=args.timezone))

    try:
        result = asyncio.run(run())
        serialized = result.model_dump_json(indent=2)+'\n'
        if args.output:
            with args.output.open('x', encoding='utf-8') as output:
                output.write(serialized)
        else:
            print(serialized, end='')
        return 0
    except (ValueError, OSError) as error:
        parser.exit(2, f'Analysis demo failed: {error}\n')


if __name__ == '__main__':
    raise SystemExit(main())
