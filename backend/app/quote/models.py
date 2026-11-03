import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    ARRAY,
    BigInteger,
    CheckConstraint,
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

LINE_STATES = ("included", "bundled", "separate", "optional", "after_visit", "excluded")


class StandardQuote(Base):
    """표준견적서 한 공정 · 한 버전. 집 정보(rev) · 산식 · 단가 버전이 바뀌면 새 버전을 쌓는다."""

    __tablename__ = "standard_quotes"
    __table_args__ = (
        UniqueConstraint("project_id", "trade", "version", name="uq_standard_quotes_version"),
        CheckConstraint("trade in ('dobae', 'mokgong')", name="trade"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint("supply >= 0 and vat >= 0 and total = supply + vat", name="money"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    trade: Mapped[str] = mapped_column(String(10))
    version: Mapped[int] = mapped_column(SmallInteger)
    project_rev: Mapped[int] = mapped_column(SmallInteger)
    formula_ver: Mapped[str] = mapped_column(String(20))
    price_ver: Mapped[str] = mapped_column(String(20))
    grade: Mapped[str | None] = mapped_column(String(10))
    area_m2: Mapped[Decimal] = mapped_column(Numeric(7, 1))
    area_basis: Mapped[str] = mapped_column(String(10))
    pyeong: Mapped[Decimal] = mapped_column(Numeric(5, 1))
    supply: Mapped[int] = mapped_column(BigInteger)
    vat: Mapped[int] = mapped_column(BigInteger)
    total: Mapped[int] = mapped_column(BigInteger)
    man_day: Mapped[Decimal] = mapped_column(Numeric(4, 1))
    crew: Mapped[int] = mapped_column(SmallInteger)
    notes: Mapped[list[str]] = mapped_column(ARRAY(String(200)), default=list, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StandardQuoteLine(Base):
    """표준견적서 줄. 표준 행 키와 포함 상태 6종으로 남긴다(합계에는 포함 · 따로 계산만)."""

    __tablename__ = "standard_quote_lines"
    __table_args__ = (
        CheckConstraint(
            "state in ('included', 'bundled', 'separate', 'optional', 'after_visit', 'excluded')",
            name="state",
        ),
        CheckConstraint("basis in ('public', 'assumed', 'none')", name="basis"),
    )

    quote_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("standard_quotes.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(60))
    qty: Mapped[Decimal | None] = mapped_column(Numeric(9, 1))
    unit: Mapped[str] = mapped_column(String(10))
    unit_price: Mapped[int | None] = mapped_column(BigInteger)
    amount: Mapped[int | None] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(String(12))
    basis: Mapped[str] = mapped_column(String(10))
    source: Mapped[str] = mapped_column(String(120))
    sort: Mapped[int] = mapped_column(SmallInteger)
