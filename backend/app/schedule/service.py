"""공정표: 예상 공정표(집 정보로 계산) · 확정 공정표 · 업체 확정.

예상 공정표는 처음 볼 때 계산해 저장하고, 집 정보(rev) · 산식 버전이 그대로면 저장본을 쓴다.
날짜 · 기간은 모두 계산 엔진(calc.schedule)이 만든다.
"""

import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.calc.common import FORMULA_VERSION
from app.calc.schedule import TradePlan, due_at, lay_out, workdays
from app.notify import service as notify_service
from app.project import service as project_service
from app.project.schemas import ProjectOut
from app.quote import service as quote_service
from app.refprice.holidays import HOLIDAYS, days_off
from app.schedule.models import OPEN_DEALS, Deal, DealEvent, Plan, PlanDay, PlanStep
from app.schedule.schemas import (
    CompareDealOut,
    ConfirmIn,
    ConfirmOut,
    DealOut,
    InboxPageOut,
    InboxRowOut,
    PlanBlockOut,
    PlanDayOut,
    PlanOut,
    PlanRedo,
)
from app.shared.clock import now_utc
from app.shared.errors import AppError
from app.shared.ids import uuid7
from app.vendor import service as vendor_service
from app.visit import service as visit_service
from app.visit.schemas import InboxSummaryOut, ReselectOut

LEAD_DAYS = 14  # 공사 시기를 아직 모를 때: 오늘부터 2주 뒤 첫 일하는 날에 시작한다고 잡는다
LABEL = {"mokgong": "목공", "dobae": "도배"}


def _off(start: date) -> frozenset[date]:
    return days_off(start, start + timedelta(days=400))


def _compute(p: ProjectOut, start: date, weekend_work: bool):
    calcs = quote_service.calc_trades(p)
    trades = [TradePlan(c.trade, c.steps, c.crew) for c in calcs]
    weekend = () if weekend_work else (5, 6)
    return lay_out(trades, start, _off(start), weekend), {c.trade: c.man_day for c in calcs}


def default_start(p: ProjectOut, today: date) -> date:
    """집 정보의 공사 시기로 시작일을 잡는다. 이사 날짜만 있으면 그 전날까지 끝나게 거꾸로 잡는다."""
    earliest = workdays(today + timedelta(days=1), 1, _off(today))[0]
    if p.when.kind == "date" and p.when.start:
        return max(p.when.start, earliest)
    if p.when.kind == "movein" and p.when.start:
        start = p.when.start
        for _ in range(8):  # 끝나는 날이 이사 전날 안쪽이 될 때까지 앞당긴다
            plan, _ = _compute(p, start, False)
            over = (plan.end - p.when.start).days + 1
            if over <= 0:
                break
            start -= timedelta(days=over)
        return max(start, earliest)
    return workdays(today + timedelta(days=LEAD_DAYS), 1, _off(today))[0]


async def _latest(s: AsyncSession, project_id: uuid.UUID, kind: str) -> Plan | None:
    q = (
        select(Plan)
        .where(Plan.project_id == project_id, Plan.kind == kind)
        .order_by(Plan.version.desc())
        .limit(1)
    )
    return (await s.scalars(q)).first()


async def _save(
    s: AsyncSession, p: ProjectOut, start: date, weekend_work: bool, prev: Plan | None
) -> None:
    plan, man_days = _compute(p, start, weekend_work)
    row = Plan(
        project_id=p.id,
        kind="expected",
        version=(prev.version + 1) if prev else 1,
        project_rev=p.rev,
        formula_ver=FORMULA_VERSION,
        start_date=plan.start,
        end_date=plan.end,
        work_days=plan.work_days,
        weekend_work=weekend_work,
    )
    s.add(row)
    await s.flush()
    s.add_all(
        [
            PlanStep(
                plan_id=row.id,
                trade=b.trade,
                kind=b.kind,
                label=b.label or LABEL.get(b.trade or "", ""),
                start_date=b.start,
                end_date=b.end,
                days=b.days,
                crew=b.crew,
                man_day=man_days.get(b.trade) if b.trade else None,
                sort=i,
            )
            for i, b in enumerate(plan.blocks)
        ]
    )
    s.add_all(
        [
            PlanDay(plan_id=row.id, day=d.day, trade=d.trade, kind=d.kind, names=list(d.names))
            for d in plan.days
        ]
    )
    try:
        await s.commit()
    except IntegrityError:  # 동시에 두 번 만들면 먼저 저장한 버전을 쓴다
        await s.rollback()


