import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel


class StandardLineOut(BaseModel):
    key: str
    name: str
    qty: Decimal | None
    unit: str
    unit_price: int | None
    amount: int | None
    state: str
    source: str


class StandardTradeOut(BaseModel):
    trade: str
    version: int
    grade: str | None
    lines: list[StandardLineOut]
    supply: int
    vat: int
    total: int
    man_day: Decimal
    crew: int
    notes: list[str]


class BudgetOut(BaseModel):
    band: str
    status: Literal["within", "over", "open"]  # open: 위 한도 없음 · 아직 몰라요
    over_by: int | None  # 넣은 예산 위 한도보다 많은 금액 (부가세 포함 합계 기준)


class StandardOut(BaseModel):
    project_id: uuid.UUID
    code: str
    project_rev: int
    formula_ver: str
    price_ver: str
    computed_at: datetime
    area_m2: Decimal
    area_basis: str
    pyeong: Decimal
    trades: list[StandardTradeOut]
    supply: int
    vat: int
    total: int
    budget: BudgetOut | None
