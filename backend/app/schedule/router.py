import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user, require_role
from app.auth.models import User
from app.db import get_session
from app.schedule import service
from app.schedule.models import CREW_MAX
from app.schedule.schemas import (
    AcceptIn,
    CompareDealOut,
    ConfirmIn,
    ConfirmOut,
    DateRequestIn,
    DatesIn,
    DealOut,
    InboxPageOut,
    JobOut,
    PlanOut,
    PlanRedo,
    PreviewOut,
    ScheduleOut,
)
from app.shared.clock import today_seoul
from app.vendor.deps import approved_vendor
from app.visit.schemas import InboxSummaryOut, ReselectOut

router = APIRouter(tags=["공정표 · 업체 확정"])
customer_only = require_role("customer")
Trade = Literal["dobae", "mokgong"]


@router.get(
    "/projects/{project_id}/plan",
    response_model=PlanOut,
    summary="공정표: 공사 날짜를 수락했으면 확정 공정표, 아니면 예상 공정표 (만든 사람 · 관리자)",
)
async def plan(
    project_id: uuid.UUID,
    kind: Literal["expected", "final"] | None = None,
    user: User = Depends(current_user),
    s: AsyncSession = Depends(get_session),
) -> PlanOut:
    return await service.plan_for(s, user, project_id, today_seoul(), kind)


@router.post(
    "/projects/{project_id}/plan",
    response_model=PlanOut,
    summary="예상 공정표 다시 계산 (시작일 · 주말 공사)",
)
async def redo_plan(
    project_id: uuid.UUID,
    data: PlanRedo,
    user: User = Depends(current_user),
    s: AsyncSession = Depends(get_session),
) -> PlanOut:
    return await service.redo(s, user, project_id, data, today_seoul())


@router.post(
    "/projects/{project_id}/quotes/{visit_id}/confirm",
    response_model=ConfirmOut,
    summary="업체 확정 (본 버전 seen_ver일 때만, 같은 공정의 다른 요청은 닫힘)",
)
async def confirm(
    project_id: uuid.UUID,
    visit_id: uuid.UUID,
    data: ConfirmIn,
    user: User = Depends(customer_only),
    s: AsyncSession = Depends(get_session),
) -> ConfirmOut:
    return await service.confirm(s, user, project_id, visit_id, data, today_seoul())


@router.post(
    "/projects/{project_id}/deals/{deal_id}/reselect",
    response_model=ReselectOut,
    summary="다시 고르기 (공사 날짜 수락 전만): 확정을 풀고 이 공정의 요청을 모두 닫는다",
)
async def reselect(
    project_id: uuid.UUID,
    deal_id: uuid.UUID,
    user: User = Depends(customer_only),
    s: AsyncSession = Depends(get_session),
) -> ReselectOut:
    return await service.reselect(s, user, project_id, deal_id)


@router.get(
    "/projects/{project_id}/deals",
    response_model=list[DealOut],
    summary="이 집 업체 확정 (공정마다, 다시 고르기 한 것은 빼고)",
)
async def deals(
    project_id: uuid.UUID,
    trade: Trade | None = None,
    user: User = Depends(customer_only),
    s: AsyncSession = Depends(get_session),
) -> list[DealOut]:
    return await service.deals_for(s, user, project_id, trade, today_seoul())


@router.get(
    "/projects/{project_id}/quotes",
    response_model=CompareDealOut,
    summary="실측 견적 비교 · 표준견적서와 업체가 보낸 견적서 · 확정 (한 공정)",
)
async def compare_quotes(
    project_id: uuid.UUID,
    trade: Trade,
    user: User = Depends(customer_only),
    s: AsyncSession = Depends(get_session),
) -> CompareDealOut:
    return await service.compare(s, user, project_id, trade, today_seoul())


