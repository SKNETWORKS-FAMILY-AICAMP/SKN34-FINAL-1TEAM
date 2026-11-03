import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel


class PlanBlockOut(BaseModel):
    trade: str | None
    kind: str
    label: str
    start: date
    end: date
    days: int
    crew: int | None
    man_day: Decimal | None


class PlanDayOut(BaseModel):
    day: date
    trade: str | None
    kind: str
    names: list[str]
    holiday: str | None


class PlanOut(BaseModel):
    project_id: uuid.UUID
    kind: str
    version: int
    project_rev: int
    start: date
    end: date
    work_days: int
    weekend_work: bool
    computed_at: datetime
    blocks: list[PlanBlockOut]
    days: list[PlanDayOut]


class PlanRedo(BaseModel):
    """날짜 바꿔 보기: 시작일 · 주말에도 공사"""

    start: date
    weekend_work: bool = False
