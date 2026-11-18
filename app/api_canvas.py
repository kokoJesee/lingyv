"""HTTP surface for the canvas module and the confirmed records it writes."""
from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.config.settings import get_settings
from app.schemas.canvas import (
    Canvas,
    ConfirmCanvasRequest,
    ConfirmResult,
    CreateCanvasRequest,
    DayView,
    PatchCanvasRequest,
    Record,
    RecordPreview,
)
from app.services.canvas_service import CanvasService

router = APIRouter(tags=["canvas"])

_service: CanvasService | None = None

RESERVED = {
    "canvas_draft_not_implemented": "一键起草要等模型接入后开放（M2）。",
    "canvas_cards_not_implemented": "K/R/N 卡片在 M2 开放。",
    "record_append_not_implemented": "「补一句后来」在 M2 开放。",
    "canvas_export_not_implemented": "导出在 M2 开放。",
    "personas_not_implemented": "角色档案在 M2 开放。",
}


def get_canvas_service() -> CanvasService:
    global _service
    if _service is None:
        _service = CanvasService(settings=get_settings())
    return _service


def reset_canvas_service(service: CanvasService | None = None) -> None:
    global _service
    _service = service


def _reserved(code: str) -> JSONResponse:
    return JSONResponse(status_code=501, content={"detail": {"code": code, "message": RESERVED[code]}})


@router.post("/sessions/{session_id}/canvases", response_model=Canvas, status_code=201)
def create_canvas(session_id: str, request: CreateCanvasRequest):
    return get_canvas_service().create(session_id, request)


@router.get("/sessions/{session_id}/canvases", response_model=list[Canvas])
def list_canvases(session_id: str, limit: int = Query(default=50, ge=1, le=200),
                  cursor: str | None = Query(default=None)):
    # cursor 预留给分页；M1 忽略并返回前 limit 条。
    return get_canvas_service().list_by_session(session_id, limit=limit)


@router.get("/canvases/{canvas_id}", response_model=Canvas)
def get_canvas(canvas_id: str):
    return get_canvas_service().get(canvas_id)


@router.post("/canvases/{canvas_id}/confirm", response_model=ConfirmResult)
def confirm_canvas(canvas_id: str, request: ConfirmCanvasRequest):
    return get_canvas_service().confirm(canvas_id, request)


@router.patch("/canvases/{canvas_id}", response_model=Canvas)
def patch_canvas(canvas_id: str, request: PatchCanvasRequest):
    return get_canvas_service().patch(canvas_id, request)


@router.get("/records", response_model=list[DayView])
def list_records(month: str = Query(pattern=r"^\d{4}-\d{2}$"),
                 limit: int = Query(default=100, ge=1, le=500), cursor: str | None = Query(default=None)):
    return get_canvas_service().month_view(month)


@router.get("/days/{day}", response_model=DayView)
def get_day(day: str):
    return get_canvas_service().day_view(day)


@router.get("/records/{record_id}", response_model=Record)
def get_record(record_id: str):
    return get_canvas_service().get_record(record_id)


@router.delete("/records/{record_id}")
def delete_record(record_id: str):
    record = get_canvas_service().delete_record(record_id)
    return {"record": record, "linked_memories": []}


@router.get("/records/{record_id}/preview", response_model=RecordPreview)
def preview_record(record_id: str):
    record = get_canvas_service().get_record(record_id)
    return get_canvas_service()._preview(record)


# ---- 预留（M2）------------------------------------------------------------

@router.post("/canvases/{canvas_id}/draft", status_code=501)
def draft_canvas(canvas_id: str):
    return _reserved("canvas_draft_not_implemented")


@router.post("/canvases/{canvas_id}/cards", status_code=501)
def canvas_cards(canvas_id: str):
    return _reserved("canvas_cards_not_implemented")


@router.post("/records/{record_id}/append", status_code=501)
def append_record(record_id: str):
    return _reserved("record_append_not_implemented")


@router.get("/canvases/{canvas_id}/export", status_code=501)
def export_canvas(canvas_id: str):
    return _reserved("canvas_export_not_implemented")


@router.get("/personas", status_code=501)
def list_personas():
    return _reserved("personas_not_implemented")
