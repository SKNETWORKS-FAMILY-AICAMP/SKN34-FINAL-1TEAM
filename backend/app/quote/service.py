"""표준견적서(믿집 계산)와 업체 실측 견적서.

표준견적서는 처음 볼 때 계산해 저장하고, 집 정보(rev) · 산식 · 단가 버전이 그대로면 저장본을 쓴다.
하나라도 바뀌면 새 버전을 쌓는다(예전 버전은 남는다). 숫자는 모두 계산 엔진이 만든다.
"""

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.calc import dobae, mokgong
from app.calc.common import FORMULA_VERSION, Step
from app.calc.quote_diff import budget_gap
from app.calc.standard_quote import house_area, house_pyeong
from app.project import service as project_service
from app.project.schemas import ProjectOut
from app.quote.models import StandardQuote, StandardQuoteLine
from app.quote.schemas import BudgetOut, StandardLineOut, StandardOut, StandardTradeOut
from app.refprice.book import DOBAE_2026H2, MOKGONG_2026H2
from app.refprice.house import HOUSE_2026H2

PRICE_VER = DOBAE_2026H2.version
ROOM = "방"  # 방문 개수: 목공을 고른 공간 중 이름에 '방'이 든 곳


@dataclass(frozen=True)
class TradeCalc:
    """공정 하나를 이 집 정보로 계산한 결과(저장하기 전)."""

    trade: str
    grade: str | None
    lines: list
    supply: int
    vat: int
    total: int
    man_day: Decimal
    crew: int
    steps: list[Step]
    notes: list[str]


def _area(p: ProjectOut) -> tuple[Decimal, str]:
    return house_area(p.size.band, p.size.m2, p.size.basis, HOUSE_2026H2)


def calc_trades(p: ProjectOut) -> list[TradeCalc]:
    """이 집 정보로 고른 공정을 계산한다(목공 → 도배 순서). 공정표도 이 결과를 쓴다."""
    area, basis = _area(p)
    out: list[TradeCalc] = []
    if p.trades.mokgong:
        names = {sp.id: sp.name for sp in p.spaces}
        rooms = sum(1 for sid in p.trades.mokgong.spaces if ROOM in names.get(sid, ""))
        items = tuple(p.trades.mokgong.items)
        m = mokgong.quote(
            mokgong.MokgongInput(
                area, basis, items, p.occupied, doors=max(rooms, 1) if "door" in items else 0
            ),
            MOKGONG_2026H2,
        )
        notes = list(m.assumptions)
        if "door" in items:
            notes.append(f"방문은 목공을 고른 방 수({max(rooms, 1)}개)로 잡았어요.")
        out.append(
            TradeCalc(
                "mokgong",
                None,
                m.lines,
                m.subtotal,
                m.vat,
                m.total,
                m.man_days,
                MOKGONG_2026H2.crew,
                m.steps,
                notes,
            )
        )
    if p.trades.dobae:
        d = dobae.quote(
            dobae.DobaeInput(area, basis, p.trades.dobae.grade, True, p.occupied, p.balcony_ext),
            DOBAE_2026H2,
        )
        out.append(
            TradeCalc(
                "dobae",
                p.trades.dobae.grade,
                d.lines,
                d.subtotal,
                d.vat,
                d.total,
                d.man_days,
                DOBAE_2026H2.crew,
                d.steps,
                list(d.assumptions),
            )
        )
    return out


async def _latest(s: AsyncSession, project_id: uuid.UUID) -> dict[str, StandardQuote]:
    sub = (
        select(StandardQuote.trade, func.max(StandardQuote.version).label("v"))
        .where(StandardQuote.project_id == project_id)
        .group_by(StandardQuote.trade)
        .subquery()
    )
    q = select(StandardQuote).join(
        sub, (StandardQuote.trade == sub.c.trade) & (StandardQuote.version == sub.c.v)
    )
    q = q.where(StandardQuote.project_id == project_id)
    return {row.trade: row for row in (await s.scalars(q)).all()}


def _fresh(row: StandardQuote, p: ProjectOut) -> bool:
    return (
        row.project_rev == p.rev
        and row.formula_ver == FORMULA_VERSION
        and row.price_ver == PRICE_VER
    )


