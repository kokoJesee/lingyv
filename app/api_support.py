"""Local development HTTP seams for modules 2–6.

The host application owns error handling and authentication. Session identifiers
select state; they are not a production authentication mechanism.
"""
from collections.abc import Callable
from typing import Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import AwareDatetime, field_validator

from app.psychology.policy import SupportNotice, support_entry
from app.schemas.state_analysis import StateAnalysis
from app.schemas.understanding import Understanding
from app.schemas.understanding_service import (
    AddUserReport, ConfirmUnderstanding, DecideUnderstanding, EditUnderstanding,
    ProposeUnderstanding, UnderstandingView,
)
from app.schemas.wearable import ContractModel
from app.schemas.wearable_service import (
    DeleteWearableRecord, ImportWearables, SetWearableConsent,
)
from app.services.session_service import SessionService
from app.services.state_analysis import AnalysisConfig, StateAnalysisService
from app.services.understanding_service import UnderstandingService
from app.services.understanding_state import UserReport
from app.services.wearable_service import WearableService

Scope = Literal['local_analysis', 'model_context']


class AnalyzeStateRequest(ContractModel):
    as_of: AwareDatetime
    timezone: str = 'Asia/Shanghai'
    scope: Scope = 'local_analysis'

    @field_validator('timezone')
    @classmethod
    def valid_timezone(cls, value):
        return AnalysisConfig(timezone=value).timezone


def make_support_router(get_sessions: Callable[[], SessionService]) -> APIRouter:
    """Resolve the current store on every request, including after test replacement."""
    router = APIRouter(tags=['personal-support'])

    @router.get('/support', response_model=SupportNotice)
    async def get_support():
        # No session, device, model, confirmation or data-read dependency.
        return support_entry()

    @router.post('/sessions/{session_id}/wearables/import')
    async def import_wearables(session_id: UUID, request: ImportWearables):
        return await WearableService(get_sessions()).import_records(session_id, request)

    @router.post('/sessions/{session_id}/wearables/consent')
    async def set_wearable_consent(session_id: UUID, request: SetWearableConsent):
        return await WearableService(get_sessions()).set_consent(session_id, request)

    @router.post('/sessions/{session_id}/wearables/delete')
    async def delete_wearable(session_id: UUID, request: DeleteWearableRecord):
        return await WearableService(get_sessions()).delete(session_id, request)

    @router.post('/sessions/{session_id}/analysis', response_model=StateAnalysis)
    async def analyze_state(session_id: UUID, request: AnalyzeStateRequest):
        service = StateAnalysisService(WearableService(get_sessions()))
        return await service.analyze(
            session_id, as_of=request.as_of, scope=request.scope,
            config=AnalysisConfig(timezone=request.timezone),
        )

    @router.post('/sessions/{session_id}/reports', response_model=UserReport, status_code=201)
    async def add_report(session_id: UUID, request: AddUserReport):
        return await UnderstandingService(get_sessions()).add_report(session_id, request)

    @router.post('/sessions/{session_id}/reports/{report_id}/delete')
    async def delete_report(session_id: UUID, report_id: str, request: DecideUnderstanding):
        return await UnderstandingService(get_sessions()).delete_report(session_id, report_id, request)

    @router.post('/sessions/{session_id}/understandings', response_model=UnderstandingView, status_code=201)
    async def propose_understanding(session_id: UUID, request: ProposeUnderstanding):
        return await UnderstandingService(get_sessions()).propose(session_id, request)

    @router.get('/sessions/{session_id}/understandings', response_model=list[Understanding])
    async def current_understandings(session_id: UUID, scope: Scope = 'local_analysis'):
        return UnderstandingService(get_sessions()).current(session_id, scope=scope)

    @router.get('/sessions/{session_id}/understandings/{understanding_id}', response_model=UnderstandingView)
    async def get_understanding(session_id: UUID, understanding_id: str):
        return UnderstandingService(get_sessions()).get(session_id, understanding_id)

    @router.put('/sessions/{session_id}/understandings/{understanding_id}', response_model=UnderstandingView)
    async def edit_understanding(session_id: UUID, understanding_id: str, request: EditUnderstanding):
        return await UnderstandingService(get_sessions()).edit(session_id, understanding_id, request)

    @router.post('/sessions/{session_id}/understandings/{understanding_id}/confirm', response_model=UnderstandingView)
    async def confirm_understanding(session_id: UUID, understanding_id: str, request: ConfirmUnderstanding):
        return await UnderstandingService(get_sessions()).confirm(session_id, understanding_id, request)

    @router.post('/sessions/{session_id}/understandings/{understanding_id}/reject', response_model=UnderstandingView)
    async def reject_understanding(session_id: UUID, understanding_id: str, request: DecideUnderstanding):
        return await UnderstandingService(get_sessions()).reject(session_id, understanding_id, request)

    return router
