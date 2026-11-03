import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.shared.ids import uuid7


class Plan(Base):
    """공정표 한 버전. expected(예상: 집 정보로 계산) · final(확정: 업체 날짜를 수락한 뒤)."""

    __tablename__ = "plans"
    __table_args__ = (
        UniqueConstraint("project_id", "kind", "version", name="uq_plans_version"),
        CheckConstraint("kind in ('expected', 'final')", name="kind"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint("end_date >= start_date", name="dates"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(10))
    version: Mapped[int] = mapped_column(SmallInteger)
    project_rev: Mapped[int] = mapped_column(SmallInteger)
    formula_ver: Mapped[str] = mapped_column(String(20))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    work_days: Mapped[int] = mapped_column(SmallInteger)
    weekend_work: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PlanStep(Base):
    """공정표 칸: 공정 작업 · 자연 건조 · 입주 청소"""

    __tablename__ = "plan_steps"
    __table_args__ = (
        CheckConstraint("kind in ('work', 'dry', 'clean')", name="kind"),
        CheckConstraint("trade is null or trade in ('dobae', 'mokgong')", name="trade"),
        CheckConstraint("(kind = 'work') = (trade is not null)", name="trade_kind"),
        CheckConstraint("end_date >= start_date and days >= 1", name="dates"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("plans.id", ondelete="CASCADE"), index=True
    )
    trade: Mapped[str | None] = mapped_column(String(10))
    kind: Mapped[str] = mapped_column(String(10))
    label: Mapped[str] = mapped_column(String(40))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    days: Mapped[int] = mapped_column(SmallInteger)
    crew: Mapped[int | None] = mapped_column(SmallInteger)
    man_day: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    sort: Mapped[int] = mapped_column(SmallInteger)


class PlanDay(Base):
    """공정표 날짜별 할 일(쉬는 날 포함)"""

    __tablename__ = "plan_days"
    __table_args__ = (CheckConstraint("kind in ('work', 'dry', 'clean', 'off')", name="kind"),)

    plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("plans.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    trade: Mapped[str | None] = mapped_column(String(10))
    kind: Mapped[str] = mapped_column(String(10))
    names: Mapped[list[str]] = mapped_column(ARRAY(String(60)), default=list, server_default="{}")
