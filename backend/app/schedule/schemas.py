import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, model_validator

from app.quote.schemas import QuoteBriefOut
from app.schedule.models import CREW_MAX, WISH_MAX
from app.visit.schemas import CompareOut, VendorVisitDetailOut, VendorVisitRow

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
# 공사 날짜 상태: 아직 안 냄 · 냄(수락 기다림) · 다른 날짜 요청 열림 · 수락함(이후 공사 상태) · 다시 고름
DatesState = Literal["waiting", "proposed", "requested", "scheduled", "closed"]


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
    overdue: bool = Field(
        description="기한 지남: 날짜를 아직 안 냈거나 다른 날짜 요청에 답하지 않음 (상태가 아님)"
    )
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
    dates_state: DatesState | None = None
    proposed_start: date | None = Field(default=None, description="수락 기다리는 날짜")
    proposed_end: date | None = None
    proposed_days: int | None = Field(default=None, description="날짜를 낸 뒤 지난 날 (공사 탭)")
    wish_dates: list[date] = Field(default=[], description="다른 날짜 요청: 고객이 고른 시작일")
    request_due_at: datetime | None = Field(default=None, description="다른 날짜 요청에 답할 기한")


class InboxPageOut(BaseModel):
    items: list[InboxRowOut]
    next_before: datetime | None


# ---- 공사 날짜 ----


class ProposalOut(BaseModel):
    """업체가 낸 공사 날짜"""

    id: uuid.UUID
    start: date
    end: date
    workdays: int = Field(description="낸 날짜 안의 일하는 날 (믿집 계산)")
    crew: int
    method: Literal["app", "call"]
    call_on: date | None = Field(description="통화로 정했으면 통화한 날")
    kind: str
    reason: str | None
    status: str
    request_id: uuid.UUID | None = Field(description="답한 다른 날짜 요청")
    changed: bool = Field(description="앞서 낸 날짜를 바꿔 다시 냈는지 ('날짜가 바뀌었어요')")
    created_at: datetime
    decided_at: datetime | None


class DateRequestOut(BaseModel):
    id: uuid.UUID
    wish_dates: list[date] = Field(description="시작해도 되는 날")
    reason: str | None
    due_at: datetime = Field(description="업체가 새 날짜를 낼 기한 (요청 다음 날 18:00)")
    overdue: bool
    status: str
    created_at: datetime


class OtherTradeOut(BaseModel):
    """이 집 다른 공정의 확정 날짜 (겹침 확인용)"""

    trade: str
    vendor_name: str
    start: date
    end: date


class StartOptionOut(BaseModel):
    start: date
    end: date = Field(description="같은 일하는 날 수로 끝나는 날 (믿집 계산)")


class StepNeedOut(BaseModel):
    name: str
    half_days: int


class NeedOut(BaseModel):
    """하루 인원으로 센 필요한 기간: 품 ÷ 인원을 반나절로 올리고 준비 · 마감을 더해 하루로 올림"""

    man_day: Decimal
    crew: int
    days: int
    steps: list[StepNeedOut]


class FitOut(BaseModel):
    """날짜가 다른 공정과 겹치는지 · 겹치지 않는 자리"""

    conflicts: list[OtherTradeOut] = Field(description="겹치는 다른 공정 (있으면 수락할 수 없다)")
    start_from: date | None = Field(description="앞 공정이 끝난 다음 일하는 날")
    end_by: date | None = Field(description="뒤 공정 시작 전 마지막 일하는 날")


class TradeDatesOut(BaseModel):
    """한 공정의 공사 날짜 (고객 쪽)"""

    deal: DealOut
    dates_state: DatesState
    proposal: ProposalOut | None = Field(description="수락 기다리는 날짜")
    request: DateRequestOut | None = Field(description="열린 다른 날짜 요청")
    requests_left: int = Field(description="다른 날짜 요청 남은 수 (2번까지)")
    fit: FitOut | None = Field(description="수락 기다리는 날짜가 다른 공정과 맞는지")
    start_options: list[StartOptionOut] = Field(
        description="다른 날짜 요청 후보: 겹치지 않는 첫날부터, 같은 기간으로 끝나는 날"
    )


class HolidayOut(BaseModel):
    day: date
    name: str


class ScheduleOut(BaseModel):
    project_id: uuid.UUID
    weekend_work: bool = Field(description="주말에도 공사 (아니면 토 · 일은 쉬는 날)")
    holidays: list[HolidayOut] = Field(description="오늘부터 180일 안의 공휴일")
    trades: list[TradeDatesOut] = Field(description="목공 → 도배 순, 확정한 공정만")


class JobOut(BaseModel):
    """업체 공사 날짜 내기 화면"""

    deal_id: uuid.UUID
    trade: str
    status: str
    confirmed_at: datetime
    dates_due_at: datetime
    overdue: bool
    start_date: date | None = Field(description="수락한 공사 날짜")
    end_date: date | None
    visit: VendorVisitDetailOut = Field(description="집 · 고객 (연락처는 공개 규칙대로)")
    quote: QuoteBriefOut = Field(description="확정한 견적서 버전")
    plan_version: int = Field(description="지금 예상 공정표 버전")
    plan_start: date | None = Field(description="예상 공정표의 이 공정 시작일")
    plan_end: date | None
    need: NeedOut = Field(description="믿집 가정 인원으로 센 필요한 기간")
    others: list[OtherTradeOut]
    weekend_work: bool
    holidays: list[HolidayOut]
    dates_state: DatesState
    proposal: ProposalOut | None
    request: DateRequestOut | None
    requests_left: int


class DatesIn(BaseModel):
    """업체가 공사 날짜 내기 (수락 전에는 다시 낼 수 있다)"""

    start: date
    end: date
    crew: int = Field(ge=1, le=CREW_MAX, description="하루 작업 인원")
    method: Literal["app", "call"] = Field(description="믿집에서 내기 · 고객과 통화로 정함")
    call_on: date | None = Field(default=None, description="통화로 정했으면 통화한 날 (필수)")
    request_id: uuid.UUID | None = Field(
        default=None, description="화면이 본 열린 요청 (그사이 바뀌었으면 409)"
    )


class PreviewOut(BaseModel):
    """낼 날짜 미리 계산: 일하는 날 · 필요한 기간 · 겹침"""

    workdays: int
    need: NeedOut
    short: bool = Field(description="일하는 날이 필요한 기간보다 적음 (막지 않고 알린다)")
    fit: FitOut


class DateRequestIn(BaseModel):
    """고객의 다른 날짜 요청 (2번까지)"""

    wish_dates: list[date] = Field(min_length=1, max_length=WISH_MAX)
    reason: Reason | None = None

    @model_validator(mode="after")
    def _distinct(self):
        if len(set(self.wish_dates)) != len(self.wish_dates):
            raise ValueError("같은 날을 두 번 골랐어요.")
        return self
