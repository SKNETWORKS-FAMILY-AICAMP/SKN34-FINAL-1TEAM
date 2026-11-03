"""이 집 기준 업체 고르기 · 업체 자세히.

업체는 승인됐고 그 공정을 하고 이 집 지역(구)에서 일하는 곳만 보인다.
가견적은 업체가 확인한 단가 × 이 집 표준견적서 수량(계산 엔진)이다. 확인한 단가가 없거나 이 집에
필요한 줄의 단가가 하나라도 빠졌으면 '가견적 없음'이다. 연락처는 여기서 보이지 않는다.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.calc.quote_diff import percent_vs
from app.calc.schedule import years_since
from app.files import service as files_service
from app.project import service as project_service
from app.project.schemas import ProjectOut
from app.quote import service as quote_service
from app.quote.models import StandardQuote, StandardQuoteLine
from app.shared.clock import SEOUL
from app.shared.errors import AppError
from app.vendor import service as vendor_service
from app.vendor.schemas import EstimateOut
from app.visit.schemas import (
    Direction,
    EstimateDetailOut,
    VendorDetailOut,
    VendorListOut,
    VendorRowOut,
)

NOT_LISTED = "이 집에서 고를 수 있는 업체가 아니에요."


def _day(at: datetime | None) -> date | None:
    return at.astimezone(SEOUL).date() if at else None


async def _standard(
    s: AsyncSession, user: User, project_id: uuid.UUID, trade: str
) -> tuple[ProjectOut, StandardQuote, list[StandardQuoteLine]]:
    """내 프로젝트(남의 것은 404)와 그 공정의 최신 표준견적서 · 줄"""
    p = await project_service.get_for(s, user, project_id)
    got = await quote_service.standard_trade(s, p, trade)
    if got is None:
        raise AppError(422, "trade", "이 집에서 고른 공정이 아니에요.")
    return p, *got


def _estimate(
    trade: str,
    std: StandardQuote,
    lines: list[StandardQuoteLine],
    cp: vendor_service.ConfirmedPrices | None,
) -> EstimateOut | None:
    """확인한 단가가 없거나 이 집에 필요한 줄의 단가가 빠졌으면 가견적 없음(None)"""
    if cp is None:
        return None
    e = vendor_service.estimate_out(
        trade, lines, cp.prices, grade=std.grade, tax_kind=cp.tax_kind, sources=cp.sources
    )
    return None if e.missing or not e.lines else e


def _vs(total: int, standard: int) -> tuple[Decimal | None, Direction | None]:
    if standard <= 0:
        return None, None
    pct = percent_vs(total, standard)
    return pct, "less" if pct < 0 else "more" if pct > 0 else "same"


async def vendors_for(
    s: AsyncSession, user: User, project_id: uuid.UUID, trade: str
) -> VendorListOut:
    """업체 고르기: 가견적 낮은 순, 가견적 없음은 맨 뒤, 같으면 상호 순."""
    p, std, lines = await _standard(s, user, project_id, trade)
    cards = await vendor_service.list_public(s, trade, p.sido, p.sgg)
    prices = await vendor_service.confirmed_prices(s, [c.vendor_id for c in cards], trade)
    rows = []
    for c in cards:
        e = _estimate(trade, std, lines, prices.get(c.vendor_id))
        pct, way = _vs(e.total, std.total) if e else (None, None)
        rows.append(
            VendorRowOut(
                vendor_id=c.vendor_id,
                biz_name=c.biz_name,
                trades=c.trades,
                areas=c.areas,
                verified_on=_day(c.verified_at),
                visit_fee=c.visit_fee,
                visit_fee_refund=c.visit_fee_refund,
                contact_method=c.contact_method,
                call_start=c.call_start,
                call_end=c.call_end,
                estimate_total=e.total if e else None,
                vs_standard=pct,
                vs_direction=way,
            )
        )
    rows.sort(key=lambda r: (r.estimate_total is None, r.estimate_total or 0, r.biz_name or ""))
    return VendorListOut(
        project_id=p.id,
        trade=trade,
        region=p.region,
        area_name=p.sgg,
        standard_total=std.total,
        vendors=rows,
    )


async def vendor_detail(
    s: AsyncSession,
    user: User,
    project_id: uuid.UUID,
    vendor_id: uuid.UUID,
    trade: str,
    today: date,
) -> VendorDetailOut:
    p, std, lines = await _standard(s, user, project_id, trade)
    if not await vendor_service.listed(s, vendor_id, trade, p.sido, p.sgg):
        raise AppError(404, "not_found", NOT_LISTED)
    profile = await vendor_service.public_profile(s, vendor_id)
    cp = (await vendor_service.confirmed_prices(s, [vendor_id], trade)).get(vendor_id)
    e = _estimate(trade, std, lines, cp)
    estimate = None
    if e is not None and cp is not None:
        pct, way = _vs(e.total, std.total)
        estimate = EstimateDetailOut(
            **e.model_dump(),
            set_id=cp.set_id,
            confirmed_on=_day(cp.confirmed_at),
            confirmed_at=cp.confirmed_at,
            exempt=cp.tax_kind == "exempt",
            vs_standard=pct,
            vs_direction=way,
        )
    left = set(e.left_out) if e else set()
    not_included = [ln.name for ln in lines if ln.state == "after_visit" or ln.key in left]
    return VendorDetailOut(
        project_id=p.id,
        trade=trade,
        standard_total=std.total,
        profile=profile,
        verified_on=_day(profile.verified_at),
        years_open=years_since(profile.opened_on, today) if profile.opened_on else None,
        in_area=True,  # 목록 조건(이 집 구가 활동 지역에 있음)을 지난 업체만 여기까지 온다
        area_name=p.sgg,
        dong=None,  # 프로젝트에 읍 · 면 · 동 칸이 아직 없다
        estimate=estimate,
        not_included=not_included,
    )


async def vendor_photo(
    s: AsyncSession, user: User, project_id: uuid.UUID, vendor_id: uuid.UUID, file_id: uuid.UUID
) -> Response:
    """이 집에서 고를 수 있는 업체의 시공 사진만 내준다. 근거 파일 · 등록증 · 남의 사진은 404."""
    p = await project_service.get_for(s, user, project_id)
    trades = [t for t in ("dobae", "mokgong") if getattr(p.trades, t) is not None]
    if not any([await vendor_service.listed(s, vendor_id, t, p.sido, p.sgg) for t in trades]):
        raise AppError(404, "not_found", NOT_LISTED)
    f = await vendor_service.portfolio_file(s, vendor_id, file_id)
    if f is None:
        raise AppError(404, "not_found", "사진을 찾을 수 없어요.")
    return await files_service.respond(f)