async def ensure_expected(s: AsyncSession, p: ProjectOut, today: date) -> Plan:
    prev = await _latest(s, p.id, "expected")
    if prev and prev.project_rev == p.rev and prev.formula_ver == FORMULA_VERSION:
        return prev
    await _save(s, p, default_start(p, today), False, prev)
    got = await _latest(s, p.id, "expected")
    assert got is not None
    return got


async def _out(s: AsyncSession, plan: Plan) -> PlanOut:
    steps = (
        await s.scalars(select(PlanStep).where(PlanStep.plan_id == plan.id).order_by(PlanStep.sort))
    ).all()
    days = (
        await s.scalars(select(PlanDay).where(PlanDay.plan_id == plan.id).order_by(PlanDay.day))
    ).all()
    return PlanOut(
        project_id=plan.project_id,
        kind=plan.kind,
        version=plan.version,
        project_rev=plan.project_rev,
        start=plan.start_date,
        end=plan.end_date,
        work_days=plan.work_days,
        weekend_work=plan.weekend_work,
        computed_at=plan.created_at,
        blocks=[
            PlanBlockOut(
                trade=st.trade,
                kind=st.kind,
                label=st.label,
                start=st.start_date,
                end=st.end_date,
                days=st.days,
                crew=st.crew,
                man_day=st.man_day,
            )
            for st in steps
        ],
        days=[
            PlanDayOut(
                day=d.day,
                trade=d.trade,
                kind=d.kind,
                names=list(d.names),
                holiday=HOLIDAYS.get(d.day),
            )
            for d in days
        ],
    )


async def plan_for(s: AsyncSession, user: User, project_id: uuid.UUID, today: date) -> PlanOut:
    """GET /projects/{id}/plan: 만든 사람 · 관리자만. 지금은 예상 공정표를 보여 준다."""
    p = await project_service.get_for(s, user, project_id)
    return await _out(s, await ensure_expected(s, p, today))


async def redo(
    s: AsyncSession, user: User, project_id: uuid.UUID, data: PlanRedo, today: date
) -> PlanOut:
    """날짜 바꿔 보기: 시작일 · 주말 공사를 바꿔 예상 공정표 새 버전을 만든다."""
    p = await project_service.get_for(s, user, project_id)
    if user.role != "customer":
        raise AppError(403, "forbidden", "고객만 공정표를 다시 계산할 수 있어요.")
    if data.start <= today or data.start > today + timedelta(days=730):
        raise AppError(422, "start", "내일부터 2년 안의 날짜를 골라 주세요.")
    prev = await _latest(s, p.id, "expected")
    await _save(s, p, data.start, data.weekend_work, prev)
    got = await _latest(s, p.id, "expected")
    assert got is not None
    return await _out(s, got)


# ---- 업체 확정: 고객이 누른 요청으로만. 요청 상태는 visit.service, 확정(deal)은 여기서 ----

DATES_DUE_DAYS = 2  # 확정하고 2일 뒤 18:00까지 업체가 공사 날짜를 낸다
TRADE_LABEL = LABEL
ALREADY = "이미 업체를 확정했어요."


async def _open_deal(s: AsyncSession, project_id: uuid.UUID, trade: str) -> Deal | None:
    q = select(Deal).where(
        Deal.project_id == project_id, Deal.trade == trade, Deal.status != "reselected"
    )
    return (await s.execute(q.execution_options(populate_existing=True))).scalar_one_or_none()


