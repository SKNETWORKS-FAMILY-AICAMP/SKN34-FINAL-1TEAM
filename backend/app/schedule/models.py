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
    Index,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
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
    # 확정 공정표 칸이 어느 업체 확정의 공사인지 (예상 공정표 · 건조 · 청소는 비움)
    deal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("deals.id", ondelete="SET NULL"))


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


# 업체 확정 뒤 공사 상태: 확정(날짜 기다림) · 날짜 수락 · 공사 끝냈어요(고객 확인 기다림) · 남은 일 · 끝남 / 다시 고르기
DEAL_STATUSES = ("confirmed", "scheduled", "checking", "fixing", "done", "reselected")
OPEN_DEALS = ("confirmed", "scheduled", "checking", "fixing")  # 업체 작업함 '공사' 탭
_DEAL_STATUS_SQL = "('confirmed', 'scheduled', 'checking', 'fixing', 'done', 'reselected')"


class Deal(Base):
    """업체 확정: 고객이 고른 업체와 확정한 견적서 버전. 공정마다 살아 있는 확정은 하나다.

    확정한 견적서 버전(quote_id)은 바꾸지 않는다. 날짜 칸은 공사 날짜를 수락한 뒤 채운다.
    """

    __tablename__ = "deals"
    __table_args__ = (
        Index(
            "uq_deals_trade",
            "project_id",
            "trade",
            unique=True,
            postgresql_where=text("status <> 'reselected'"),
        ),
        CheckConstraint("trade in ('dobae', 'mokgong')", name="trade"),
        CheckConstraint(f"status in {_DEAL_STATUS_SQL}", name="status"),
        CheckConstraint("date_request_count between 0 and 2", name="date_request_count"),
        CheckConstraint(
            "(start_date is null) = (end_date is null)"
            " and (start_date is null or end_date >= start_date)",
            name="dates",
        ),
        # 날짜를 수락한 뒤(scheduled부터)에는 공사 날짜가 있다
        CheckConstraint(
            "status in ('confirmed', 'reselected') or start_date is not null", name="scheduled"
        ),
        # 공사 끝냈어요를 올린 뒤(checking · fixing · done)에만 끝낸 날이 있다
        CheckConstraint(
            "(status in ('checking', 'fixing', 'done')) = (work_done_on is not null)",
            name="work_done_on",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    trade: Mapped[str] = mapped_column(String(10))
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("vendors.user_id", ondelete="SET NULL"), index=True
    )
    vendor_name: Mapped[str] = mapped_column(String(100))
    visit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("visits.id", ondelete="CASCADE"), unique=True
    )
    # 확정한 견적서 버전. 지우지 못하게 막는다(ON DELETE 없음)
    quote_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vendor_quotes.id"))
    status: Mapped[str] = mapped_column(String(10), default="confirmed", server_default="confirmed")
    dates_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # 공사 날짜 낼 기한
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    work_done_on: Mapped[date | None] = mapped_column(Date)
    date_request_count: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DealEvent(Base):
    """업체 확정 상태 이력 (만들 때 없음 → confirmed)"""

    __tablename__ = "deal_events"
    __table_args__ = (
        CheckConstraint(
            f"from_status is null or from_status in {_DEAL_STATUS_SQL}", name="from_status"
        ),
        CheckConstraint(f"to_status in {_DEAL_STATUS_SQL}", name="to_status"),
        CheckConstraint("note is null or length(btrim(note)) > 0", name="note"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    deal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("deals.id", ondelete="CASCADE"), index=True
    )
    from_status: Mapped[str | None] = mapped_column(String(10))
    to_status: Mapped[str] = mapped_column(String(10))
    by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    note: Mapped[str | None] = mapped_column(String(300))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
