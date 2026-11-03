import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.quote.schemas import QuoteBriefOut
from app.visit.schemas import CompareOut, VendorVisitRow


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


class ConfirmIn(BaseModel):
    seen_ver: int = Field(ge=1, description="비교 화면을 연 때의 본 버전 (seen_ver 그대로)")


class DealOut(BaseModel):
    """업체 확정 한 건 (고객 쪽)"""

    id: uuid.UUID
    trade: str
    status: str
    vendor_id: uuid.UUID | None
    vendor_name: str
    owner_name: str | None = Field(description="업체 대표 이름 (연락처가 보일 때만)")
    visit_id: uuid.UUID
    quote: QuoteBriefOut = Field(description="확정한 견적서 버전 (바꾸지 않는다)")
    confirmed_at: datetime
    dates_due_at: datetime = Field(description="업체가 공사 날짜를 낼 기한")
    overdue: bool = Field(description="기한이 지났는데 아직 날짜를 안 냄 (상태로 저장하지 않음)")
    start_date: date | None
    end_date: date | None
    work_done_on: date | None


class ConfirmOut(BaseModel):
    deal: DealOut
    closed: list[uuid.UUID] = Field(description="같은 공정에서 닫힌 다른 요청")
    quote_expired: bool = Field(
        description="확정한 견적서의 유효기간이 지났는지 (막지 않고 알린다)"
    )


class CompareDealOut(CompareOut):
    deal: DealOut | None = Field(description="그 공정의 업체 확정 (없으면 비어 있다)")


class InboxRowOut(VendorVisitRow):
    """업체 작업함 한 줄. 공사 탭은 확정 정보가 더 들어 있다."""

    deal_id: uuid.UUID | None = None
    deal_status: str | None = None
    confirmed_at: datetime | None = None
    dates_due_at: datetime | None = None
    overdue: bool = False
    start_date: date | None = None
    end_date: date | None = None


class InboxPageOut(BaseModel):
    items: list[InboxRowOut]
    next_before: datetime | None
