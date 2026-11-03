"""공정표: 예상 공정표(집 정보로 계산) · 확정 공정표.

예상 공정표는 처음 볼 때 계산해 저장하고, 집 정보(rev) · 산식 버전이 그대로면 저장본을 쓴다.
날짜 · 기간은 모두 계산 엔진(calc.schedule)이 만든다.
"""

import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.calc.common import FORMULA_VERSION
from app.calc.schedule import TradePlan, lay_out, workdays
from app.project import service as project_service
from app.project.schemas import ProjectOut
from app.quote import service as quote_service
from app.refprice.holidays import HOLIDAYS, days_off
from app.schedule.models import Plan, PlanDay, PlanStep
from app.schedule.schemas import PlanBlockOut, PlanDayOut, PlanOut, PlanRedo
from app.shared.errors import AppError

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