async def _deal_out(
    s: AsyncSession, deals: list[Deal], today: date, now: datetime
) -> list[DealOut]:
    quotes = await quote_service.briefs_by_id(s, [d.quote_id for d in deals])
    visits = await visit_service.visits_by_id(s, [d.visit_id for d in deals])
    shown = [
        d.vendor_id
        for d in deals
        if d.vendor_id
        and d.visit_id in visits
        and visit_service.contact_visible(
            visits[d.visit_id].status, visits[d.visit_id].contact_until, today
        )
    ]
    contacts = await vendor_service.contacts(s, shown)
    return [
        DealOut(
            id=d.id,
            trade=d.trade,
            status=d.status,
            vendor_id=d.vendor_id,
            vendor_name=d.vendor_name,
            owner_name=contacts[d.vendor_id].owner_name if d.vendor_id in contacts else None,
            visit_id=d.visit_id,
            quote=quotes[d.quote_id],
            confirmed_at=d.created_at,
            dates_due_at=d.dates_due_at,
            overdue=d.status == "confirmed" and now > d.dates_due_at,
            start_date=d.start_date,
            end_date=d.end_date,
            work_done_on=d.work_done_on,
        )
        for d in deals
    ]


async def confirm(
    s: AsyncSession,
    user: User,
    project_id: uuid.UUID,
    visit_id: uuid.UUID,
    data: ConfirmIn,
    today: date,
) -> ConfirmOut:
    """업체 확정 (한 트랜잭션): 프로젝트 잠금 → 요청 quoted → chosen → 그 버전으로 확정 →
    같은 공정의 다른 요청 닫기 → 이력 · 알림. 공정마다 살아 있는 확정은 하나(부분 UQ)."""
    p = await project_service.get_for(s, user, project_id)
    await project_service.lock_for_visit(s, p.id)  # 같은 집의 확정 · 요청이 줄 선다
    v = await visit_service.in_project(s, p.id, visit_id)
    if await _open_deal(s, p.id, v.trade) is not None:
        raise AppError(409, "already_chosen", ALREADY)
    await visit_service.choose(s, user, v, data.seen_ver)
    quote = await quote_service.sent_by_version(s, v.id, data.seen_ver)
    if quote is None:
        raise AppError(409, "quote_changed", visit_service.QUOTE_CHANGED)
    now = now_utc()
    deal = Deal(
        id=uuid7(),
        project_id=p.id,
        trade=v.trade,
        vendor_id=v.vendor_id,
        vendor_name=v.vendor_name,
        visit_id=v.id,
        quote_id=quote.id,
        status="confirmed",
        dates_due_at=due_at(now, DATES_DUE_DAYS),
        created_at=now,
    )
    try:
        s.add(deal)
        await s.flush()
        s.add(DealEvent(deal_id=deal.id, from_status=None, to_status="confirmed", by_id=user.id))
        closed = await visit_service.close_others(s, p.id, v.trade, v.id, user)
        if v.vendor_id is not None:
            await notify_service.add(
                s,
                user_id=v.vendor_id,
                kind="deal_confirmed",
                body="고객이 확정했어요 · 공사 날짜를 내 주세요",
                link=f"/vendor/jobs/{deal.id}",
                dedup_key=f"deal:{deal.id}",
            )
        await s.commit()
    except IntegrityError as exc:  # 같은 공정을 동시에 두 번 확정하면 부분 UQ가 막는다
        await s.rollback()
        raise AppError(409, "already_chosen", ALREADY) from exc
    deal = await _open_deal(
        s, p.id, v.trade
    )  # 저장된 값으로 다시 읽는다 (시각 표기를 읽기와 맞춘다)
    (out,) = await _deal_out(s, [deal], today, now)
    expired = quote.valid_until is not None and quote.valid_until < today
    return ConfirmOut(deal=out, closed=closed, quote_expired=expired)


AFTER_DATES = "공사 날짜를 수락한 뒤에는 업체를 다시 고를 수 없어요."


