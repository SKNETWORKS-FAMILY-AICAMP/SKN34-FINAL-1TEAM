import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import service as auth_service
from app.auth.deps import require_role, set_session_cookie
from app.auth.models import User
from app.auth.schemas import MeOut
from app.db import get_session
from app.shared.clock import today_seoul
from app.shared.ratelimit import client_ip
from app.vendor import service
from app.vendor.deps import approved_vendor
from app.vendor.schemas import (
    PortfolioIn,
    PortfolioOut,
    PriceFileIn,
    PriceFileOut,
    PriceSetIn,
    PricesOut,
    ProfileIn,
    ProfileOut,
    RejectIn,
    VendorAdminOut,
    VendorSignupIn,
)

router = APIRouter(tags=["업체"])
admin_only = require_role("admin")


@router.post(
    "/auth/vendor-signup",
    status_code=201,
    response_model=MeOut,
    summary="업체 회원가입 · 기본 정보 + 사업자등록증, 승인대기로 시작",
)
async def vendor_signup(
    request: Request,
    response: Response,
    name: Annotated[str, Form(description="담당자 이름")],
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    license_file: Annotated[UploadFile, File(description="사업자등록증 (jpg · png · pdf)")],
    s: AsyncSession = Depends(get_session),
) -> MeOut:
    try:
        body = VendorSignupIn(name=name, email=email, password=password)
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_input=False)
        raise RequestValidationError([{**e, "loc": ("body", *e["loc"])} for e in errors]) from exc
    user, token = await service.signup(s, body, license_file, client_ip(request))
    set_session_cookie(response, token)
    return await auth_service.me(s, user)


@router.get("/admin/vendors", response_model=list[VendorAdminOut], summary="업체관리 · 상태별 목록")
async def list_vendors(
    status: Literal["pending", "approved", "rejected"] | None = None,
    _: User = Depends(admin_only),
    s: AsyncSession = Depends(get_session),
) -> list[VendorAdminOut]:
    return await service.list_for_admin(s, status)


@router.get("/admin/vendors/{vendor_id}", response_model=VendorAdminOut, summary="업체 상세")
async def get_vendor(
    vendor_id: uuid.UUID, _: User = Depends(admin_only), s: AsyncSession = Depends(get_session)
) -> VendorAdminOut:
    return await service.get_for_admin(s, vendor_id)


@router.post(
    "/admin/vendors/{vendor_id}/approve",
    response_model=VendorAdminOut,
    summary="업체 가입 승인",
)
async def approve(
    vendor_id: uuid.UUID, admin: User = Depends(admin_only), s: AsyncSession = Depends(get_session)
) -> VendorAdminOut:
    return await service.approve(s, admin, vendor_id)


@router.post(
    "/admin/vendors/{vendor_id}/reject",
    response_model=VendorAdminOut,
    summary="업체 가입 반려",
)
async def reject(
    vendor_id: uuid.UUID,
    data: RejectIn,
    admin: User = Depends(admin_only),
    s: AsyncSession = Depends(get_session),
) -> VendorAdminOut:
    return await service.reject(s, admin, vendor_id, data.reason)


@router.get("/vendor/profile", response_model=ProfileOut, summary="업체 정보 (승인된 업체 본인)")
async def profile(
    vendor: User = Depends(approved_vendor), s: AsyncSession = Depends(get_session)
) -> ProfileOut:
    return await service.get_profile(s, vendor)


@router.put(
    "/vendor/profile",
    response_model=ProfileOut,
    summary="업체 정보 저장 · 통째로 (상호 · 대표자 · 사업자번호는 못 바꿈, updated_at이 다르면 409)",
)
async def save_profile(
    data: ProfileIn,
    vendor: User = Depends(approved_vendor),
    s: AsyncSession = Depends(get_session),
) -> ProfileOut:
    return await service.save_profile(s, vendor, data, today_seoul())


@router.post(
    "/vendor/portfolio",
    status_code=201,
    response_model=PortfolioOut,
    summary="시공 사진 더하기 (먼저 POST /files kind=portfolio)",
)
async def add_portfolio(
    data: PortfolioIn,
    vendor: User = Depends(approved_vendor),
    s: AsyncSession = Depends(get_session),
) -> PortfolioOut:
    return await service.add_portfolio(s, vendor, data, today_seoul())


@router.delete("/vendor/portfolio/{item_id}", status_code=204, summary="시공 사진 빼기")
async def remove_portfolio(
    item_id: uuid.UUID,
    vendor: User = Depends(approved_vendor),
    s: AsyncSession = Depends(get_session),
) -> Response:
    await service.remove_portfolio(s, vendor, item_id)
    return Response(status_code=204)


TradeParam = Literal["dobae", "mokgong"]


@router.get(
    "/vendor/prices",
    response_model=PricesOut,
    summary="가견적 단가 · 근거 파일 (고치는 중 · 확인한 묶음, 고객에게 보이는 모습 예)",
)
async def prices(
    trade: TradeParam,
    vendor: User = Depends(approved_vendor),
    s: AsyncSession = Depends(get_session),
) -> PricesOut:
    return await service.prices_for(s, vendor, trade)


@router.put(
    "/vendor/prices/{trade}",
    response_model=PricesOut,
    summary="가견적 단가 저장 (고치는 중인 묶음, 확인 전에는 고객에게 안 씀)",
)
async def save_prices(
    trade: TradeParam,
    data: PriceSetIn,
    vendor: User = Depends(approved_vendor),
    s: AsyncSession = Depends(get_session),
) -> PricesOut:
    return await service.save_prices(s, vendor, trade, data)


@router.post(
    "/vendor/prices/{trade}/confirm",
    response_model=PricesOut,
    summary="가견적 단가 확인했어요 (고치는 중 → 고객 가견적에 씀, 예전 묶음은 보관)",
)
async def confirm_prices(
    trade: TradeParam,
    vendor: User = Depends(approved_vendor),
    s: AsyncSession = Depends(get_session),
) -> PricesOut:
    return await service.confirm_prices(s, vendor, trade)


@router.post(
    "/vendor/price-files",
    status_code=201,
    response_model=PriceFileOut,
    summary="가견적 근거 파일 붙이기 (먼저 POST /files kind=price_doc)",
)
async def add_price_file(
    data: PriceFileIn,
    vendor: User = Depends(approved_vendor),
    s: AsyncSession = Depends(get_session),
) -> PriceFileOut:
    return await service.add_price_file(s, vendor, data)


@router.delete("/vendor/price-files/{file_id}", status_code=204, summary="가견적 근거 파일 빼기")
async def remove_price_file(
    file_id: uuid.UUID,
    vendor: User = Depends(approved_vendor),
    s: AsyncSession = Depends(get_session),
) -> Response:
    await service.remove_price_file(s, vendor, file_id)
    return Response(status_code=204)
