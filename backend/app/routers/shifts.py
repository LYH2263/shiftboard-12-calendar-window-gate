from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services import handover as svc

router = APIRouter(prefix="/shifts", tags=["班次"])


class WindowBody(BaseModel):
    weekday: int = Field(..., ge=1, le=7, description="星期几：1=周一 … 7=周日")
    start: str = Field(..., description="起始时刻本地墙钟 HH:MM")
    end: str = Field(..., description="结束时刻本地墙钟 HH:MM，允许 24:00；小于起始视为跨午夜")


class WindowsBody(BaseModel):
    windows: list[WindowBody]


@router.get("")
def get_shifts():
    return svc.list_shifts()


@router.put("/{shift_id}/windows")
def put_windows(shift_id: int, body: WindowsBody):
    return svc.update_shift_windows(
        shift_id, [w.model_dump() for w in body.windows]
    )
