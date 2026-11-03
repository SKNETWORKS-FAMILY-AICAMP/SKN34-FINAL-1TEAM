import uuid
from datetime import date, datetime, time

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    SmallInteger,
    String,
    Text,
    Time,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.shared.ids import uuid7

TRADES = ("dobae", "mokgong")
# 자주 묻는 5가지: 진행 순서 · 견적 계산 방식 · 추가금이 생기는 경우 · 공사 기간 · A/S · 환불
FAQ_KEYS = ("process", "pricing", "extra_cost", "duration", "as_refund")
LINKS_MAX = 5  # 시공 사례 링크
PORTFOLIO_MAX = 20  # 시공 사진


class Vendor(Base):
    """업체 회원. 가입 때는 사업자등록증만 받고, 관리자가 승인해야 업체 기능을 쓴다.

    상호 · 사업자번호 · 연락처 · 주소 · 사이트는 승인 뒤 업체 정보 관리에서 채운다.
    연락 방식 · 통화 시간 · 실측 비용은 고객에게 보이고, 휴대폰 · 채널 주소는 실측 요청 뒤에만 보인다.
    """

    __tablename__ = "vendors"
    __table_args__ = (
        CheckConstraint("status in ('pending', 'approved', 'rejected')", name="status"),
        CheckConstraint("biz_no is null or biz_no ~ '^[0-9]{10}$'", name="biz_no"),
        CheckConstraint(
            "(status = 'rejected') = (reject_reason is not null)", name="reject_reason"
        ),
        CheckConstraint(
            "reject_reason is null or length(btrim(reject_reason)) > 0", name="reject_reason_text"
        ),
        CheckConstraint("phone ~ '^01[0-9]{8,9}$'", name="phone"),
        CheckConstraint(
            "contact_method in ('phone', 'sms', 'kakao', 'web')", name="contact_method"
        ),
        # 카카오톡 채널 · 홈페이지 상담은 주소가 있어야 연락할 수 있다
        CheckConstraint(
            "contact_method not in ('kakao', 'web') or contact_url is not null", name="contact_url"
        ),
        CheckConstraint(
            "(call_start is null) = (call_end is null)"
            " and (call_start is null or call_start < call_end)",
            name="call_time",
        ),
        CheckConstraint("visit_fee >= 0", name="visit_fee"),
        CheckConstraint("not visit_fee_refund or visit_fee > 0", name="visit_fee_refund"),
        CheckConstraint("tax_kind in ('general', 'exempt')", name="tax_kind"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    biz_name: Mapped[str | None] = mapped_column(String(100))
    owner_name: Mapped[str | None] = mapped_column(String(50))
    biz_no: Mapped[str | None] = mapped_column(String(10), unique=True)
    phone: Mapped[str | None] = mapped_column(String(20))
    address: Mapped[str | None] = mapped_column(String(200))
    site_url: Mapped[str | None] = mapped_column(String(300))
    license_file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("files.id"))
    intro: Mapped[str] = mapped_column(Text, default="", server_default="")
    status: Mapped[str] = mapped_column(String(10), default="pending", server_default="pending")
    reject_reason: Mapped[str | None] = mapped_column(String(300))
    # 처리한 관리자 계정이 지워져도 승인 기록은 남긴다 (누가 했는지만 비움)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # 전화 · 문자 · 카카오톡 채널 · 홈페이지 상담. 채널 · 상담 주소는 kakao · web일 때만
    contact_method: Mapped[str | None] = mapped_column(String(5))
    contact_url: Mapped[str | None] = mapped_column(String(300))
    call_start: Mapped[time | None] = mapped_column(Time)
    call_end: Mapped[time | None] = mapped_column(Time)
    # 실측 출장비(원, 부가세 포함, 0 = 무료)와 '계약하면 빼 줘요'
    visit_fee: Mapped[int | None] = mapped_column(BigInteger)
    visit_fee_refund: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # 과세(general) · 면세(exempt)
    tax_kind: Mapped[str] = mapped_column(String(10), default="general", server_default="general")
    # 업체가 적는 짧은 글. 비면 고객 화면에 '확인 안 됨'으로 보인다
    interior_license: Mapped[str | None] = mapped_column(String(100))
    insurance: Mapped[str | None] = mapped_column(String(100))
    opened_on: Mapped[date | None] = mapped_column(Date)  # 개업일 (사업자 확인 때 채운다)
    # 업체 정보 마지막 저장. 비면 아직 저장 안 함 (PUT이 이 값으로 동시 저장을 막는다)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VendorTrade(Base):
    """업체가 하는 공정"""

    __tablename__ = "vendor_trades"
    __table_args__ = (CheckConstraint("trade in ('dobae', 'mokgong')", name="trade"),)

    vendor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vendors.user_id", ondelete="CASCADE"), primary_key=True
    )
    trade: Mapped[str] = mapped_column(String(10), primary_key=True)


class VendorArea(Base):
    """업체 시공 지역 (시 · 도 + 시 · 군 · 구)"""

    __tablename__ = "vendor_areas"

    vendor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vendors.user_id", ondelete="CASCADE"), primary_key=True
    )
    sido: Mapped[str] = mapped_column(String(20), primary_key=True)
    sgg: Mapped[str] = mapped_column(String(20), primary_key=True)


class VendorPortfolio(Base):
    """포트폴리오 사진 (선택). 공간 · 평형 · 시공 시기는 업체가 적는다."""

    __tablename__ = "vendor_portfolio"
    __table_args__ = (CheckConstraint("pyeong between 1 and 200", name="pyeong"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    vendor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vendors.user_id", ondelete="CASCADE"), index=True
    )
    file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("files.id"), unique=True)
    title: Mapped[str] = mapped_column(String(60))
    sort: Mapped[int] = mapped_column(SmallInteger, default=0)
    space: Mapped[str | None] = mapped_column(String(20))  # 거실 · 안방
    pyeong: Mapped[int | None] = mapped_column(SmallInteger)  # 아파트 평형(업체가 적은 값)
    taken_on: Mapped[date | None] = mapped_column(Date)  # 시공한 때 (화면에는 연 · 월만)


class VendorFaq(Base):
    """자주 묻는 5가지에 업체가 적은 답. 답이 없는 질문은 행이 없다."""

    __tablename__ = "vendor_faqs"
    __table_args__ = (
        CheckConstraint(
            "key in ('process', 'pricing', 'extra_cost', 'duration', 'as_refund')", name="key"
        ),
        CheckConstraint("length(btrim(answer)) > 0", name="answer"),
    )

    vendor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vendors.user_id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(12), primary_key=True)
    answer: Mapped[str] = mapped_column(String(200))


class VendorLink(Base):
    """시공 사례 링크. 5개까지(sort 0~4)."""

    __tablename__ = "vendor_links"
    __table_args__ = (CheckConstraint("sort between 0 and 4", name="sort"),)

    vendor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vendors.user_id", ondelete="CASCADE"), primary_key=True
    )
    sort: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    label: Mapped[str | None] = mapped_column(String(40))
    url: Mapped[str] = mapped_column(String(300))
