"""Offline four-route demonstration; no live model or safety classification."""
import argparse
import asyncio
import json
from uuid import uuid4

from app.psychology.adapter import PsychologyAdapter
from app.psychology.inputs import PsychologyInputBuilder
from app.psychology.mock import FixedPsychologyModel, fixture_result
from app.psychology.policy import REVIEW_STATUS, support_entry
from app.services.session_service import SessionService


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--route', default='routine_support', choices=['routine_support','clarify_safety','safety_support','urgent_real_world_help'])
    args = parser.parse_args(argv)

    async def run():
        sessions = SessionService(); sid = sessions.create().session_id
        builder = PsychologyInputBuilder(sessions)
        prepared = await builder.prepare(sid, uuid4(), current_message='这是虚构工程演示，我希望得到支持。', history=[], as_of='2026-11-04T00:00:00+08:00')
        model = FixedPsychologyModel(fixture_result(args.route))
        response = await PsychologyAdapter(model).evaluate(prepared.request, ensure_current=lambda: builder.validate_current(prepared))
        return dict(review_status=REVIEW_STATUS, live_model_calls=0, fixture_calls=model.calls,
                    response=response.model_dump(mode='json'), support_entry=support_entry().__dict__)
    print(json.dumps(asyncio.run(run()), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
