import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from app.visit.models import LIVE, Visit


async def of_project(s: AsyncSession, project_id: uuid.UUID, trade: str | None) -> list[Visit]:
    """이 집 실측 요청 (최근 순, 닫힌 것 포함)"""
    q = select(Visit).where(Visit.project_id == project_id)
    if trade:
        q = q.where(Visit.trade == trade)
    q = q.order_by(Visit.created_at.desc(), Visit.id.desc())
    return list((await s.execute(q)).scalars().all())


async def live(s: AsyncSession, project_id: uuid.UUID, trade: str) -> list[Visit]:
    """칸을 차지하는 요청 (요청함 · 견적서 옴 · 확정)"""
    q = select(Visit).where(
        Visit.project_id == project_id, Visit.trade == trade, Visit.status.in_(LIVE)
    )
    return list((await s.execute(q)).scalars().all())


async def latest_by_vendor(
    s: AsyncSession, project_id: uuid.UUID, trade: str, vendor_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Visit]:
    """업체마다 이 집 · 이 공정의 가장 최근 요청"""
    q = (
        select(Visit)
        .where(
            Visit.project_id == project_id,
            Visit.trade == trade,
            Visit.vendor_id.in_(vendor_ids),
        )
        .order_by(Visit.vendor_id, Visit.created_at.desc(), Visit.id.desc())
        .ext(distinct_on(Visit.vendor_id))
    )
    return {v.vendor_id: v for v in (await s.execute(q)).scalars()}