@router.get(
    "/vendor/visits",
    response_model=InboxPageOut,
    summary="업체 작업함 · 할 일 · 기다림 · 공사 · 끝남 (끝남은 before 커서로 limit씩)",
)
async def vendor_inbox(
    tab: Literal["todo", "waiting", "jobs", "done"] = "todo",
    before: AwareDatetime | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    vendor: User = Depends(approved_vendor),
    s: AsyncSession = Depends(get_session),
) -> InboxPageOut:
    return await service.vendor_inbox(s, vendor, tab, today_seoul(), before, limit)


@router.get("/vendor/visits/summary", response_model=InboxSummaryOut, summary="업체 작업함 탭별 수")
async def vendor_inbox_summary(
    vendor: User = Depends(approved_vendor), s: AsyncSession = Depends(get_session)
) -> InboxSummaryOut:
    return await service.vendor_summary(s, vendor)


# ---- 공사 날짜 ----


@router.get(
    "/projects/{project_id}/schedule",
    response_model=ScheduleOut,
    summary="공사 날짜: 공정마다 낸 날짜 · 다른 날짜 요청 · 겹침 · 요청 후보",
)
async def schedule(
    project_id: uuid.UUID,
    user: User = Depends(customer_only),
    s: AsyncSession = Depends(get_session),
) -> ScheduleOut:
    return await service.schedule_for(s, user, project_id, today_seoul())


@router.post(
    "/projects/{project_id}/deals/{deal_id}/dates/{proposal_id}/accept",
    response_model=ScheduleOut,
    summary="낸 공사 날짜 · 새 날짜 수락 → 확정 공정표 새 버전 (겹침 · 다시 냄 · 공정표 바뀜은 409)",
)
async def accept_dates(
    project_id: uuid.UUID,
    deal_id: uuid.UUID,
    proposal_id: uuid.UUID,
    data: AcceptIn,
    user: User = Depends(customer_only),
    s: AsyncSession = Depends(get_session),
) -> ScheduleOut:
    return await service.accept_dates(
        s, user, project_id, deal_id, proposal_id, data, today_seoul()
    )


@router.post(
    "/projects/{project_id}/deals/{deal_id}/date-requests",
    status_code=201,
    response_model=ScheduleOut,
    summary="다른 공사 날짜 요청 (2번까지, 업체는 다음 날 18:00까지 다시 낸다)",
)
async def request_dates(
    project_id: uuid.UUID,
    deal_id: uuid.UUID,
    data: DateRequestIn,
    user: User = Depends(customer_only),
    s: AsyncSession = Depends(get_session),
) -> ScheduleOut:
    return await service.request_dates(s, user, project_id, deal_id, data, today_seoul())


@router.get(
    "/vendor/jobs/{deal_id}", response_model=JobOut, summary="공사 날짜 내기 화면 (내 공사만)"
)
async def job(
    deal_id: uuid.UUID,
    vendor: User = Depends(approved_vendor),
    s: AsyncSession = Depends(get_session),
) -> JobOut:
    return await service.job_for(s, vendor, deal_id, today_seoul())


@router.get(
    "/vendor/jobs/{deal_id}/dates/preview",
    response_model=PreviewOut,
    summary="낼 날짜 미리 계산: 일하는 날 · 그 인원의 필요한 기간 · 겹침 (저장하지 않음)",
)
async def preview_dates(
    deal_id: uuid.UUID,
    start: date,
    end: date,
    crew: Annotated[int, Query(ge=1, le=CREW_MAX)],
    vendor: User = Depends(approved_vendor),
    s: AsyncSession = Depends(get_session),
) -> PreviewOut:
    return await service.preview_dates(s, vendor, deal_id, start, end, crew, today_seoul())


@router.post(
    "/vendor/jobs/{deal_id}/dates",
    response_model=JobOut,
    summary="공사 날짜 내기 · 다시 내기 (고객이 수락하기 전까지)",
)
async def propose_dates(
    deal_id: uuid.UUID,
    data: DatesIn,
    vendor: User = Depends(approved_vendor),
    s: AsyncSession = Depends(get_session),
) -> JobOut:
    return await service.propose_dates(s, vendor, deal_id, data, today_seoul())
