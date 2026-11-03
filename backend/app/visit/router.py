import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import AwareDatetime
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_role
from app.auth.models import User
from app.db import get_session
from app.quote.schemas import QuoteDraftIn
from app.shared.clock import today_seoul
from app.vendor.deps import approved_vendor
from app.visit import service
from app.visit.schemas import (
    DeclineIn,
    InboxSummaryOut,
    QuoteBoardOut,
    VendorDetailOut,
    VendorListOut,
    VendorVisitDetailOut,
    VendorVisitPage,
    VisitCreateIn,
    VisitCreateOut,
    VisitListOut,
    VisitOut,
)

router = APIRouter(tags=["업체 고르기 · 실측 요청"])
customer_only = require_role("customer")
Trade = Literal["dobae", "mokgong"]


@router.get(
    "/projects/{project_id}/vendors",
    response_model=VendorListOut,
    summary="이 집 업체 고르기 · 가견적 낮은 순 (내 프로젝트만)",
)
async def vendors(
    project_id: uuid.UUID,
    trade: Trade,
    sort: Literal["estimate"] = "estimate",
    user: User = Depends(customer_only),
    s: AsyncSession = Depends(get_session),
) -> VendorListOut:
    return await service.vendors_for(s, user, project_id, trade)


@router.get(
    "/projects/{project_id}/vendors/{vendor_id}",
    response_model=VendorDetailOut,
    summary="이 집 기준 업체 자세히 · 가견적 계산 (목록 밖 업체는 404)",
)
async def vendor_detail(
    project_id: uuid.UUID,
    vendor_id: uuid.UUID,
    trade: Trade,
    user: User = Depends(customer_only),
    s: AsyncSession = Depends(get_session),
) -> VendorDetailOut:
    return await service.vendor_detail(s, user, project_id, vendor_id, trade, today_seoul())


@router.get(
    "/projects/{project_id}/vendors/{vendor_id}/photos/{file_id}",
    summary="업체 시공 사진 보기 (이 집에서 고를 수 있는 업체만)",
)
async def vendor_photo(
    project_id: uuid.UUID,
    vendor_id: uuid.UUID,
    file_id: uuid.UUID,
    user: User = Depends(customer_only),
    s: AsyncSession = Depends(get_session),
) -> Response:
    return await service.vendor_photo(s, user, project_id, vendor_id, file_id)


@router.post(
    "/projects/{project_id}/visits",
    status_code=201,
    response_model=VisitCreateOut,
    summary="실측 요청 보내기 · 공정마다 3곳까지, 연락처는 고른 업체에만",
)
async def request_visits(
    project_id: uuid.UUID,
    data: VisitCreateIn,
    user: User = Depends(customer_only),
    s: AsyncSession = Depends(get_session),
) -> VisitCreateOut:
    return await service.request_visits(s, user, project_id, data, today_seoul())


@router.get(
    "/projects/{project_id}/visits",
    response_model=VisitListOut,
    summary="이 집 실측 요청 · 업체 연락처 · 보낼 문구 · 남은 칸",
)
async def visits(
    project_id: uuid.UUID,
    trade: Trade | None = None,
    user: User = Depends(customer_only),
    s: AsyncSession = Depends(get_session),
) -> VisitListOut:
    return await service.visits_for(s, user, project_id, trade, today_seoul())


@router.post(
    "/projects/{project_id}/visits/{visit_id}/cancel",
    response_model=VisitOut,
    summary="실측 요청 취소 (견적서가 오기 전까지)",
)
async def cancel_visit(
    project_id: uuid.UUID,
    visit_id: uuid.UUID,
    user: User = Depends(customer_only),
    s: AsyncSession = Depends(get_session),
) -> VisitOut:
    return await service.cancel(s, user, project_id, visit_id, today_seoul())


@router.get(
    "/vendor/visits",
    response_model=VendorVisitPage,
    summary="업체 작업함 · 할 일 · 기다림 · 끝남 (끝남은 before 커서로 limit씩)",
)
async def vendor_inbox(
    tab: Literal["todo", "waiting", "done"] = "todo",
    before: AwareDatetime | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    vendor: User = Depends(approved_vendor),
    s: AsyncSession = Depends(get_session),
) -> VendorVisitPage:
    return await service.inbox(s, vendor, tab, today_seoul(), before, limit)


@router.get("/vendor/visits/summary", response_model=InboxSummaryOut, summary="업체 작업함 탭별 수")
async def vendor_inbox_summary(
    vendor: User = Depends(approved_vendor), s: AsyncSession = Depends(get_session)
) -> InboxSummaryOut:
    return await service.inbox_summary(s, vendor)


@router.get(
    "/vendor/visits/{visit_id}",
    response_model=VendorVisitDetailOut,
    summary="받은 실측 요청 자세히 (고객 연락처는 보낸 요청이 열려 있을 때만)",
)
async def vendor_visit(
    visit_id: uuid.UUID,
    vendor: User = Depends(approved_vendor),
    s: AsyncSession = Depends(get_session),
) -> VendorVisitDetailOut:
    return await service.vendor_visit(s, vendor, visit_id, today_seoul())


@router.post(
    "/vendor/visits/{visit_id}/decline",
    response_model=VendorVisitDetailOut,
    summary="이 요청 못 받아요 (견적서를 보내기 전까지)",
)
async def decline_visit(
    visit_id: uuid.UUID,
    data: DeclineIn,
    vendor: User = Depends(approved_vendor),
    s: AsyncSession = Depends(get_session),
) -> VendorVisitDetailOut:
    return await service.decline(s, vendor, visit_id, data.code, today_seoul())


@router.post(
    "/vendor/visits/{visit_id}/seen", status_code=204, summary="받은 요청 열어 봄 ('새 요청' 지움)"
)
async def seen_visit(
    visit_id: uuid.UUID,
    vendor: User = Depends(approved_vendor),
    s: AsyncSession = Depends(get_session),
) -> Response:
    await service.mark_seen(s, vendor, visit_id)
    return Response(status_code=204)


@router.get(
    "/vendor/visits/{visit_id}/quote",
    response_model=QuoteBoardOut,
    summary="실측 견적서 올리기 · 임시 저장본 · 보낸 버전 · 칸 한도",
)
async def quote_board(
    visit_id: uuid.UUID,
    vendor: User = Depends(approved_vendor),
    s: AsyncSession = Depends(get_session),
) -> QuoteBoardOut:
    return await service.quote_board(s, vendor, visit_id)


@router.put(
    "/vendor/visits/{visit_id}/quote",
    response_model=QuoteBoardOut,
    summary="실측 견적서 임시 저장 (통째로, 요청함 · 견적서 옴일 때만)",
)
async def save_quote(
    visit_id: uuid.UUID,
    data: QuoteDraftIn,
    vendor: User = Depends(approved_vendor),
    s: AsyncSession = Depends(get_session),
) -> QuoteBoardOut:
    return await service.save_quote(s, vendor, visit_id, data, today_seoul())


@router.post(
    "/vendor/visits/{visit_id}/quote/send",
    response_model=QuoteBoardOut,
    summary="실측 견적서 보내기 (빠진 칸은 칸별 422, 이미 보냈으면 409)",
)
async def send_quote(
    visit_id: uuid.UUID,
    vendor: User = Depends(approved_vendor),
    s: AsyncSession = Depends(get_session),
) -> QuoteBoardOut:
    return await service.send_quote(s, vendor, visit_id, today_seoul())
