import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.auth.models import User
from app.db import get_session
from app.schedule import service
from app.schedule.schemas import PlanOut, PlanRedo
from app.shared.clock import today_seoul

router = APIRouter(tags=["공정표"])


@router.get(
    "/projects/{project_id}/plan",
    response_model=PlanOut,
    summary="공정표 (예상 공정표, 만든 사람 · 관리자)",
)
async def plan(
    project_id: uuid.UUID,
    user: User = Depends(current_user),
    s: AsyncSession = Depends(get_session),
) -> PlanOut:
    return await service.plan_for(s, user, project_id, today_seoul())


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
