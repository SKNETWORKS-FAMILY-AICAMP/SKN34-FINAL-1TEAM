import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_role
from app.auth.models import User
from app.db import get_session
from app.shared.clock import today_seoul
from app.visit import service
from app.visit.schemas import VendorDetailOut, VendorListOut

router = APIRouter(tags=["업체 고르기"])
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