async def ensure_standard(s: AsyncSession, p: ProjectOut) -> dict[str, StandardQuote]:
    """이 집의 최신 표준견적서(공정별). 없거나 바뀌었으면 새 버전을 저장하고 commit한다."""
    latest = await _latest(s, p.id)
    wanted = {t.trade: t for t in calc_trades(p)}
    stale = [t for t in wanted if t not in latest or not _fresh(latest[t], p)]
    if not stale:
        return {t: latest[t] for t in wanted}
    area, basis = _area(p)
    pyeong = house_pyeong(p.size.band, p.size.m2, p.size.basis, HOUSE_2026H2)
    for trade in stale:
        c = wanted[trade]
        row = StandardQuote(
            project_id=p.id,
            trade=trade,
            version=(latest[trade].version + 1) if trade in latest else 1,
            project_rev=p.rev,
            formula_ver=FORMULA_VERSION,
            price_ver=PRICE_VER,
            grade=c.grade,
            area_m2=area,
            area_basis=basis,
            pyeong=pyeong,
            supply=c.supply,
            vat=c.vat,
            total=c.total,
            man_day=c.man_day,
            crew=c.crew,
            notes=c.notes,
        )
        s.add(row)
        await s.flush()
        s.add_all(
            [
                StandardQuoteLine(
                    quote_id=row.id,
                    key=ln.key,
                    name=ln.name,
                    qty=ln.qty,
                    unit=ln.unit,
                    unit_price=ln.unit_price,
                    amount=ln.amount,
                    state=ln.state,
                    basis=ln.basis,
                    source=ln.source[:120],
                    sort=i,
                )
                for i, ln in enumerate(c.lines)
            ]
        )
    try:
        await s.commit()
    except IntegrityError:  # 같은 집을 동시에 두 번 열면 먼저 저장한 버전을 쓴다
        await s.rollback()
    latest = await _latest(s, p.id)
    return {t: latest[t] for t in wanted if t in latest}


async def _lines(s: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, list[StandardQuoteLine]]:
    rows = (
        await s.scalars(
            select(StandardQuoteLine)
            .where(StandardQuoteLine.quote_id.in_(ids))
            .order_by(StandardQuoteLine.sort)
        )
    ).all()
    out: dict[uuid.UUID, list[StandardQuoteLine]] = {i: [] for i in ids}
    for ln in rows:
        out[ln.quote_id].append(ln)
    return out


async def standard_trade(
    s: AsyncSession, p: ProjectOut, trade: str
) -> tuple[StandardQuote, list[StandardQuoteLine]] | None:
    """이 집 최신 표준견적서 한 공정과 그 줄 (그 공정을 고르지 않았으면 None). 가견적 수량에 쓴다."""
    row = (await ensure_standard(s, p)).get(trade)
    if row is None:
        return None
    return row, (await _lines(s, [row.id]))[row.id]


async def standard_for(s: AsyncSession, user: User, project_id: uuid.UUID) -> StandardOut:
    """GET /projects/{id}/standard: 만든 사람 · 관리자만(남의 것은 404)."""
    p = await project_service.get_for(s, user, project_id)
    rows = await ensure_standard(s, p)
    order = [t for t in ("mokgong", "dobae") if t in rows]
    lines = await _lines(s, [rows[t].id for t in order])
    trades = [
        StandardTradeOut(
            trade=t,
            version=rows[t].version,
            grade=rows[t].grade,
            lines=[
                StandardLineOut(
                    key=ln.key,
                    name=ln.name,
                    qty=ln.qty,
                    unit=ln.unit,
                    unit_price=ln.unit_price,
                    amount=ln.amount,
                    state=ln.state,
                    source=ln.source,
                )
                for ln in lines[rows[t].id]
            ],
            supply=rows[t].supply,
            vat=rows[t].vat,
            total=rows[t].total,
            man_day=rows[t].man_day,
            crew=rows[t].crew,
            notes=list(rows[t].notes),
        )
        for t in order
    ]
    supply = sum(x.supply for x in trades)
    vat = sum(x.vat for x in trades)
    total = sum(x.total for x in trades)
    budget = None
    if p.budget:
        status, over = budget_gap(total, p.budget)
        budget = BudgetOut(band=p.budget, status=status, over_by=over)
    first = rows[order[0]]
    return StandardOut(
        project_id=p.id,
        code=p.code,
        project_rev=p.rev,
        formula_ver=first.formula_ver,
        price_ver=first.price_ver,
        computed_at=max(rows[t].created_at for t in order),
        area_m2=first.area_m2,
        area_basis=first.area_basis,
        pyeong=first.pyeong,
        trades=trades,
        supply=supply,
        vat=vat,
        total=total,
        budget=budget,
    )
