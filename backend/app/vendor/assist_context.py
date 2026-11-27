"""업체 도우미에 필요한 소유권·업무 한도만 읽는다. 조회 과정에서 저장하지 않는다."""

from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.calc.schedule import JOB_MAX_DAYS
from app.project.models import Project
from app.quote.models import VendorQuote
from app.refprice.holidays import HOLIDAYS
from app.schedule.models import CREW_MAX, Deal, Plan
from app.shared.clock import now_utc, today_seoul
from app.shared.errors import AppError
from app.vendor import service as vendor_service
from app.vendor.assist_schemas import AssistContext, AssistRequest
from app.visit.models import Visit

# 실제 일정·추가 금액 서비스에서 허용한 상태. 해석은 이 상태에서도 저장하지 않는다.
EXTRA_OPEN = frozenset(("scheduled", "fixing"))
QUOTE_OPEN_V3 = frozenset(("booked", "visited", "quoted"))
QUOTE_OPEN_V2 = frozenset(("requested", "quoted"))


async def _visit_for(s: AsyncSession, vendor_id: UUID, visit_id: UUID) -> Visit:
    v = await s.get(Visit, visit_id)
    if v is None or v.vendor_id != vendor_id:
        raise AppError(404, "not_found", "실측 요청을 찾을 수 없어요.")
    return v


async def _deal_for(s: AsyncSession, vendor_id: UUID, deal_id: UUID) -> Deal:
    d = await s.get(Deal, deal_id)
    if d is None or d.vendor_id != vendor_id:
        raise AppError(404, "not_found", "확정한 공사를 찾을 수 없어요.")
    return d


async def _weekend_work(s: AsyncSession, d: Deal) -> bool:
    """현재 공사 정보에 맞는 예상 공정표만 따른다. 없으면 기본 주말 휴무."""
    project = await s.get(Project, d.project_id)
    plan = await s.scalar(
        select(Plan)
        .where(Plan.project_id == d.project_id, Plan.kind == "expected")
        .order_by(Plan.version.desc())
        .limit(1)
    )
    return bool(project and plan and plan.project_rev == project.rev and plan.weekend_work)


async def load_context(s: AsyncSession, vendor: User, data: AssistRequest) -> AssistContext:
    """API 전용 읽기 경계. 반환 객체에는 ID·고객 개인정보가 없다."""
    today = today_seoul()
    context = AssistContext(today=today)

    if data.form in ("period", "quote"):
        v = await _visit_for(s, vendor.id, data.resource_id)
        if data.form == "period":
            if v.wish_start is None or v.status != "requested":
                raise AppError(409, "invalid_state", "지금은 기간을 답할 수 없어요.")
            if v.reply_due_at is not None and v.reply_due_at <= now_utc():
                raise AppError(409, "expired", "기간을 답할 기한이 지났어요.")
            context.base_month = v.wish_start
        else:
            allowed = QUOTE_OPEN_V3 if v.wish_start is not None else QUOTE_OPEN_V2
            if v.status not in allowed:
                raise AppError(409, "invalid_state", "지금은 견적서 내용을 적을 수 없어요.")
            tax = await vendor_service.tax_kind_of(s, vendor.id)
            context.vat_modes = (
                ["exempt"] if tax == "exempt" else ["included", "excluded", "exempt"]
            )
            context.days_max = JOB_MAX_DAYS
            context.base_month = v.wish_start
        return context

    d = await _deal_for(s, vendor.id, data.resource_id)
    if d.status == "reselected":
        raise AppError(409, "invalid_state", "다시 고른 업체의 공사예요.")

    if data.form == "extra":
        if d.status not in EXTRA_OPEN:
            raise AppError(409, "invalid_state", "공사 중에만 추가 금액을 적을 수 있어요.")
        tax = await vendor_service.tax_kind_of(s, vendor.id)
        context.vat_modes = ["exempt"] if tax == "exempt" else ["included", "excluded", "exempt"]
        return context

    if d.status not in ("confirmed", "scheduled"):
        raise AppError(409, "invalid_state", "지금은 공사 날짜를 낼 수 없어요.")
    quote = await s.get(VendorQuote, d.quote_id)
    context.required_days = quote.days if quote is not None else None
    context.days_max = JOB_MAX_DAYS
    context.crew_max = CREW_MAX
    context.no_weekend = not await _weekend_work(s, d)
    context.base_month = d.start_date or today
    end = today + timedelta(days=365)
    context.holidays = [day for day in HOLIDAYS if today <= day <= end]
    return context