async def reselect(
    s: AsyncSession, user: User, project_id: uuid.UUID, deal_id: uuid.UUID
) -> ReselectOut:
    """다시 고르기 (공사 날짜 수락 전만, 한 트랜잭션): 프로젝트 잠금 → 확정 confirmed → reselected →
    확정한 요청 · 남은 요청 닫기(reselected) → 이력 · 알림. 공정의 칸 3개가 다시 빈다."""
    p = await project_service.get_for(s, user, project_id)
    await project_service.lock_for_visit(s, p.id)  # 같은 집의 확정 · 요청 · 다시 고르기가 줄 선다
    deal = await s.get(Deal, deal_id, populate_existing=True)
    if deal is None or deal.project_id != p.id:
        raise AppError(404, "not_found", "업체 확정을 찾을 수 없어요.")
    if deal.status == "reselected":
        raise AppError(409, "invalid_state", "이미 업체를 다시 고르기로 했어요.")
    if deal.status != "confirmed":
        raise AppError(409, "invalid_state", AFTER_DATES)
    res = await s.execute(
        update(Deal)
        .where(Deal.id == deal.id, Deal.status == "confirmed")
        .values(status="reselected")
        .execution_options(synchronize_session=False)
    )
    if res.rowcount != 1:  # 그사이 공사 날짜를 수락했다
        raise AppError(409, "invalid_state", AFTER_DATES)
    s.add(
        DealEvent(deal_id=deal.id, from_status="confirmed", to_status="reselected", by_id=user.id)
    )
    out = await visit_service.close_for_reselect(s, p.id, deal.trade, user)
    if deal.vendor_id is not None:
        await notify_service.add(
            s,
            user_id=deal.vendor_id,
            kind="deal_reselected",
            body=visit_service.RESELECTED,
            link=f"/vendor/requests/{deal.visit_id}",
            dedup_key=f"deal:{deal.id}:reselected",
        )
    await s.commit()
    return out


async def deals_for(
    s: AsyncSession, user: User, project_id: uuid.UUID, trade: str | None, today: date
) -> list[DealOut]:
    """이 집의 업체 확정 (다시 고르기 한 것은 빼고)"""
    p = await project_service.get_for(s, user, project_id)
    q = select(Deal).where(Deal.project_id == p.id, Deal.status != "reselected")
    if trade:
        q = q.where(Deal.trade == trade)
    deals = list((await s.execute(q.order_by(Deal.created_at))).scalars().all())
    return await _deal_out(s, deals, today, now_utc())


async def compare(
    s: AsyncSession, user: User, project_id: uuid.UUID, trade: str, today: date
) -> CompareDealOut:
    base = await visit_service.compare(s, user, project_id, trade, today)
    deal = await _open_deal(s, project_id, trade)
    out = (await _deal_out(s, [deal], today, now_utc()))[0] if deal else None
    return CompareDealOut(**base.model_dump(), deal=out)


async def vendor_inbox(
    s: AsyncSession,
    vendor: User,
    tab: str,
    today: date,
    before: datetime | None,
    limit: int,
) -> InboxPageOut:
    """업체 작업함. 공사 탭은 확정한 공사(끝남 · 다시 고르기 빼고), 확정한 순 최근 먼저."""
    if tab != "jobs":
        page = await visit_service.inbox(s, vendor, tab, today, before, limit)
        return InboxPageOut(
            items=[InboxRowOut(**r.model_dump()) for r in page.items], next_before=page.next_before
        )
    q = (
        select(Deal)
        .where(Deal.vendor_id == vendor.id, Deal.status.in_(OPEN_DEALS))
        .order_by(Deal.created_at.desc())
    )
    deals = list((await s.execute(q)).scalars().all())
    rows = await visit_service.rows_for(s, [d.visit_id for d in deals], today)
    now = now_utc()
    return InboxPageOut(
        items=[
            InboxRowOut(
                **rows[d.visit_id].model_dump(),
                deal_id=d.id,
                deal_status=d.status,
                confirmed_at=d.created_at,
                dates_due_at=d.dates_due_at,
                overdue=d.status == "confirmed" and now > d.dates_due_at,
                start_date=d.start_date,
                end_date=d.end_date,
            )
            for d in deals
            if d.visit_id in rows
        ],
        next_before=None,
    )


async def vendor_summary(s: AsyncSession, vendor: User) -> InboxSummaryOut:
    base = await visit_service.inbox_summary(s, vendor)
    n = await s.scalar(
        select(func.count())
        .select_from(Deal)
        .where(Deal.vendor_id == vendor.id, Deal.status.in_(OPEN_DEALS))
    )
    return base.model_copy(update={"jobs": int(n or 0)})
